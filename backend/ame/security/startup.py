"""Fail-closed production checks. Never log secret material."""

from __future__ import annotations

import os
import re

from ame.config import DEFAULT_SECRET_KEY, get_settings
from ame.security.secrets import CredentialKeyMissing, is_production, resolve_credential_key

_WEAK_SECRET_KEYS = frozenset(
    {
        "",
        DEFAULT_SECRET_KEY,
        "changeme",
        "change-me",
        "secret",
        "password",
        "admin",
        "ame",
        "development",
        "test",
        "local",
        "123456",
        "secret_key",
    }
)
_FORCE_SQLITE_TRUTHY = frozenset({"1", "true", "yes", "on"})


class ProductionConfigError(RuntimeError):
    """Production configuration is unsafe."""


def app_env_name() -> str:
    return (get_settings().app_env or "").strip().lower()


def sqlite_fallback_allowed() -> bool:
    env = app_env_name()
    if env in {"production", "prod"}:
        return False
    return env in {"development", "dev", "test"}


def force_sqlite_requested() -> bool:
    return os.getenv("AME_FORCE_SQLITE", "").strip().lower() in _FORCE_SQLITE_TRUTHY


def validate_secret_key() -> None:
    if not is_production():
        return
    key = (get_settings().secret_key or "").strip()
    if not key:
        raise ProductionConfigError("SECRET_KEY is required in production")
    if key.lower() in _WEAK_SECRET_KEYS or key == DEFAULT_SECRET_KEY:
        raise ProductionConfigError("SECRET_KEY is the development default or too weak")
    if len(key) < 32:
        raise ProductionConfigError("SECRET_KEY is too short for production")
    if len(set(key)) == 1:
        raise ProductionConfigError("SECRET_KEY is too weak")
    if re.fullmatch(r"(.)\1+", key):
        raise ProductionConfigError("SECRET_KEY is too weak")


def validate_credential_kek() -> None:
    if not is_production():
        return
    try:
        resolve_credential_key()
    except CredentialKeyMissing as exc:
        raise ProductionConfigError("AME_CREDENTIAL_KEK is required in production") from exc


def _url_is_sqlite(url: str) -> bool:
    scheme = (url or "").split(":", 1)[0].lower()
    return "sqlite" in scheme


def validate_sqlite_policy() -> None:
    if not is_production():
        return
    if force_sqlite_requested():
        raise ProductionConfigError("AME_FORCE_SQLITE is not allowed in production")
    settings = get_settings()
    if _url_is_sqlite(settings.database_url) or _url_is_sqlite(settings.database_url_sync):
        raise ProductionConfigError("SQLite database URLs are not allowed in production")


def validate_runtime_security() -> None:
    validate_secret_key()
    validate_credential_kek()
    validate_sqlite_policy()
