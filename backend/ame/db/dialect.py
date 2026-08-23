from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


def dialect_name(session: AsyncSession) -> str:
    bind = session.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", "") or "postgresql"


def is_sqlite(session: AsyncSession) -> bool:
    return dialect_name(session) == "sqlite"


def upsert_insert(table, session: AsyncSession):
    if is_sqlite(session):
        from sqlalchemy.dialects.sqlite import insert

        return insert(table)
    from sqlalchemy.dialects.postgresql import insert

    return insert(table)
