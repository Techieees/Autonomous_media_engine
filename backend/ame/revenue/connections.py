from __future__ import annotations

import importlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import ConnectionState
from ame.db.models import PlatformConnection

_CONNECTED = {
    ConnectionState.CONNECTED.value,
    ConnectionState.READY.value,
}


async def list_connections(session: AsyncSession) -> list[PlatformConnection]:
    result = await session.execute(select(PlatformConnection))
    return list(result.scalars().all())


def is_connected(connection: PlatformConnection) -> bool:
    return connection.state in _CONNECTED and bool(connection.token_encrypted)


async def connected_platforms(session: AsyncSession) -> list[PlatformConnection]:
    return [item for item in await list_connections(session) if is_connected(item)]


def resolve_adapter(platform: str) -> Any | None:
    try:
        registry = importlib.import_module("ame.publishers.registry")
        getter = getattr(registry, "get_adapter", None)
        if callable(getter):
            return getter(platform)
    except Exception:  # noqa: BLE001
        pass
    try:
        module = importlib.import_module(f"ame.publishers.{platform}")
        for attr in ("get_adapter", "adapter", "ADAPTER"):
            candidate = getattr(module, attr, None)
            if callable(candidate):
                return candidate()
            if candidate is not None:
                return candidate
    except Exception:  # noqa: BLE001
        return None
    return None
