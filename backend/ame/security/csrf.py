from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from ame.config import get_settings

CSRF_COOKIE_NAME = "ame_oauth_csrf"
CSRF_HEADER_NAME = "X-AME-CSRF"
CSRF_TTL_SECONDS = 600
_PURPOSE = b"ame.csrf.v1"


def _key() -> bytes:
    return hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()


def _sign(payload: bytes) -> str:
    digest = hmac.new(_key(), _PURPOSE + payload, hashlib.sha256).digest()
    return digest.hex()


def issue_csrf_token() -> str:
    nonce = secrets.token_urlsafe(32)
    return f"{nonce}.{_sign(nonce.encode('ascii'))}"


def verify_csrf_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    nonce, _, provided = token.partition(".")
    if not nonce or not provided:
        return False
    expected = _sign(nonce.encode("ascii"))
    return hmac.compare_digest(provided, expected)


def bind_oauth_csrf_cookie(state: str) -> str:
    return _sign(f"oauth-state:{state}".encode("utf-8"))


def verify_oauth_csrf_cookie(cookie_value: str | None, state: str) -> bool:
    if not cookie_value or not state:
        return False
    expected = bind_oauth_csrf_cookie(state)
    return hmac.compare_digest(cookie_value, expected)


def verify_double_submit(cookie_value: str | None, submitted: str | None) -> bool:
    if not cookie_value or not submitted:
        return False
    return hmac.compare_digest(cookie_value, submitted)


def csrf_cookie_settings(*, secure: bool | None = None) -> dict[str, Any]:
    settings = get_settings()
    if secure is None:
        secure = settings.app_env == "production"
    return {
        "key": CSRF_COOKIE_NAME,
        "max_age": CSRF_TTL_SECONDS,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
    }
