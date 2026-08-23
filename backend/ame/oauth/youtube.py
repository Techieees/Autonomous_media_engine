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
from ame.oauth.state import create_oauth_state, generate_pkce, verify_oauth_state
from ame.oauth.tokens import (
    mark_checklist_completed,
    persist_tokens,
    read_access_token,
    read_expires_in,
    read_refresh_token,
    read_scope_list,
)
from ame.observability import get_logger
from ame.security.csrf import bind_oauth_csrf_cookie, verify_oauth_csrf_cookie

logger = get_logger("ame.oauth.youtube")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNEL_URL = "https://www.googleapis.com/youtube/v3/channels"

YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)

YOUTUBE_OAUTH_TITLES = (
    SPECS_BY_KEY["youtube.dedicated_account"].title,
    SPECS_BY_KEY["youtube.oauth"].title,
)


class AuthorizeRequest(BaseModel):
    platform: str = Platform.YOUTUBE.value
    url: str
    state: str
    csrf_cookie: str
    expires_in: int = 600


def credentials_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.youtube_client_id and settings.youtube_client_secret)


def authorize_url(
    state: str,
    *,
    settings: Settings | None = None,
    code_challenge: str | None = None,
) -> str:
    settings = settings or get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.YOUTUBE.value)
    params = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": settings.youtube_redirect_uri,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def build_authorize_url(session: AsyncSession) -> AuthorizeRequest:
    settings = get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.YOUTUBE.value)
    verifier, challenge = generate_pkce()
    state = await create_oauth_state(
        Platform.YOUTUBE.value,
        session=session,
        code_verifier=verifier,
        extra={"redirect_uri": settings.youtube_redirect_uri},
    )
    return AuthorizeRequest(
        url=authorize_url(state, settings=settings, code_challenge=challenge),
        state=state,
        csrf_cookie=bind_oauth_csrf_cookie(state),
    )


async def _channel_label(access_token: str) -> str | None:
    try:
        payload = await get_json(
            CHANNEL_URL,
            platform=Platform.YOUTUBE.value,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"part": "snippet", "mine": "true"},
        )
    except OAuthExchangeError:
        logger.info("youtube_account_label_unavailable")
        return None
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    snippet = items[0].get("snippet") if isinstance(items[0], dict) else None
    if not isinstance(snippet, dict):
        return None
    title = snippet.get("title")
    return title if isinstance(title, str) and title else None


async def exchange_authorization_code(
    session: AsyncSession,
    *,
    code: str,
    state: str,
    csrf_cookie: str | None = None,
) -> PlatformConnection:
    settings = get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.YOUTUBE.value)
    if csrf_cookie is not None and not verify_oauth_csrf_cookie(csrf_cookie, state):
        raise OAuthStateError("OAuth CSRF cookie does not match state")
    claims = await verify_oauth_state(state, platform=Platform.YOUTUBE.value, session=session)
    cleaned = code.strip().removesuffix("#_")
    form = {
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "code": cleaned,
        "grant_type": "authorization_code",
        "redirect_uri": str(claims.extra.get("redirect_uri") or settings.youtube_redirect_uri),
    }
    if claims.code_verifier:
        form["code_verifier"] = claims.code_verifier
    payload = await post_form(TOKEN_URL, form, platform=Platform.YOUTUBE.value)
    access_token = read_access_token(payload)
    if not access_token:
        raise OAuthExchangeError(Platform.YOUTUBE.value, "token exchange failed")
    refresh_token = read_refresh_token(payload)
    scopes = read_scope_list(payload, list(YOUTUBE_SCOPES))
    can_publish = any("youtube.upload" in scope for scope in scopes)
    next_state = (
        ConnectionState.READY if refresh_token and can_publish else ConnectionState.CONNECTED
    )
    label = await _channel_label(access_token)
    connection = await persist_tokens(
        session,
        Platform.YOUTUBE,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=read_expires_in(payload, 3600),
        scopes=scopes,
        state=next_state,
        account_label=label,
        metadata={"oauth_provider": "google"},
    )
    await mark_checklist_completed(session, list(YOUTUBE_OAUTH_TITLES))
    await sync_connection_states(session)
    await session.refresh(connection)
    logger.info("youtube_oauth_connected", state=connection.state)
    return connection
