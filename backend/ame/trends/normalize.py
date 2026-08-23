from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from ame.contracts.schemas import TrendSignalIn

UNKNOWN_AGE_HOURS = 12.0


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_text(value: str | None, limit: int) -> str:
    return (value or "").strip()[:limit]


def derive_topic(title: str) -> str:
    cleaned = title.strip()
    for prefix in ("Show HN:", "Ask HN:", "Tell HN:", "Launch HN:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return clamp_text(cleaned, 300)


def stable_external_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty external id")
    if len(text) <= 200:
        return text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def host_of(url: str) -> str:
    return urlparse(url).netloc or "unknown"


def utc_from_unix(timestamp: Any) -> datetime | None:
    seconds = optional_float(timestamp)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def utc_from_struct(parsed: Any) -> datetime | None:
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def utc_from_isoformat(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def age_hours_between(published_at: datetime, now: datetime) -> float:
    return max((now - published_at).total_seconds() / 3600.0, 0.0)


def finalize_signal(signal: TrendSignalIn, now: datetime) -> TrendSignalIn:
    published = signal.published_at
    if published is not None and published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    age = signal.age_hours
    if published is None and age > 0:
        published = now - timedelta(hours=age)
    if published is not None:
        age = age_hours_between(published, now)
    else:
        age = UNKNOWN_AGE_HOURS
        published = now - timedelta(hours=age)
    return signal.model_copy(update={"published_at": published, "age_hours": age})


def velocity_from_volume(volume: int | float | None, age_hours: float) -> float:
    if volume is None or volume <= 0:
        return 0.0
    return float(volume) / max(age_hours, 0.25)


def engagement_rate(likes: int | None, comments: int | None, views: int | None) -> float:
    interactions = (likes or 0) + (comments or 0)
    if views and views > 0:
        return interactions / views
    if likes and likes > 0:
        return (comments or 0) / likes
    return 0.0
