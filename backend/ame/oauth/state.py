from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import HumanActionStatus
from ame.db.models import HumanAction
from ame.oauth.errors import OAuthStateError
from ame.observability import get_logger
from ame.security.secrets import decrypt_secret, encrypt_secret

logger = get_logger("ame.oauth.state")

OAUTH_STATE_TTL_SECONDS = 600
OAUTH_STATE_CATEGORY = "oauth_state"
REDIS_KEY_PREFIX = "ame:oauth:state:"
_PURPOSE = b"ame.oauth.state.v1"
_REDIS_COOLDOWN_SECONDS = 30

_memory_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] = {}
_redis_skip_until = 0.0
_fallback_redis: Any = None


class OAuthStateClaims(BaseModel):
    jti: str
    platform: str
    iat: int
    exp: int
    code_verifier: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _SignedState:
    token: str
    claims: OAuthStateClaims
    record: dict[str, Any]


def generate_pkce() -> tuple[str, str]:
    verifier = urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _hmac_key() -> bytes:
    return hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()


def _b64encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def _sign_payload(payload: bytes) -> str:
    digest = hmac.new(_hmac_key(), _PURPOSE + payload, hashlib.sha256).digest()
    return _b64encode(digest)


def _redis_key(jti: str) -> str:
    return f"{REDIS_KEY_PREFIX}{jti}"


def _action_title(jti: str) -> str:
    return f"oauth_state:{jti}"


def _mark_redis_down() -> None:
    global _redis_skip_until, _fallback_redis
    _redis_skip_until = time.monotonic() + _REDIS_COOLDOWN_SECONDS
    _fallback_redis = None


def _redis_client() -> Any | None:
    global _fallback_redis
    if time.monotonic() < _redis_skip_until:
        return None
    try:
        from ame.redis_client import get_redis

        client = get_redis()
        if client is not None:
            return client
    except Exception:
        pass
    if _fallback_redis is not None:
        return _fallback_redis
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
        client.ping()
        _fallback_redis = client
        return client
    except Exception:
        _mark_redis_down()
        return None


def _memory_put(jti: str, record: dict[str, Any]) -> None:
    with _memory_lock:
        now = int(time.time())
        stale = [key for key, value in _memory.items() if int(value.get("exp", 0)) <= now]
        for key in stale:
            _memory.pop(key, None)
        _memory[jti] = dict(record)


def _memory_consume(jti: str, platform: str) -> dict[str, Any] | None | bool:
    with _memory_lock:
        record = _memory.get(jti)
        if record is None:
            return None
        if record.get("used"):
            return False
        if record.get("platform") != platform:
            return False
        if int(record.get("exp", 0)) <= int(time.time()):
            _memory.pop(jti, None)
            return None
        record["used"] = True
        return dict(record)


def _redis_put(jti: str, record: dict[str, Any]) -> bool:
    client = _redis_client()
    if client is None:
        return False
    try:
        client.set(_redis_key(jti), json.dumps(record), ex=OAUTH_STATE_TTL_SECONDS)
        return True
    except Exception:
        logger.warning("oauth_state_redis_unavailable", action="set")
        _mark_redis_down()
        return False


def _redis_consume(jti: str, platform: str) -> dict[str, Any] | None | bool:
    client = _redis_client()
    if client is None:
        return None
    key = _redis_key(jti)
    try:
        if hasattr(client, "getdel"):
            raw = client.getdel(key)
        else:
            pipe = client.pipeline()
            pipe.get(key)
            pipe.delete(key)
            raw = pipe.execute()[0]
    except Exception:
        logger.warning("oauth_state_redis_unavailable", action="getdel")
        _mark_redis_down()
        return None
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if record.get("used"):
        return False
    if record.get("platform") != platform:
        return False
    return record


def _parse_db_record(action: HumanAction) -> dict[str, Any] | None:
    raw = action.instructions or ""
    if raw.startswith("AME_OAUTH_STATE:"):
        raw = raw[len("AME_OAUTH_STATE:") :]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


async def _db_put(session: AsyncSession, jti: str, record: dict[str, Any]) -> None:
    await _sweep_expired_db(session)
    session.add(
        HumanAction(
            title=_action_title(jti),
            instructions="AME_OAUTH_STATE:" + json.dumps(record, separators=(",", ":")),
            category=OAUTH_STATE_CATEGORY,
            status=HumanActionStatus.OPEN.value,
            platform=record.get("platform"),
            blocking=False,
        )
    )
    await session.flush()


async def _db_consume(session: AsyncSession, jti: str, platform: str) -> dict[str, Any] | None | bool:
    result = await session.execute(
        select(HumanAction).where(
            HumanAction.category == OAUTH_STATE_CATEGORY,
            HumanAction.title == _action_title(jti),
        )
    )
    action = result.scalar_one_or_none()
    if action is None:
        return None
    if action.status == HumanActionStatus.COMPLETED.value:
        return False
    if action.status != HumanActionStatus.OPEN.value:
        return False
    record = _parse_db_record(action)
    if record is None:
        action.status = HumanActionStatus.CANCELLED.value
        await session.flush()
        return False
    if record.get("platform") != platform:
        return False
    if int(record.get("exp", 0)) <= int(time.time()):
        action.status = HumanActionStatus.CANCELLED.value
        await session.flush()
        return None
    record["used"] = True
    action.status = HumanActionStatus.COMPLETED.value
    action.instructions = "AME_OAUTH_STATE:" + json.dumps(record, separators=(",", ":"))
    await session.flush()
    return record


async def _sweep_expired_db(session: AsyncSession) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    result = await session.execute(
        select(HumanAction)
        .where(
            HumanAction.category == OAUTH_STATE_CATEGORY,
            HumanAction.status == HumanActionStatus.OPEN.value,
            HumanAction.created_at < cutoff,
        )
        .limit(100)
    )
    for action in result.scalars():
        action.status = HumanActionStatus.CANCELLED.value


def _build_signed_state(
    platform: str,
    *,
    code_verifier: str | None = None,
    extra: dict[str, Any] | None = None,
) -> _SignedState:
    now = int(time.time())
    claims = OAuthStateClaims(
        jti=secrets.token_urlsafe(24),
        platform=platform,
        iat=now,
        exp=now + OAUTH_STATE_TTL_SECONDS,
        code_verifier=code_verifier,
        extra=dict(extra or {}),
    )
    body = {
        "v": 1,
        "jti": claims.jti,
        "p": claims.platform,
        "iat": claims.iat,
        "exp": claims.exp,
    }
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = f"{_b64encode(payload)}.{_sign_payload(payload)}"
    record = {
        "platform": platform,
        "exp": claims.exp,
        "used": False,
        "code_verifier": encrypt_secret(code_verifier) if code_verifier else None,
        "verifier_encrypted": bool(code_verifier),
        "extra": claims.extra,
    }
    return _SignedState(token=token, claims=claims, record=record)


def _parse_signed_state(state: str) -> dict[str, Any]:
    if not state or "." not in state:
        raise OAuthStateError("OAuth state is malformed")
    encoded, _, provided = state.partition(".")
    try:
        payload = _b64decode(encoded)
    except Exception as exc:
        raise OAuthStateError("OAuth state is malformed") from exc
    expected = _sign_payload(payload)
    if not hmac.compare_digest(provided, expected):
        raise OAuthStateError("OAuth state signature is invalid")
    try:
        body = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OAuthStateError("OAuth state is malformed") from exc
    if not isinstance(body, dict):
        raise OAuthStateError("OAuth state is malformed")
    exp = int(body.get("exp") or 0)
    if exp <= int(time.time()):
        raise OAuthStateError("OAuth state has expired")
    return body


def _claims_from_parts(body: dict[str, Any], record: dict[str, Any]) -> OAuthStateClaims:
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    verifier = record.get("code_verifier")
    if isinstance(verifier, str) and verifier and record.get("verifier_encrypted"):
        try:
            verifier = decrypt_secret(verifier)
        except ValueError as exc:
            raise OAuthStateError("OAuth PKCE verifier is unreadable") from exc
    elif not isinstance(verifier, str):
        verifier = None
    return OAuthStateClaims(
        jti=str(body["jti"]),
        platform=str(body["p"]),
        iat=int(body["iat"]),
        exp=int(body["exp"]),
        code_verifier=verifier,
        extra=extra,
    )


async def create_oauth_state(
    platform: str,
    *,
    session: AsyncSession | None = None,
    code_verifier: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    signed = _build_signed_state(platform, code_verifier=code_verifier, extra=extra)
    redis_ok = _redis_put(signed.claims.jti, signed.record)
    _memory_put(signed.claims.jti, signed.record)
    if not redis_ok:
        if session is not None:
            await _db_put(session, signed.claims.jti, signed.record)
            logger.info("oauth_state_issued", platform=platform, store="memory_db")
        else:
            logger.info("oauth_state_issued", platform=platform, store="memory")
    else:
        logger.info("oauth_state_issued", platform=platform, store="redis")
    return signed.token


async def verify_oauth_state(
    state: str,
    *,
    platform: str,
    session: AsyncSession | None = None,
) -> OAuthStateClaims:
    body = _parse_signed_state(state)
    if body.get("p") != platform:
        raise OAuthStateError("OAuth state platform mismatch")
    jti = str(body.get("jti") or "")
    if not jti:
        raise OAuthStateError("OAuth state is malformed")

    record: dict[str, Any] | None = None
    replayed = False

    redis_result = _redis_consume(jti, platform)
    if redis_result is False:
        replayed = True
    elif isinstance(redis_result, dict):
        record = redis_result

    memory_result = _memory_consume(jti, platform)
    if memory_result is False:
        replayed = True
    elif isinstance(memory_result, dict) and record is None:
        record = memory_result

    if record is None and session is not None:
        db_result = await _db_consume(session, jti, platform)
        if db_result is False:
            replayed = True
        elif isinstance(db_result, dict):
            record = db_result

    if replayed and record is None:
        raise OAuthStateError("OAuth state has already been used")
    if record is None:
        raise OAuthStateError("OAuth state is unknown or has already been used")
    return _claims_from_parts(body, record)
