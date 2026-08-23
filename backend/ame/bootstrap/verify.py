"""Verify external account/app/OAuth state. Never fabricate success."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ame.bootstrap.boundary import read_boundary
from ame.bootstrap.status import credentials_configured, token_blob_usable
from ame.config import get_settings
from ame.contracts.enums import Platform
from ame.db.models import AccountBootstrap, PlatformConnection
from ame.observability import get_logger
from ame.security.secrets import decrypt_secret

logger = get_logger("ame.bootstrap.verify")


@dataclass
class ExternalProof:
    verified: bool
    can_verify: bool
    kind: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


async def verify_account(session: AsyncSession, row: AccountBootstrap) -> ExternalProof:
    if get_settings().bootstrap_simulation:
        boundary = read_boundary(row)
        exists = bool(boundary.get("account_exists"))
        return ExternalProof(
            verified=exists,
            can_verify=True,
            kind="account",
            reason="simulated account exists" if exists else "simulated account not confirmed",
            evidence={"account_id": boundary.get("account_id")},
        )
    if row.platform == Platform.YOUTUBE.value:
        return await _youtube_channel(session)
    if row.platform == Platform.INSTAGRAM.value:
        return await _instagram_user(session)
    return await _tiktok_user(session)


async def verify_developer_app(session: AsyncSession, row: AccountBootstrap) -> ExternalProof:
    _ = session
    if get_settings().bootstrap_simulation:
        exists = bool(read_boundary(row).get("developer_app_exists"))
        return ExternalProof(
            verified=exists,
            can_verify=True,
            kind="developer_app",
            reason="simulated developer app exists" if exists else "simulated developer app not confirmed",
        )
    if credentials_configured(row.platform):
        return ExternalProof(
            verified=True,
            can_verify=True,
            kind="developer_app",
            reason="official client credentials are present in environment",
        )
    return ExternalProof(
        verified=False,
        can_verify=False,
        kind="developer_app",
        reason="developer credentials are not present; portal confirmation is required",
    )


async def verify_oauth(session: AsyncSession, row: AccountBootstrap) -> ExternalProof:
    if get_settings().bootstrap_simulation:
        ok = bool(read_boundary(row).get("oauth_authenticated"))
        return ExternalProof(
            verified=ok,
            can_verify=True,
            kind="oauth",
            reason="simulated oauth confirmed" if ok else "simulated oauth not confirmed",
        )
    connection = await _connection(session, row.platform)
    if connection is None or not token_blob_usable(connection.token_encrypted):
        return ExternalProof(
            verified=False,
            can_verify=False,
            kind="oauth",
            reason="no authenticated token to verify",
        )
    if row.platform == Platform.YOUTUBE.value:
        channel = await _youtube_channel(session)
        if channel.verified:
            return channel
        return ExternalProof(
            verified=False,
            can_verify=channel.can_verify,
            kind="oauth",
            reason=channel.reason,
            evidence=channel.evidence,
        )
    return ExternalProof(
        verified=True,
        can_verify=True,
        kind="oauth",
        reason="access token present after official callback",
        evidence={"account_label": connection.account_label},
    )


async def _connection(session: AsyncSession, platform: str) -> PlatformConnection | None:
    from sqlalchemy import select

    return (
        await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    ).scalar_one_or_none()


async def _access_token(session: AsyncSession, platform: str) -> str | None:
    connection = await _connection(session, platform)
    if connection is None or not connection.token_encrypted:
        return None
    try:
        return decrypt_secret(connection.token_encrypted)
    except ValueError:
        return None


async def _youtube_channel(session: AsyncSession) -> ExternalProof:
    token = await _access_token(session, Platform.YOUTUBE.value)
    if not token:
        return ExternalProof(
            verified=False,
            can_verify=False,
            kind="channel",
            reason="YouTube channel cannot be verified without an official OAuth token",
        )
    try:
        from ame.oauth.http import get_json
        from ame.oauth.youtube import CHANNEL_URL

        payload = await get_json(
            CHANNEL_URL,
            platform=Platform.YOUTUBE.value,
            headers={"Authorization": f"Bearer {token}"},
            params={"part": "id,snippet", "mine": "true"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("youtube_channel_verify_failed", error=type(exc).__name__)
        return ExternalProof(
            verified=False,
            can_verify=True,
            kind="channel",
            reason="YouTube Data API did not confirm a channel",
        )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return ExternalProof(
            verified=False,
            can_verify=True,
            kind="channel",
            reason="authenticated Google account has no YouTube channel",
        )
    first = items[0] if isinstance(items[0], dict) else {}
    snippet = first.get("snippet") if isinstance(first.get("snippet"), dict) else {}
    return ExternalProof(
        verified=True,
        can_verify=True,
        kind="channel",
        reason="YouTube channel exists",
        evidence={"channel_id": first.get("id"), "title": snippet.get("title")},
    )


async def _instagram_user(session: AsyncSession) -> ExternalProof:
    token = await _access_token(session, Platform.INSTAGRAM.value)
    if not token:
        return ExternalProof(
            verified=False,
            can_verify=False,
            kind="account",
            reason="Instagram professional account cannot be verified without Meta OAuth",
        )
    try:
        from ame.config import get_settings
        from ame.oauth.http import get_json

        version = get_settings().instagram_graph_version
        payload = await get_json(
            f"https://graph.facebook.com/{version}/me",
            platform=Platform.INSTAGRAM.value,
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "id,name"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("instagram_verify_failed", error=type(exc).__name__)
        return ExternalProof(
            verified=False,
            can_verify=True,
            kind="account",
            reason="Meta Graph did not confirm an Instagram professional user",
        )
    user_id = payload.get("id") if isinstance(payload, dict) else None
    if not user_id:
        return ExternalProof(
            verified=False,
            can_verify=True,
            kind="account",
            reason="Meta Graph response had no user id",
        )
    return ExternalProof(
        verified=True,
        can_verify=True,
        kind="account",
        reason="Instagram professional account exists",
        evidence={"ig_user_id": user_id, "name": payload.get("name")},
    )


async def _tiktok_user(session: AsyncSession) -> ExternalProof:
    token = await _access_token(session, Platform.TIKTOK.value)
    if not token:
        return ExternalProof(
            verified=False,
            can_verify=False,
            kind="account",
            reason="TikTok account cannot be verified without official OAuth",
        )
    try:
        from ame.oauth.http import get_json

        payload = await get_json(
            "https://open.tiktokapis.com/v2/user/info/",
            platform=Platform.TIKTOK.value,
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "open_id,display_name"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("tiktok_verify_failed", error=type(exc).__name__)
        return ExternalProof(
            verified=False,
            can_verify=True,
            kind="account",
            reason="TikTok API did not confirm an authenticated user",
        )
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("user") if isinstance(data, dict) else None
    open_id = user.get("open_id") if isinstance(user, dict) else None
    if not open_id:
        return ExternalProof(
            verified=False,
            can_verify=True,
            kind="account",
            reason="TikTok response had no open_id",
        )
    return ExternalProof(
        verified=True,
        can_verify=True,
        kind="account",
        reason="TikTok account exists",
        evidence={"open_id": open_id, "display_name": user.get("display_name")},
    )
