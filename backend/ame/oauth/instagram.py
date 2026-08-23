from __future__ import annotations

from urllib.parse import urlencode

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ame.bootstrap.instructions import SPECS_BY_KEY
from ame.bootstrap.status import sync_connection_states
from ame.config import Settings, get_settings
from ame.contracts.enums import ConnectionState, Platform
from ame.db.models import PlatformConnection
from ame.oauth.errors import OAuthExchangeError, OAuthNotConfiguredError, OAuthStateError
from ame.oauth.http import get_json, post_form
from ame.oauth.state import create_oauth_state, verify_oauth_state
from ame.oauth.tokens import (
    mark_checklist_completed,
    persist_tokens,
    read_access_token,
    read_expires_in,
    read_scope_list,
    unwrap_token_payload,
)
from ame.observability import get_logger
from ame.security.csrf import bind_oauth_csrf_cookie, verify_oauth_csrf_cookie

logger = get_logger("ame.oauth.instagram")

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SHORT_LIVED_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
ME_URL = "https://graph.instagram.com/me"

INSTAGRAM_SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
)

INSTAGRAM_OAUTH_TITLES = (
    SPECS_BY_KEY["instagram.dedicated_account"].title,
    SPECS_BY_KEY["instagram.oauth"].title,
)


class AuthorizeRequest(BaseModel):
    platform: str = Platform.INSTAGRAM.value
    url: str
    state: str
    csrf_cookie: str
    expires_in: int = 600


def credentials_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.meta_app_id and settings.meta_app_secret)


def authorize_url(state: str, *, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.INSTAGRAM.value)
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_redirect_uri,
        "response_type": "code",
        "scope": ",".join(INSTAGRAM_SCOPES),
        "state": state,
        "force_reauth": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def build_authorize_url(session: AsyncSession) -> AuthorizeRequest:
    settings = get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.INSTAGRAM.value)
    state = await create_oauth_state(
        Platform.INSTAGRAM.value,
        session=session,
        extra={"redirect_uri": settings.meta_redirect_uri},
    )
    return AuthorizeRequest(
        url=authorize_url(state, settings=settings),
        state=state,
        csrf_cookie=bind_oauth_csrf_cookie(state),
    )


async def _exchange_long_lived(short_token: str, settings: Settings) -> dict:
    version = settings.instagram_graph_version.strip() or "v21.0"
    params = {
        "grant_type": "ig_exchange_token",
        "client_secret": settings.meta_app_secret,
        "access_token": short_token,
    }
    try:
        return await get_json(
            LONG_LIVED_TOKEN_URL,
            platform=Platform.INSTAGRAM.value,
            params=params,
        )
    except OAuthExchangeError:
        return await get_json(
            f"https://graph.instagram.com/{version}/access_token",
            platform=Platform.INSTAGRAM.value,
            params=params,
        )


async def _account_label(access_token: str, settings: Settings) -> str | None:
    version = settings.instagram_graph_version.strip() or "v21.0"
    try:
        payload = await get_json(
            f"https://graph.instagram.com/{version}/me",
            platform=Platform.INSTAGRAM.value,
            params={"fields": "user_id,username", "access_token": access_token},
        )
    except OAuthExchangeError:
        try:
            payload = await get_json(
                ME_URL,
                platform=Platform.INSTAGRAM.value,
                params={"fields": "user_id,username", "access_token": access_token},
            )
        except OAuthExchangeError:
            logger.info("instagram_account_label_unavailable")
            return None
    username = payload.get("username")
    return username if isinstance(username, str) and username else None


async def exchange_authorization_code(
    session: AsyncSession,
    *,
    code: str,
    state: str,
    csrf_cookie: str | None = None,
) -> PlatformConnection:
    settings = get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.INSTAGRAM.value)
    if csrf_cookie is not None and not verify_oauth_csrf_cookie(csrf_cookie, state):
        raise OAuthStateError("OAuth CSRF cookie does not match state")
    claims = await verify_oauth_state(state, platform=Platform.INSTAGRAM.value, session=session)
    cleaned = code.strip().removesuffix("#_")
    short_payload = await post_form(
        SHORT_LIVED_TOKEN_URL,
        {
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": str(claims.extra.get("redirect_uri") or settings.meta_redirect_uri),
            "code": cleaned,
        },
        platform=Platform.INSTAGRAM.value,
    )
    short_token = read_access_token(short_payload)
    if not short_token:
        raise OAuthExchangeError(Platform.INSTAGRAM.value, "token exchange failed")
    unwrapped = unwrap_token_payload(short_payload)
    user_id = unwrapped.get("user_id")
    try:
        long_payload = await _exchange_long_lived(short_token, settings)
        access_token = read_access_token(long_payload) or short_token
        expires_in = read_expires_in(long_payload, 5_184_000)
    except OAuthExchangeError:
        logger.info("instagram_long_lived_exchange_skipped")
        access_token = short_token
        expires_in = read_expires_in(short_payload, 3600)
        long_payload = short_payload
    label = await _account_label(access_token, settings)
    connection = await persist_tokens(
        session,
        Platform.INSTAGRAM,
        access_token=access_token,
        refresh_token=None,
        expires_in=expires_in,
        scopes=read_scope_list(long_payload, list(INSTAGRAM_SCOPES)),
        state=ConnectionState.CONNECTED,
        account_label=label,
        metadata={
            "oauth_provider": "meta",
            **({"user_id": str(user_id)} if user_id is not None else {}),
        },
    )
    await mark_checklist_completed(session, list(INSTAGRAM_OAUTH_TITLES))
    await sync_connection_states(session)
    await session.refresh(connection)
    logger.info("instagram_oauth_connected", state=connection.state)
    return connection
