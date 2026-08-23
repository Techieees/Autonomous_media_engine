from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from ame.oauth.errors import OAuthExchangeError
from ame.observability import get_logger

logger = get_logger("ame.oauth.http")

_TIMEOUT = httpx.Timeout(20.0)


def _host(url: str) -> str:
    return urlparse(url).netloc


def _extract_error_code(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    for key in ("error", "error_type"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:80]
    nested = payload.get("error")
    if isinstance(nested, dict):
        code = nested.get("code") or nested.get("error_code") or nested.get("message")
        if code:
            return str(code)[:80]
    return fallback


async def post_form(url: str, data: dict[str, str], *, platform: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.post(
                url,
                data=data,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError:
        logger.warning("oauth_http_error", platform=platform, host=_host(url), method="POST")
        raise OAuthExchangeError(platform, "token endpoint is unreachable") from None
    return _read_json(response, platform=platform, host=_host(url), method="POST")


async def get_json(
    url: str,
    *,
    platform: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError:
        logger.warning("oauth_http_error", platform=platform, host=_host(url), method="GET")
        raise OAuthExchangeError(platform, "api endpoint is unreachable") from None
    return _read_json(response, platform=platform, host=_host(url), method="GET")


def _read_json(response: httpx.Response, *, platform: str, host: str, method: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        code = _extract_error_code(payload, f"http_{response.status_code}")
        logger.warning(
            "oauth_http_status",
            platform=platform,
            host=host,
            method=method,
            status_code=response.status_code,
            error_code=code,
        )
        raise OAuthExchangeError(platform, "token exchange failed")
    if not isinstance(payload, dict):
        raise OAuthExchangeError(platform, "token exchange returned an unexpected payload")
    error_code = payload.get("error")
    if isinstance(error_code, str) and error_code and error_code.lower() not in {"ok", "success"}:
        logger.warning(
            "oauth_http_status",
            platform=platform,
            host=host,
            method=method,
            status_code=response.status_code,
            error_code=error_code[:80],
        )
        raise OAuthExchangeError(platform, "token exchange failed")
    nested = payload.get("error")
    if isinstance(nested, dict):
        nested_code = str(nested.get("code") or "")
        if nested_code and nested_code.lower() not in {"ok", "0", "success"}:
            logger.warning(
                "oauth_http_status",
                platform=platform,
                host=host,
                method=method,
                status_code=response.status_code,
                error_code=nested_code[:80],
            )
            raise OAuthExchangeError(platform, "token exchange failed")
    return payload
