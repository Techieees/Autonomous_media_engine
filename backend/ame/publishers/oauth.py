from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from ame.config import Settings, get_settings
from ame.contracts.enums import ConnectionState, Platform
from ame.security.secrets import decrypt_secret, encrypt_secret
from ame.storage import get_store

_STATE_TTL_SECONDS = 600

YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/youtube "
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/yt-analytics.readonly"
)

INSTAGRAM_SCOPES = (
    "instagram_basic,instagram_content_publish,instagram_manage_insights,"
    "pages_show_list,pages_read_engagement"
)

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES = "user.info.basic,video.publish,video.upload,video.list"

READY_STATES = frozenset(
    {ConnectionState.CONNECTED.value, ConnectionState.READY.value}
)


class OAuthState(BaseModel):
    platform: str
    nonce: str
    expires_at: int


class AuthorizeRequest(BaseModel):
    platform: Platform
    url: str | None = None
    state: str | None = None
    status: ConnectionState
    instructions: str
    scopes: str = ""


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    scopes: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


def _hmac_key(settings: Settings | None = None) -> bytes:
    return hashlib.sha256((settings or get_settings()).secret_key.encode()).digest()


def create_oauth_state(platform: Platform | str, *, ttl_seconds: int = _STATE_TTL_SECONDS) -> str:
    # Dashboard OAuth uses async ame.oauth.state. This helper is HMAC-only for adapters.
    nonce = uuid4().hex
    expires_at = int(time.time()) + ttl_seconds
    body = f"{platform}:{nonce}:{expires_at}"
    signature = hmac.new(_hmac_key(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{body}.{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_oauth_state(state: str, *, expected_platform: str | None = None) -> OAuthState:
    padded = state + "=" * (-len(state) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    body, _, signature = decoded.rpartition(".")
    expected = hmac.new(_hmac_key(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise ValueError("oauth state signature mismatch")
    platform, nonce, expires_raw = body.split(":", 2)
    expires_at = int(expires_raw)
    if expires_at < int(time.time()):
        raise ValueError("oauth state expired")
    if expected_platform and platform != expected_platform:
        raise ValueError("oauth state platform mismatch")
    return OAuthState(platform=platform, nonce=nonce, expires_at=expires_at)


def build_authorize_url(platform: Platform | str, *, state: str | None = None) -> AuthorizeRequest:
    settings = get_settings()
    resolved = Platform(platform)
    signed = state or create_oauth_state(resolved)
    if resolved is Platform.YOUTUBE:
        if not settings.youtube_client_id:
            return AuthorizeRequest(
                platform=resolved,
                status=ConnectionState.CONNECTION_REQUIRED,
                instructions=(
                    "Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET, then start OAuth "
                    "from the dashboard. Do not paste passwords or tokens into chat."
                ),
                scopes=YOUTUBE_SCOPES,
            )
        params = httpx.QueryParams(
            {
                "client_id": settings.youtube_client_id,
                "redirect_uri": settings.youtube_redirect_uri,
                "response_type": "code",
                "scope": YOUTUBE_SCOPES,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": signed,
            }
        )
        return AuthorizeRequest(
            platform=resolved,
            url=f"{YOUTUBE_AUTH_URL}?{params}",
            state=signed,
            status=ConnectionState.CONNECTION_REQUIRED,
            instructions="Authorize a dedicated YouTube channel. Never share passwords.",
            scopes=YOUTUBE_SCOPES,
        )
    if resolved is Platform.INSTAGRAM:
        if not settings.meta_app_id:
            return AuthorizeRequest(
                platform=resolved,
                status=ConnectionState.CONNECTION_REQUIRED,
                instructions=(
                    "Set META_APP_ID and META_APP_SECRET, convert the Instagram account "
                    "to Professional, and connect it to a Facebook Page. Do not paste passwords."
                ),
                scopes=INSTAGRAM_SCOPES,
            )
        version = settings.instagram_graph_version
        params = httpx.QueryParams(
            {
                "client_id": settings.meta_app_id,
                "redirect_uri": settings.meta_redirect_uri,
                "response_type": "code",
                "scope": INSTAGRAM_SCOPES,
                "state": signed,
            }
        )
        return AuthorizeRequest(
            platform=resolved,
            url=f"https://www.facebook.com/{version}/dialog/oauth?{params}",
            state=signed,
            status=ConnectionState.CONNECTION_REQUIRED,
            instructions=(
                "Authorize a Professional Instagram account linked to a Facebook Page. "
                "Never share passwords or tokens."
            ),
            scopes=INSTAGRAM_SCOPES,
        )
    if resolved is Platform.TIKTOK:
        if not settings.tiktok_client_key:
            return AuthorizeRequest(
                platform=resolved,
                status=ConnectionState.CONNECTION_REQUIRED,
                instructions=(
                    "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET, then complete TikTok "
                    "app review for video.publish. Do not paste passwords or tokens."
                ),
                scopes=TIKTOK_SCOPES,
            )
        params = httpx.QueryParams(
            {
                "client_key": settings.tiktok_client_key,
                "redirect_uri": settings.tiktok_redirect_uri,
                "response_type": "code",
                "scope": TIKTOK_SCOPES,
                "state": signed,
            }
        )
        return AuthorizeRequest(
            platform=resolved,
            url=f"{TIKTOK_AUTH_URL}?{params}",
            state=signed,
            status=ConnectionState.CONNECTION_REQUIRED,
            instructions=(
                "Authorize TikTok Login Kit. Direct Post stays blocked until app review "
                "and creator consent are complete. Do not bypass confirmation."
            ),
            scopes=TIKTOK_SCOPES,
        )
    return AuthorizeRequest(
        platform=resolved,
        status=ConnectionState.READY,
        instructions="Dry-run does not use OAuth.",
    )


def load_access_token(connection: Any) -> str | None:
    blob = getattr(connection, "token_encrypted", None) if connection is not None else None
    if not blob:
        return None
    raw = decrypt_secret(blob)
    if raw.startswith("{"):
        data = json.loads(raw)
        token = data.get("access_token") or data.get("accessToken")
        return str(token) if token else None
    return raw


def load_refresh_token(connection: Any) -> str | None:
    blob = getattr(connection, "refresh_encrypted", None) if connection is not None else None
    if not blob:
        return None
    return decrypt_secret(blob)


def persist_token_bundle(connection: Any, bundle: TokenBundle) -> None:
    payload = {"access_token": bundle.access_token, **bundle.extra}
    connection.token_encrypted = encrypt_secret(json.dumps(payload))
    if bundle.refresh_token:
        connection.refresh_encrypted = encrypt_secret(bundle.refresh_token)
    if bundle.expires_in is not None:
        connection.expires_at = datetime.now(UTC) + timedelta(seconds=int(bundle.expires_in))
    if bundle.scopes:
        connection.scopes = bundle.scopes


def token_needs_refresh(connection: Any) -> bool:
    expires_at = getattr(connection, "expires_at", None) if connection is not None else None
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC) + timedelta(minutes=2)


def connection_is_ready(connection: Any) -> bool:
    if connection is None:
        return False
    state = getattr(connection, "state", None)
    return state in READY_STATES and bool(load_access_token(connection))


def is_simulated(content: Any) -> bool:
    return bool(getattr(content, "simulation", False))


def connection_scopes(connection: Any) -> list[str]:
    raw = getattr(connection, "scopes", None) if connection is not None else None
    if not raw:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.replace(" ", ",").split(",") if item.strip()]
    return [str(item) for item in raw]


def connection_metadata(connection: Any) -> dict[str, Any]:
    raw = getattr(connection, "metadata_json", None) if connection is not None else None
    return dict(raw or {})


def public_http_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"http_{response.status_code}"
    error = data.get("error", data)
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or error.get("type")
        return str(message or response.status_code)[:400]
    return str(error)[:400]


def load_media(media_key: str) -> tuple[bytes, Path | None]:
    store = get_store()
    try:
        path = store.local_path(media_key)
        if path.exists():
            return path.read_bytes(), path
    except RuntimeError:
        pass
    return store.get(media_key), None


@asynccontextmanager
async def http_client(
    existing: httpx.AsyncClient | None = None,
    *,
    timeout: float = 60.0,
):
    if existing is not None:
        yield existing
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        yield client


def _form_token(data: dict[str, str]) -> dict[str, Any]:
    return data


async def exchange_youtube_code(
    code: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    settings = get_settings()
    async with http_client(client) as http:
        response = await http.post(
            YOUTUBE_TOKEN_URL,
            data=_form_token(
                {
                    "client_id": settings.youtube_client_id,
                    "client_secret": settings.youtube_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.youtube_redirect_uri,
                }
            ),
        )
        response.raise_for_status()
        return _bundle_from_google(response.json())


async def refresh_youtube_token(
    refresh_token: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    settings = get_settings()
    async with http_client(client) as http:
        response = await http.post(
            YOUTUBE_TOKEN_URL,
            data=_form_token(
                {
                    "client_id": settings.youtube_client_id,
                    "client_secret": settings.youtube_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                }
            ),
        )
        response.raise_for_status()
        bundle = _bundle_from_google(response.json())
        if not bundle.refresh_token:
            bundle.refresh_token = refresh_token
        return bundle


async def exchange_instagram_code(
    code: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    settings = get_settings()
    version = settings.instagram_graph_version
    async with http_client(client) as http:
        response = await http.get(
            f"https://graph.facebook.com/{version}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri,
                "code": code,
            },
        )
        response.raise_for_status()
        short = response.json()
        short_token = short.get("access_token")
        if not short_token:
            raise ValueError("instagram token exchange returned no access_token")
        long_lived = await http.get(
            f"https://graph.facebook.com/{version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )
        payload = long_lived.json() if long_lived.is_success else short
        token = payload.get("access_token") or short_token
        return TokenBundle(
            access_token=str(token),
            expires_in=payload.get("expires_in") or short.get("expires_in"),
            extra={"token_type": payload.get("token_type", "bearer")},
        )


async def refresh_instagram_token(
    access_token: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    settings = get_settings()
    version = settings.instagram_graph_version
    async with http_client(client) as http:
        response = await http.get(
            f"https://graph.facebook.com/{version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": access_token,
            },
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ValueError("instagram token refresh returned no access_token")
        return TokenBundle(
            access_token=str(token),
            expires_in=payload.get("expires_in"),
            extra={"token_type": payload.get("token_type", "bearer")},
        )


async def exchange_tiktok_code(
    code: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    settings = get_settings()
    async with http_client(client) as http:
        response = await http.post(
            TIKTOK_TOKEN_URL,
            data=_form_token(
                {
                    "client_key": settings.tiktok_client_key,
                    "client_secret": settings.tiktok_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.tiktok_redirect_uri,
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return _bundle_from_tiktok(response.json())


async def refresh_tiktok_token(
    refresh_token: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    settings = get_settings()
    async with http_client(client) as http:
        response = await http.post(
            TIKTOK_TOKEN_URL,
            data=_form_token(
                {
                    "client_key": settings.tiktok_client_key,
                    "client_secret": settings.tiktok_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        bundle = _bundle_from_tiktok(response.json())
        if not bundle.refresh_token:
            bundle.refresh_token = refresh_token
        return bundle


def _bundle_from_google(payload: dict[str, Any]) -> TokenBundle:
    token = payload.get("access_token")
    if not token:
        raise ValueError("google token response missing access_token")
    scopes = str(payload.get("scope") or "").split()
    return TokenBundle(
        access_token=str(token),
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in"),
        scopes=scopes,
        extra={"token_type": payload.get("token_type", "Bearer")},
    )


def _bundle_from_tiktok(payload: dict[str, Any]) -> TokenBundle:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    token = data.get("access_token")
    if not token:
        raise ValueError("tiktok token response missing access_token")
    scopes = [item for item in str(data.get("scope") or "").split(",") if item]
    extra = {"open_id": data.get("open_id"), "token_type": data.get("token_type", "Bearer")}
    return TokenBundle(
        access_token=str(token),
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        scopes=scopes,
        extra={key: value for key, value in extra.items() if value},
    )
