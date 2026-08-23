from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from ame.db.runtime import (
    async_session_factory as _async_factory,
    database_backend,
    ensure_schema,
    get_async_engine,
    get_sync_engine,
    sync_session_factory as _sync_factory,
)


class _LazyAsyncSession:
    def __call__(self, **kwargs):
        return _async_factory()(**kwargs)


class _LazySyncSession:
    def __call__(self, **kwargs):
        return _sync_factory()(**kwargs)


async_session_factory = _LazyAsyncSession()
sync_session_factory = _LazySyncSession()
async_engine = None  # resolved lazily via get_async_engine
sync_engine = None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _async_factory()() as session:
        yield session


def init_database() -> None:
    from ame.security.startup import validate_runtime_security

    validate_runtime_security()
    get_async_engine()
    get_sync_engine()
    ensure_schema()
    _ = database_backend()
