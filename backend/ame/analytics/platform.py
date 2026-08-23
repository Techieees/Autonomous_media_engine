from __future__ import annotations

import importlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.normalize import NormalizedMetrics, normalize_platform_metrics, unavailable_metrics
from ame.contracts.enums import ConnectionState
from ame.db.models import PlatformConnection, Publication
from ame.observability import get_logger

logger = get_logger("ame.analytics.platform")

_CONNECTED = {
    ConnectionState.CONNECTED.value,
    ConnectionState.READY.value,
}


async def load_connection(session: AsyncSession, platform: str) -> PlatformConnection | None:
    result = await session.execute(
        select(PlatformConnection).where(PlatformConnection.platform == platform)
    )
    return result.scalar_one_or_none()


def connection_is_ready(connection: PlatformConnection | None) -> bool:
    if connection is None:
        return False
    return connection.state in _CONNECTED and bool(connection.token_encrypted)


def _resolve_adapter(platform: str) -> Any | None:
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


async def fetch_real_metrics(
    session: AsyncSession, publication: Publication
) -> NormalizedMetrics:
    """Official adapter metrics only. Never invent views or CPM."""
    if publication.simulation:
        return unavailable_metrics(
            reason="publication_is_simulation", platform=publication.platform
        )
    connection = await load_connection(session, publication.platform)
    if not connection_is_ready(connection):
        return unavailable_metrics(
            reason="platform_not_connected", platform=publication.platform
        )
    adapter = _resolve_adapter(publication.platform)
    if adapter is None:
        return unavailable_metrics(
            reason="publisher_adapter_missing", platform=publication.platform
        )
    fetcher = getattr(adapter, "fetch_metrics", None)
    if not callable(fetcher):
        return unavailable_metrics(
            reason="adapter_has_no_fetch_metrics", platform=publication.platform
        )
    try:
        raw = await fetcher(publication)
    except Exception:  # noqa: BLE001
        logger.exception(
            "platform_metrics_fetch_failed",
            platform=publication.platform,
            publication_id=str(publication.id),
        )
        return unavailable_metrics(
            reason="platform_metrics_fetch_failed", platform=publication.platform
        )
    if not raw:
        return unavailable_metrics(
            reason="platform_returned_no_metrics", platform=publication.platform
        )
    return normalize_platform_metrics(dict(raw), platform=publication.platform)
