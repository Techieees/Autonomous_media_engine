from __future__ import annotations

from typing import Any

import httpx

from ame.trends.normalize import host_of

DEFAULT_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
DEFAULT_HEADERS = {
    "User-Agent": "autonomous-media-engine/0.1 (+trend-discovery; official-apis-only)",
    "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
}


def public_http_error(exc: BaseException, url: str) -> RuntimeError:
    host = host_of(url)
    if isinstance(exc, httpx.TimeoutException):
        return RuntimeError(f"timeout contacting {host}")
    if isinstance(exc, httpx.HTTPStatusError):
        return RuntimeError(f"http {exc.response.status_code} from {host}")
    if isinstance(exc, httpx.HTTPError):
        return RuntimeError(f"http error from {host}")
    return RuntimeError(f"request failed for {host}")


async def get_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> httpx.Response:
    try:
        response = await client.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response
    except httpx.HTTPError as exc:
        raise public_http_error(exc, url) from None


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> Any:
    response = await get_response(
        client, url, params=params, headers=headers, timeout=timeout
    )
    return response.json()


async def post_form(
    client: httpx.AsyncClient,
    url: str,
    *,
    data: dict[str, Any],
    auth: tuple[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> Any:
    try:
        response = await client.post(
            url, data=data, auth=auth, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise public_http_error(exc, url) from None
