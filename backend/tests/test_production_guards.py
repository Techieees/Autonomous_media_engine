"""Production must not silently use SQLite or a development SECRET_KEY."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from ame.config import DEFAULT_SECRET_KEY, get_settings
from ame.db.runtime import reset_database_runtime, resolve_database_urls, sqlite_paths
from ame.security.startup import ProductionConfigError, validate_runtime_security, validate_secret_key


PROD_SECRET = "production-secret-key-value-32ok"
PROD_KEK = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")


def _production(monkeypatch, *, secret: str | None = PROD_SECRET, kek: str | None = PROD_KEK) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    if secret is None:
        monkeypatch.setenv("SECRET_KEY", "")
    else:
        monkeypatch.setenv("SECRET_KEY", secret)
    if kek is None:
        monkeypatch.delenv("AME_CREDENTIAL_KEK", raising=False)
    else:
        monkeypatch.setenv("AME_CREDENTIAL_KEK", kek)
    monkeypatch.delenv("AME_FORCE_SQLITE", raising=False)
    get_settings.cache_clear()
    reset_database_runtime()


def test_production_rejects_default_secret_key(monkeypatch) -> None:
    _production(monkeypatch, secret=DEFAULT_SECRET_KEY)
    with pytest.raises(ProductionConfigError, match="SECRET_KEY"):
        validate_secret_key()


def test_production_rejects_missing_secret_key(monkeypatch) -> None:
    _production(monkeypatch, secret="")
    with pytest.raises(ProductionConfigError, match="SECRET_KEY"):
        validate_secret_key()


def test_production_rejects_short_secret_key(monkeypatch) -> None:
    _production(monkeypatch, secret="short-key")
    with pytest.raises(ProductionConfigError, match="SECRET_KEY"):
        validate_secret_key()


def test_production_rejects_force_sqlite(monkeypatch) -> None:
    _production(monkeypatch)
    monkeypatch.setenv("AME_FORCE_SQLITE", "1")
    get_settings.cache_clear()
    with pytest.raises((RuntimeError, ProductionConfigError), match="AME_FORCE_SQLITE"):
        validate_runtime_security()
        resolve_database_urls()


def test_production_unreachable_postgres_does_not_use_sqlite(monkeypatch, tmp_path: Path) -> None:
    _production(monkeypatch)
    monkeypatch.setattr("ame.db.runtime.postgres_reachable", lambda _url: False)

    def _forbidden() -> tuple[str, str]:
        raise AssertionError("sqlite_paths must not run in production")

    monkeypatch.setattr("ame.db.runtime.sqlite_paths", _forbidden)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        resolve_database_urls()
    assert not (tmp_path / "ame.dev.db").exists()


def test_development_may_use_sqlite_fallback(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AME_FORCE_SQLITE", "1")
    get_settings.cache_clear()
    reset_database_runtime()
    async_url, sync_url, backend = resolve_database_urls()
    assert backend == "sqlite"
    assert "ame.dev.db" in async_url
    assert "ame.dev.db" in sync_url


def test_production_rejects_sqlite_database_url(monkeypatch) -> None:
    _production(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///C:/tmp/ame.db")
    monkeypatch.setenv("DATABASE_URL_SYNC", "sqlite:///C:/tmp/ame.db")
    get_settings.cache_clear()
    with pytest.raises((RuntimeError, ProductionConfigError), match="SQLite"):
        validate_runtime_security()


def test_production_sqlite_paths_rejected(monkeypatch) -> None:
    _production(monkeypatch)
    with pytest.raises(RuntimeError, match="SQLite fallback is disabled"):
        sqlite_paths()
