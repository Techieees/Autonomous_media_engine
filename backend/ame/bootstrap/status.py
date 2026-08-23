from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.bootstrap.instructions import OWNER_ACTION_CATEGORIES, SPECS_BY_KEY
from ame.config import Settings, get_settings
from ame.contracts.enums import ConnectionState, HumanActionStatus, Platform, PublishStatus
from ame.db.models import HumanAction, PlatformConnection
from ame.security.secrets import decrypt_secret

PRODUCTION_PLATFORMS = (Platform.YOUTUBE, Platform.INSTAGRAM, Platform.TIKTOK)


class ConnectionStatus(BaseModel):
    platform: str
    state: ConnectionState
    account_label: str | None = None
    scopes: list[Any] = Field(default_factory=list)
    expires_at: datetime | None = None
    app_configured: bool
    token_present: bool
    refresh_present: bool
    open_action_titles: list[str] = Field(default_factory=list)
    authorize_available: bool
    publish_gate: str
    simulation_only: bool


def credentials_configured(platform: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if platform == Platform.YOUTUBE.value:
        return bool(settings.youtube_client_id and settings.youtube_client_secret)
    if platform == Platform.INSTAGRAM.value:
        return bool(settings.meta_app_id and settings.meta_app_secret)
    if platform == Platform.TIKTOK.value:
        return bool(settings.tiktok_client_key and settings.tiktok_client_secret)
    return False


def token_blob_usable(value: str | None) -> bool:
    if not value:
        return False
    try:
        decrypt_secret(value)
    except ValueError:
        return False
    return True


def publish_gate_for(state: ConnectionState) -> str:
    if state == ConnectionState.READY:
        return "allowed"
    if state == ConnectionState.NEEDS_PLATFORM_REVIEW:
        return PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL.value
    if state in {ConnectionState.REQUIRES_HUMAN_ACTION, ConnectionState.CONNECTED}:
        return PublishStatus.REQUIRES_HUMAN_ACTION.value
    return PublishStatus.CONNECTION_REQUIRED.value


def _open_titles_for(platform: str, actions: Sequence[HumanAction]) -> list[str]:
    titles: list[str] = []
    for action in actions:
        if action.status != HumanActionStatus.OPEN.value:
            continue
        if action.category not in OWNER_ACTION_CATEGORIES:
            continue
        if action.platform == platform:
            titles.append(action.title)
    return titles


def _token_needs_reauth(connection: PlatformConnection, now: datetime) -> bool:
    if not token_blob_usable(connection.token_encrypted):
        return True
    if connection.expires_at is None:
        return False
    expires = connection.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires > now:
        return False
    return not token_blob_usable(connection.refresh_encrypted)


def resolve_connection_status(
    connection: PlatformConnection | None,
    *,
    platform: str,
    settings: Settings | None = None,
    open_actions: Sequence[HumanAction] | None = None,
    now: datetime | None = None,
) -> ConnectionStatus:
    settings = settings or get_settings()
    moment = now or datetime.now(UTC)
    actions = open_actions or []
    titles = _open_titles_for(platform, actions)
    configured = credentials_configured(platform, settings)
    token_present = bool(connection and token_blob_usable(connection.token_encrypted))
    refresh_present = bool(connection and token_blob_usable(connection.refresh_encrypted))

    if connection is None or not token_present:
        state = (
            ConnectionState.CONNECTION_REQUIRED
            if configured
            else ConnectionState.NOT_CONFIGURED
        )
    elif _token_needs_reauth(connection, moment):
        state = ConnectionState.NEEDS_REAUTHORIZATION
    else:
        state = _connected_state(platform, titles, refresh_present)

    return ConnectionStatus(
        platform=platform,
        state=state,
        account_label=connection.account_label if connection else None,
        scopes=list(connection.scopes or []) if connection else [],
        expires_at=connection.expires_at if connection else None,
        app_configured=configured,
        token_present=token_present,
        refresh_present=refresh_present,
        open_action_titles=titles,
        authorize_available=configured,
        publish_gate=publish_gate_for(state),
        simulation_only=state != ConnectionState.READY,
    )


def _connected_state(platform: str, titles: list[str], refresh_present: bool) -> ConnectionState:
    tiktok_review = SPECS_BY_KEY["tiktok.app_review"].title
    instagram_pro = SPECS_BY_KEY["instagram.professional_conversion"].title
    if platform == Platform.TIKTOK.value and tiktok_review in titles:
        return ConnectionState.NEEDS_PLATFORM_REVIEW
    if platform == Platform.INSTAGRAM.value and instagram_pro in titles:
        return ConnectionState.REQUIRES_HUMAN_ACTION
    if platform == Platform.YOUTUBE.value and not refresh_present:
        return ConnectionState.CONNECTED
    if platform == Platform.YOUTUBE.value:
        return ConnectionState.READY
    if platform == Platform.INSTAGRAM.value:
        return ConnectionState.READY
    if platform == Platform.TIKTOK.value:
        return ConnectionState.READY
    return ConnectionState.CONNECTED


async def load_owner_actions(session: AsyncSession) -> list[HumanAction]:
    result = await session.execute(
        select(HumanAction).where(HumanAction.category.in_(OWNER_ACTION_CATEGORIES))
    )
    return list(result.scalars())


async def resolve_all_connection_statuses(session: AsyncSession) -> list[ConnectionStatus]:
    settings = get_settings()
    actions = await load_owner_actions(session)
    result = await session.execute(
        select(PlatformConnection).where(
            PlatformConnection.platform.in_([item.value for item in PRODUCTION_PLATFORMS])
        )
    )
    by_platform = {row.platform: row for row in result.scalars()}
    now = datetime.now(UTC)
    return [
        resolve_connection_status(
            by_platform.get(platform.value),
            platform=platform.value,
            settings=settings,
            open_actions=actions,
            now=now,
        )
        for platform in PRODUCTION_PLATFORMS
    ]


async def sync_connection_states(session: AsyncSession) -> list[ConnectionStatus]:
    statuses = await resolve_all_connection_statuses(session)
    result = await session.execute(
        select(PlatformConnection).where(
            PlatformConnection.platform.in_([item.platform for item in statuses])
        )
    )
    by_platform = {row.platform: row for row in result.scalars()}
    changed = False
    for status in statuses:
        connection = by_platform.get(status.platform)
        if connection is None:
            continue
        if connection.state != status.state.value:
            connection.state = status.state.value
            changed = True
    if changed:
        await session.flush()
    return statuses
