from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from ame.config import get_settings
from ame.observability import get_logger
from ame.security.startup import (
    force_sqlite_requested,
    sqlite_fallback_allowed,
    validate_sqlite_policy,
)

logger = get_logger("ame.db.runtime")

_STATE: dict[str, object] = {}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").is_file() or (parent / "docker-compose.yml").is_file():
            return parent
    return Path.cwd()


def sqlite_paths() -> tuple[str, str]:
    if not sqlite_fallback_allowed():
        raise RuntimeError("SQLite fallback is disabled outside development/test")
    root = _repo_root()
    db_path = (root / "data" / "ame.dev.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    posix = db_path.as_posix()
    return f"sqlite+aiosqlite:///{posix}", f"sqlite:///{posix}"


def postgres_reachable(sync_url: str) -> bool:
    if force_sqlite_requested():
        if not sqlite_fallback_allowed():
            raise RuntimeError("AME_FORCE_SQLITE is not allowed in production")
        return False
    try:
        engine = create_engine(
            sync_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def resolve_database_urls() -> tuple[str, str, str]:
    settings = get_settings()
    validate_sqlite_policy()
    if force_sqlite_requested() and not sqlite_fallback_allowed():
        raise RuntimeError("AME_FORCE_SQLITE is not allowed in production")
    if postgres_reachable(settings.database_url_sync):
        logger.info("database_backend", backend="postgresql")
        return settings.database_url, settings.database_url_sync, "postgresql"
    if not sqlite_fallback_allowed():
        raise RuntimeError(
            "PostgreSQL is unreachable; SQLite fallback is disabled in production"
        )
    async_url, sync_url = sqlite_paths()
    logger.warning(
        "database_fallback",
        backend="sqlite",
        reason="postgres_unavailable",
        path=sync_url,
    )
    return async_url, sync_url, "sqlite"


def reset_database_runtime() -> None:
    """Drop cached engines so tests can switch APP_ENV / URLs."""
    engine = _STATE.pop("async_engine", None)
    sync_engine = _STATE.pop("sync_engine", None)
    _STATE.clear()
    if engine is not None:
        try:
            engine.sync_engine.dispose()  # type: ignore[attr-defined]
        except Exception:
            pass
    if sync_engine is not None:
        try:
            sync_engine.dispose()  # type: ignore[union-attr]
        except Exception:
            pass


def get_async_engine() -> AsyncEngine:
    engine = _STATE.get("async_engine")
    if engine is None:
        async_url, sync_url, backend = resolve_database_urls()
        async_connect = {"timeout": 30} if backend == "sqlite" else {}
        sync_connect = (
            {"check_same_thread": False, "timeout": 30} if backend == "sqlite" else {}
        )
        engine = create_async_engine(async_url, pool_pre_ping=True, connect_args=async_connect)
        sync_engine = create_engine(sync_url, pool_pre_ping=True, connect_args=sync_connect)
        if backend == "sqlite":
            _apply_sqlite_pragmas(sync_engine)
        _STATE["async_engine"] = engine
        _STATE["sync_engine"] = sync_engine
        _STATE["backend"] = backend
        _STATE["async_session"] = async_sessionmaker(engine, expire_on_commit=False)
        _STATE["sync_session"] = sessionmaker(sync_engine, expire_on_commit=False)
    return engine  # type: ignore[return-value]


def _apply_sqlite_pragmas(engine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_sync_engine():
    get_async_engine()
    return _STATE["sync_engine"]


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    get_async_engine()
    return _STATE["async_session"]  # type: ignore[return-value]


def sync_session_factory() -> sessionmaker[Session]:
    get_async_engine()
    return _STATE["sync_session"]  # type: ignore[return-value]


def database_backend() -> str:
    get_async_engine()
    return str(_STATE.get("backend") or "postgresql")


def ensure_schema() -> None:
    from ame.db.base import Base
    from ame.db import models  # noqa: F401

    engine = get_sync_engine()
    Base.metadata.create_all(engine)
    _evolve_schema(engine)
    logger.info("schema_ready", backend=database_backend())


def _evolve_schema(engine) -> None:
    statements = (
        "ALTER TABLE human_actions ADD COLUMN classification VARCHAR(40) DEFAULT 'genuinely_human_required'",
        "ALTER TABLE human_actions ADD COLUMN checkpoint_kind VARCHAR(80)",
        "ALTER TABLE human_actions ADD COLUMN details JSON",
        "ALTER TABLE agent_messages ADD COLUMN task_id CHAR(36)",
        "ALTER TABLE agent_messages ADD COLUMN related_entity_type VARCHAR(80)",
        "ALTER TABLE agent_messages ADD COLUMN related_entity_id CHAR(36)",
        "ALTER TABLE agent_messages ADD COLUMN confidence FLOAT",
    )
    with engine.begin() as conn:
        for statement in statements:
            try:
                conn.exec_driver_sql(statement)
            except Exception:
                continue
