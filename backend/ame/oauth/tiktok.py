from __future__ import annotations

from urllib.parse import urlencode

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.bootstrap.instructions import SPECS_BY_KEY
from ame.bootstrap.status import sync_connection_states
from ame.config import Settings, get_settings
from ame.contracts.enums import ConnectionState, HumanActionStatus, Platform
from ame.db.models import HumanAction, PlatformConnection
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
    unwrap_token_payload,
)
from ame.observability import get_logger
from ame.security.csrf import bind_oauth_csrf_cookie, verify_oauth_csrf_cookie

logger = get_logger("ame.oauth.tiktok")

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

TIKTOK_SCOPES = (
    "user.info.basic",
    "user.info.profile",
    "video.upload",
    "video.publish",
)

TIKTOK_OAUTH_TITLES = (
    SPECS_BY_KEY["tiktok.dedicated_account"].title,
    SPECS_BY_KEY["tiktok.developer_app"].title,
)
TIKTOK_REVIEW_TITLE = SPECS_BY_KEY["tiktok.app_review"].title


class AuthorizeRequest(BaseModel):
    platform: str = Platform.TIKTOK.value
    url: str
    state: str
    csrf_cookie: str
    expires_in: int = 600


def credentials_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.tiktok_client_key and settings.tiktok_client_secret)


def authorize_url(
    state: str,
    *,
    settings: Settings | None = None,
    code_challenge: str | None = None,
) -> str:
    settings = settings or get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.TIKTOK.value)
    params = {
        "client_key": settings.tiktok_client_key,
        "redirect_uri": settings.tiktok_redirect_uri,
        "response_type": "code",
        "scope": ",".join(TIKTOK_SCOPES),
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def build_authorize_url(session: AsyncSession) -> AuthorizeRequest:
    settings = get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.TIKTOK.value)
    verifier, challenge = generate_pkce()
    state = await create_oauth_state(
        Platform.TIKTOK.value,
        session=session,
        code_verifier=verifier,
        extra={"redirect_uri": settings.tiktok_redirect_uri},
    )
    return AuthorizeRequest(
        url=authorize_url(state, settings=settings, code_challenge=challenge),
        state=state,
        csrf_cookie=bind_oauth_csrf_cookie(state),
    )


async def _review_open(session: AsyncSession) -> bool:
    result = await session.execute(
        select(HumanAction).where(
            HumanAction.title == TIKTOK_REVIEW_TITLE,
            HumanAction.platform == Platform.TIKTOK.value,
            HumanAction.status == HumanActionStatus.OPEN.value,
        )
    )
    return result.scalar_one_or_none() is not None


async def _account_label(access_token: str) -> tuple[str | None, str | None]:
    try:
        payload = await get_json(
            USER_INFO_URL,
            platform=Platform.TIKTOK.value,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "open_id,display_name,username"},
        )
    except OAuthExchangeError:
        logger.info("tiktok_account_label_unavailable")
        return None, None
    data = payload.get("data")
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return None, None
    label = user.get("username") or user.get("display_name")
    open_id = user.get("open_id")
    return (
        label if isinstance(label, str) and label else None,
        open_id if isinstance(open_id, str) and open_id else None,
    )


async def exchange_authorization_code(
    session: AsyncSession,
    *,
    code: str,
    state: str,
    csrf_cookie: str | None = None,
) -> PlatformConnection:
    settings = get_settings()
    if not credentials_configured(settings):
        raise OAuthNotConfiguredError(Platform.TIKTOK.value)
    if csrf_cookie is not None and not verify_oauth_csrf_cookie(csrf_cookie, state):
        raise OAuthStateError("OAuth CSRF cookie does not match state")
    claims = await verify_oauth_state(state, platform=Platform.TIKTOK.value, session=session)
    cleaned = code.strip().removesuffix("#_")
    form = {
        "client_key": settings.tiktok_client_key,
        "client_secret": settings.tiktok_client_secret,
        "code": cleaned,
        "grant_type": "authorization_code",
        "redirect_uri": str(claims.extra.get("redirect_uri") or settings.tiktok_redirect_uri),
    }
    if claims.code_verifier:
        form["code_verifier"] = claims.code_verifier
    payload = await post_form(TOKEN_URL, form, platform=Platform.TIKTOK.value)
    access_token = read_access_token(payload)
    if not access_token:
        raise OAuthExchangeError(Platform.TIKTOK.value, "token exchange failed")
    unwrapped = unwrap_token_payload(payload)
    label, open_id = await _account_label(access_token)
    if open_id is None:
        raw_open_id = unwrapped.get("open_id")
        open_id = raw_open_id if isinstance(raw_open_id, str) else None
    next_state = (
        ConnectionState.NEEDS_PLATFORM_REVIEW
        if await _review_open(session)
        else ConnectionState.READY
    )
    connection = await persist_tokens(
        session,
        Platform.TIKTOK,
        access_token=access_token,
        refresh_token=read_refresh_token(payload),
        expires_in=read_expires_in(payload, 86400),
        scopes=read_scope_list(payload, list(TIKTOK_SCOPES)),
        state=next_state,
        account_label=label,
        metadata={
            "oauth_provider": "tiktok",
            "open_id": open_id,
        },
    )
    await mark_checklist_completed(session, list(TIKTOK_OAUTH_TITLES))
    await sync_connection_states(session)
    await session.refresh(connection)
    logger.info("tiktok_oauth_connected", state=connection.state)
    return connection
