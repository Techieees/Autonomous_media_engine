from sqlalchemy.ext.asyncio import AsyncSession

from ame.oauth.errors import (
    OAuthError,
    OAuthExchangeError,
    OAuthNotConfiguredError,
    OAuthStateError,
    public_oauth_error,
)
from ame.oauth.instagram import (
    build_authorize_url as build_instagram_authorize_url,
    credentials_configured as instagram_configured,
    exchange_authorization_code as exchange_instagram_code,
)
from ame.oauth.state import (
    OAUTH_STATE_CATEGORY,
    OAUTH_STATE_TTL_SECONDS,
    OAuthStateClaims,
    create_oauth_state,
    generate_pkce,
    verify_oauth_state,
)
from ame.oauth.tiktok import (
    build_authorize_url as build_tiktok_authorize_url,
    credentials_configured as tiktok_configured,
    exchange_authorization_code as exchange_tiktok_code,
)
from ame.oauth.youtube import (
    build_authorize_url as build_youtube_authorize_url,
    credentials_configured as youtube_configured,
    exchange_authorization_code as exchange_youtube_code,
)

__all__ = [
    "OAUTH_STATE_CATEGORY",
    "OAUTH_STATE_TTL_SECONDS",
    "OAuthError",
    "OAuthExchangeError",
    "OAuthNotConfiguredError",
    "OAuthStateClaims",
    "OAuthStateError",
    "build_authorize_url",
    "build_instagram_authorize_url",
    "build_tiktok_authorize_url",
    "build_youtube_authorize_url",
    "create_oauth_state",
    "exchange_authorization_code",
    "exchange_instagram_code",
    "exchange_tiktok_code",
    "exchange_youtube_code",
    "generate_pkce",
    "instagram_configured",
    "public_oauth_error",
    "tiktok_configured",
    "verify_oauth_state",
    "youtube_configured",
]


async def build_authorize_url(session: AsyncSession, platform: str):
    key = platform.lower()
    if key == "youtube":
        return await build_youtube_authorize_url(session)
    if key == "instagram":
        return await build_instagram_authorize_url(session)
    if key == "tiktok":
        return await build_tiktok_authorize_url(session)
    raise OAuthNotConfiguredError(key)


async def exchange_authorization_code(session: AsyncSession, platform: str, **kwargs):
    key = platform.lower()
    if key == "youtube":
        return await exchange_youtube_code(session, **kwargs)
    if key == "instagram":
        return await exchange_instagram_code(session, **kwargs)
    if key == "tiktok":
        return await exchange_tiktok_code(session, **kwargs)
    raise OAuthNotConfiguredError(key)
