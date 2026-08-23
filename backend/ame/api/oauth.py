from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ame.api.errors import APIError
from ame.api.services import upsert_platform_connection
from ame.config import Settings, get_settings
from ame.contracts.enums import ConnectionState, Platform
from ame.observability import get_logger
from ame.security.secrets import encrypt_secret

log = get_logger("ame.api.oauth")

YOUTUBE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/yt-analytics.readonly"
)

INSTAGRAM_SCOPES = (
    "instagram_basic,instagram_content_publish,pages_show_list,"
    "pages_read_engagement,instagram_manage_insights"
)

TIKTOK_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES = "user.info.basic,video.publish,video.upload"

INSTRUCTIONS = {
    Platform.YOUTUBE.value: (
        "Create a dedicated Google/YouTube brand account, then create an OAuth "
        "client in Google Cloud. Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, "
        "and YOUTUBE_REDIRECT_URI. Restart the API and open this start URL again. "
        "Never paste passwords or tokens into AME."
    ),
    Platform.INSTAGRAM.value: (
        "Create a dedicated Instagram professional account and a Meta app. "
        "Set META_APP_ID, META_APP_SECRET, and META_REDIRECT_URI. "
        "Restart the API and retry Instagram OAuth. AME never asks for the Instagram password."
    ),
    Platform.TIKTOK.value: (
        "Create a dedicated TikTok account and developer application. "
        "Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, and TIKTOK_REDIRECT_URI. "
        "Complete any official app review, then retry this start URL. "
        "Do not send credentials to AME."
    ),
}


def _key() -> bytes:
    return hashlib.sha256(get_settings().secret_key.encode()).digest()


def local_create_oauth_state(platform: str, ttl_seconds: int = 600) -> str:
    nonce = uuid4().hex
    exp = int(time.time()) + ttl_seconds
    body = f"{platform}:{nonce}:{exp}"
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    raw = f"{body}:{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def local_verify_oauth_state(state: str, expected_platform: str) -> dict[str, str]:
    padded = state + "=" * (-len(state) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        platform, nonce, exp_s, sig = raw.split(":", 3)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError("invalid_oauth_state", "OAuth state is invalid", status_code=400) from exc
    body = f"{platform}:{nonce}:{exp_s}"
    expected = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise APIError("invalid_oauth_state", "OAuth state signature mismatch", status_code=400)
    try:
        exp = int(exp_s)
    except ValueError as exc:
        raise APIError("invalid_oauth_state", "OAuth state is invalid", status_code=400) from exc
    if exp < int(time.time()):
        raise APIError("expired_oauth_state", "OAuth state expired", status_code=400)
    if platform != expected_platform:
        raise APIError("invalid_oauth_state", "OAuth state platform mismatch", status_code=400)
    return {"platform": platform, "nonce": nonce}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def create_oauth_state(platform: str) -> str:
    try:
        from ame.oauth.state import create_oauth_state as external
    except ImportError:
        return local_create_oauth_state(platform)
    return str(await _maybe_await(external(platform)))


async def verify_oauth_state(state: str, platform: str) -> dict[str, Any]:
    try:
        from ame.oauth.state import verify_oauth_state as external
    except ImportError:
        return local_verify_oauth_state(state, platform)
    try:
        try:
            verified = await _maybe_await(external(state, platform))
        except TypeError:
            verified = await _maybe_await(external(state))
        if isinstance(verified, dict):
            return verified
        return {"platform": platform, "raw": str(verified)}
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise APIError("invalid_oauth_state", "OAuth state is invalid", status_code=400) from exc


def _client_id(settings: Settings, platform: str) -> str:
    if platform == Platform.YOUTUBE.value:
        return settings.youtube_client_id
    if platform == Platform.INSTAGRAM.value:
        return settings.meta_app_id
    if platform == Platform.TIKTOK.value:
        return settings.tiktok_client_key
    return ""


def _client_secret(settings: Settings, platform: str) -> str:
    if platform == Platform.YOUTUBE.value:
        return settings.youtube_client_secret
    if platform == Platform.INSTAGRAM.value:
        return settings.meta_app_secret
    if platform == Platform.TIKTOK.value:
        return settings.tiktok_client_secret
    return ""


def _redirect_uri(settings: Settings, platform: str) -> str:
    if platform == Platform.YOUTUBE.value:
        return settings.youtube_redirect_uri
    if platform == Platform.INSTAGRAM.value:
        return settings.meta_redirect_uri
    return settings.tiktok_redirect_uri


def connection_required_payload(platform: str) -> dict[str, str]:
    instructions = INSTRUCTIONS[platform]
    try:
        from ame.bootstrap.instructions import generate_platform_instructions

        instructions = generate_platform_instructions(platform)
    except (ImportError, KeyError):
        pass
    return {
        "state": ConnectionState.CONNECTION_REQUIRED.value,
        "instructions": instructions,
        "platform": platform,
    }


def local_authorize_url(platform: str, state: str, settings: Settings) -> str:
    client_id = _client_id(settings, platform)
    redirect_uri = _redirect_uri(settings, platform)
    if platform == Platform.YOUTUBE.value:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": YOUTUBE_SCOPES,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": state,
            }
        )
        return f"{YOUTUBE_AUTH}?{query}"
    if platform == Platform.INSTAGRAM.value:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": INSTAGRAM_SCOPES,
                "state": state,
            }
        )
        return (
            f"https://www.facebook.com/{settings.instagram_graph_version}/dialog/oauth?{query}"
        )
    query = urlencode(
        {
            "client_key": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": TIKTOK_SCOPES,
            "state": state,
        }
    )
    return f"{TIKTOK_AUTH}?{query}"


async def authorize_url(platform: str, state: str, settings: Settings) -> str:
    try:
        module = __import__(
            f"ame.oauth.{platform}",
            fromlist=["authorize_url", "build_authorize_url"],
        )
        builder = getattr(module, "authorize_url", None) or getattr(
            module, "build_authorize_url", None
        )
        if builder is not None:
            return str(await _maybe_await(builder(state)))
    except (ImportError, TypeError, AttributeError):
        pass
    return local_authorize_url(platform, state, settings)


def _token_payload(data: dict[str, Any]) -> dict[str, Any]:
    inner = data.get("data")
    if isinstance(inner, dict) and ("access_token" in inner or "accessToken" in inner):
        return inner
    return data


async def exchange_code(platform: str, code: str, settings: Settings) -> dict[str, Any]:
    try:
        module = __import__(f"ame.oauth.{platform}", fromlist=["exchange_code"])
        exchanger = getattr(module, "exchange_code", None)
        if exchanger is not None:
            return dict(await _maybe_await(exchanger(code)))
    except (ImportError, TypeError, AttributeError):
        pass
    client_id = _client_id(settings, platform)
    client_secret = _client_secret(settings, platform)
    redirect_uri = _redirect_uri(settings, platform)
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if platform == Platform.YOUTUBE.value:
            response = await client.post(
                YOUTUBE_TOKEN,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        elif platform == Platform.INSTAGRAM.value:
            version = settings.instagram_graph_version
            response = await client.get(
                f"https://graph.facebook.com/{version}/oauth/access_token",
                params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
        else:
            response = await client.post(
                TIKTOK_TOKEN,
                data={
                    "client_key": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    log.info("oauth_token_exchange", platform=platform, status=response.status_code)
    try:
        data = response.json()
    except ValueError as exc:
        raise APIError(
            "oauth_exchange_failed",
            "Token endpoint returned non-JSON",
            status_code=502,
        ) from exc
    if response.status_code >= 400:
        raise APIError(
            "oauth_exchange_failed",
            "Official token exchange failed",
            status_code=502,
            details={"platform": platform, "status": response.status_code},
        )
    payload = _token_payload(data if isinstance(data, dict) else {})
    access = payload.get("access_token") or payload.get("accessToken")
    if not access:
        raise APIError(
            "oauth_exchange_failed",
            "Token endpoint did not return an access token",
            status_code=502,
        )
    refresh = payload.get("refresh_token") or payload.get("refreshToken")
    expires_in = int(payload.get("expires_in") or payload.get("expiresIn") or 3600)
    scopes = payload.get("scope") or payload.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [item for item in scopes.replace(",", " ").split() if item]
    result = {
        "access_token": str(access),
        "refresh_token": str(refresh) if refresh else None,
        "expires_in": expires_in,
        "scopes": scopes,
        "token_type": payload.get("token_type") or "bearer",
    }
    if platform == Platform.INSTAGRAM.value and result["access_token"]:
        try:
            long_lived = await client_long_lived_instagram(settings, result["access_token"])
            if long_lived:
                result.update(long_lived)
        except Exception:  # noqa: BLE001
            log.warning("instagram_long_lived_failed")
    return result


async def client_long_lived_instagram(
    settings: Settings, short_token: str
) -> dict[str, Any] | None:
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"https://graph.facebook.com/{settings.instagram_graph_version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )
    if response.status_code >= 400:
        return None
    data = response.json()
    access = data.get("access_token")
    if not access:
        return None
    return {
        "access_token": access,
        "expires_in": int(data.get("expires_in") or 60 * 60 * 24 * 60),
        "token_type": data.get("token_type") or "bearer",
    }


async def persist_tokens(
    session: AsyncSession,
    platform: str,
    tokens: dict[str, Any],
) -> str:
    conn = await upsert_platform_connection(session, platform)
    conn.token_encrypted = encrypt_secret(str(tokens["access_token"]))
    if tokens.get("refresh_token"):
        conn.refresh_encrypted = encrypt_secret(str(tokens["refresh_token"]))
    conn.expires_at = datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in") or 3600))
    scopes = tokens.get("scopes") or []
    if scopes:
        conn.scopes = list(scopes)
    conn.metadata_json = {
        **(conn.metadata_json or {}),
        "token_type": tokens.get("token_type") or "bearer",
        "connected_at": datetime.now(UTC).isoformat(),
    }
    conn.state = (
        ConnectionState.READY.value
        if conn.refresh_encrypted
        else ConnectionState.CONNECTED.value
    )
    await session.commit()
    return conn.state


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept") or ""
    return "text/html" in accept


def finish_response(request: Request, payload: dict[str, Any]) -> JSONResponse | RedirectResponse:
    if _wants_html(request) and payload.get("state") not in {None}:
        origin = get_settings().dashboard_origin.rstrip("/")
        query = urlencode(
            {
                "platform": payload.get("platform") or "",
                "connection_state": payload.get("state") or "",
            }
        )
        return RedirectResponse(f"{origin}/bootstrap?{query}", status_code=302)
    return JSONResponse(payload)


async def start_oauth(platform: str, session: AsyncSession) -> JSONResponse | RedirectResponse:
    settings = get_settings()
    if not _client_id(settings, platform):
        return JSONResponse(connection_required_payload(platform))
    try:
        from ame.oauth import OAuthNotConfiguredError, build_authorize_url
        from ame.security.csrf import csrf_cookie_settings

        try:
            request = await build_authorize_url(session, platform)
        except OAuthNotConfiguredError:
            return JSONResponse(connection_required_payload(platform))
        response = RedirectResponse(request.url, status_code=302)
        cookie = csrf_cookie_settings()
        response.set_cookie(value=request.csrf_cookie, **cookie)
        return response
    except ImportError:
        pass
    conn = await upsert_platform_connection(session, platform)
    state = await create_oauth_state(platform)
    url = await authorize_url(platform, state, settings)
    if conn.state == ConnectionState.NOT_CONFIGURED.value:
        conn.state = ConnectionState.CONNECTION_REQUIRED.value
        await session.commit()
    return RedirectResponse(url, status_code=302)


async def callback_oauth(
    platform: str,
    request: Request,
    session: AsyncSession,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
) -> JSONResponse | RedirectResponse:
    settings = get_settings()
    if not _client_id(settings, platform) or not _client_secret(settings, platform):
        return JSONResponse(connection_required_payload(platform))
    if error:
        payload = {
            "state": ConnectionState.REQUIRES_HUMAN_ACTION.value,
            "platform": platform,
            "instructions": connection_required_payload(platform)["instructions"],
        }
        return finish_response(request, payload)
    if not state or not code:
        raise APIError("oauth_callback_invalid", "code and state are required", status_code=400)
    try:
        from ame.oauth import (
            OAuthExchangeError,
            OAuthNotConfiguredError,
            OAuthStateError,
            exchange_authorization_code,
        )
        from ame.security.csrf import CSRF_COOKIE_NAME

        try:
            connection = await exchange_authorization_code(
                session,
                platform,
                code=code,
                state=state,
                csrf_cookie=request.cookies.get(CSRF_COOKIE_NAME),
            )
        except OAuthNotConfiguredError:
            return JSONResponse(connection_required_payload(platform))
        except OAuthStateError as exc:
            raise APIError("invalid_oauth_state", exc.message, status_code=400) from exc
        except OAuthExchangeError as exc:
            raise APIError(
                "oauth_exchange_failed",
                "Official token exchange failed",
                status_code=502,
                details={"platform": platform},
            ) from exc
        stored = connection.state
        payload = {
            "state": stored.value if hasattr(stored, "value") else stored,
            "platform": platform,
            "simulation": False,
        }
        return finish_response(request, payload)
    except ImportError:
        pass
    await verify_oauth_state(state, platform)
    try:
        tokens = await exchange_code(platform, code, settings)
        stored_state = await persist_tokens(session, platform, tokens)
    except APIError:
        conn = await upsert_platform_connection(session, platform)
        conn.state = ConnectionState.NEEDS_REAUTHORIZATION.value
        await session.commit()
        raise
    payload = {
        "state": stored_state,
        "platform": platform,
        "simulation": False,
    }
    return finish_response(request, payload)
