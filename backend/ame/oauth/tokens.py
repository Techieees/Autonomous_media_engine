from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import ConnectionState, HumanActionStatus, Platform
from ame.db.models import HumanAction, PlatformConnection
from ame.observability import get_logger
from ame.security.secrets import encrypt_secret

logger = get_logger("ame.oauth.tokens")


def unwrap_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("access_token"), str):
        return payload
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("access_token"), str):
        return data
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("access_token"), str):
            return first
    return payload


def read_access_token(payload: dict[str, Any]) -> str | None:
    unwrapped = unwrap_token_payload(payload)
    token = unwrapped.get("access_token")
    return token if isinstance(token, str) and token else None


def read_refresh_token(payload: dict[str, Any]) -> str | None:
    unwrapped = unwrap_token_payload(payload)
    token = unwrapped.get("refresh_token")
    return token if isinstance(token, str) and token else None


def read_expires_in(payload: dict[str, Any], default: int) -> int:
    unwrapped = unwrap_token_payload(payload)
    raw = unwrapped.get("expires_in")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def read_scope_list(payload: dict[str, Any], fallback: list[str]) -> list[str]:
    unwrapped = unwrap_token_payload(payload)
    raw = unwrapped.get("scope") or unwrapped.get("permissions")
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    if isinstance(raw, str) and raw.strip():
        separator = "," if "," in raw else " "
        return [item for item in raw.split(separator) if item]
    return list(fallback)


def expiry_at(expires_in: int) -> datetime:
    skew = 60 if expires_in > 120 else 0
    return datetime.now(UTC) + timedelta(seconds=max(expires_in - skew, 1))


async def load_connection(session: AsyncSession, platform: Platform | str) -> PlatformConnection:
    value = platform.value if isinstance(platform, Platform) else platform
    result = await session.execute(
        select(PlatformConnection).where(PlatformConnection.platform == value)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = PlatformConnection(
            platform=value,
            state=ConnectionState.CONNECTION_REQUIRED.value,
            scopes=[],
            metadata_json={},
        )
        session.add(connection)
        await session.flush()
    return connection


async def persist_tokens(
    session: AsyncSession,
    platform: Platform | str,
    *,
    access_token: str,
    refresh_token: str | None,
    expires_in: int,
    scopes: list[str],
    state: ConnectionState,
    account_label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PlatformConnection:
    connection = await load_connection(session, platform)
    existing_meta = dict(connection.metadata_json or {})
    existing_meta.pop("access_token", None)
    existing_meta.pop("refresh_token", None)
    existing_meta.pop("token", None)
    extra = dict(metadata or {})
    extra.pop("access_token", None)
    extra.pop("refresh_token", None)
    extra.pop("token", None)
    connection.token_encrypted = encrypt_secret(access_token)
    connection.refresh_encrypted = encrypt_secret(refresh_token) if refresh_token else None
    connection.expires_at = expiry_at(expires_in)
    connection.scopes = scopes
    connection.state = state.value
    if account_label:
        connection.account_label = account_label
    connection.metadata_json = {
        **existing_meta,
        **extra,
        "connected_at": datetime.now(UTC).isoformat(),
    }
    await session.flush()
    logger.info(
        "oauth_tokens_persisted",
        platform=connection.platform,
        state=connection.state,
        has_refresh=bool(refresh_token),
        scope_count=len(scopes),
    )
    try:
        from ame.bootstrap.orchestrator import on_oauth_tokens_persisted

        await on_oauth_tokens_persisted(session, connection.platform)
    except Exception:  # noqa: BLE001
        logger.exception("bootstrap_oauth_resume_failed", platform=connection.platform)
    return connection


async def mark_checklist_completed(session: AsyncSession, titles: list[str]) -> int:
    if not titles:
        return 0
    result = await session.execute(
        select(HumanAction).where(
            HumanAction.title.in_(titles),
            HumanAction.status == HumanActionStatus.OPEN.value,
            HumanAction.category != "oauth_state",
        )
    )
    updated = 0
    for action in result.scalars():
        action.status = HumanActionStatus.COMPLETED.value
        updated += 1
    if updated:
        await session.flush()
        logger.info("oauth_checklist_completed", count=updated)
    return updated
