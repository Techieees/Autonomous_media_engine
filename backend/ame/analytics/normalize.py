from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NormalizedMetrics(BaseModel):
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    watch_time_seconds: float = 0.0
    completion_rate: float | None = None
    followers_gained: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)
    simulation: bool = False
    source: str = "unavailable"
    metrics_available: bool = False


_VIEW_KEYS = (
    "views",
    "viewCount",
    "view_count",
    "video_views",
    "videoViews",
    "impressions",
    "statistics.viewCount",
)
_LIKE_KEYS = ("likes", "likeCount", "like_count", "statistics.likeCount")
_COMMENT_KEYS = ("comments", "commentCount", "comment_count", "statistics.commentCount")
_SHARE_KEYS = ("shares", "shareCount", "share_count", "sharesCount")
_WATCH_KEYS = (
    "watch_time_seconds",
    "watchTimeSeconds",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "average_time_watched",
)
_COMPLETION_KEYS = (
    "completion_rate",
    "completionRate",
    "averageViewPercentage",
    "full_video_watched_rate",
    "averageViewPercentage",
)
_FOLLOWER_KEYS = (
    "followers_gained",
    "followersGained",
    "subscribersGained",
    "subscriber_gained",
    "follows",
)


def _dig(raw: dict[str, Any], path: str) -> Any:
    current: Any = raw
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = _dig(raw, key) if "." in key else raw.get(key)
        if value is not None:
            return value
    return None


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _completion(raw: dict[str, Any]) -> float | None:
    value = _as_float(_first(raw, _COMPLETION_KEYS))
    if value is None:
        return None
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def _watch_seconds(raw: dict[str, Any], views: int) -> float:
    minutes = _as_float(raw.get("estimatedMinutesWatched"))
    if minutes is not None and "watch_time_seconds" not in raw and "watchTimeSeconds" not in raw:
        return max(0.0, minutes * 60.0)
    direct = _as_float(_first(raw, ("watch_time_seconds", "watchTimeSeconds")))
    if direct is not None:
        return max(0.0, direct)
    avg = _as_float(_first(raw, ("averageViewDuration", "average_time_watched")))
    if avg is not None:
        return max(0.0, avg * max(views, 0))
    return 0.0


def unavailable_metrics(*, reason: str, platform: str | None = None) -> NormalizedMetrics:
    return NormalizedMetrics(
        source="unavailable",
        metrics_available=False,
        simulation=False,
        raw={
            "source": "unavailable",
            "reason": reason,
            "platform": platform,
            "metrics_available": False,
            "not_invented": True,
            "note": "No official platform metrics were available. Zeros are placeholders, not reach.",
        },
    )


def normalize_platform_metrics(raw: dict[str, Any] | None, *, platform: str) -> NormalizedMetrics:
    if not raw:
        return unavailable_metrics(reason="empty_platform_payload", platform=platform)
    payload = dict(raw)
    nested = payload.get("statistics")
    if isinstance(nested, dict):
        for key, value in nested.items():
            payload.setdefault(key, value)
    views = _as_int(_first(payload, _VIEW_KEYS))
    likes = _as_int(_first(payload, _LIKE_KEYS))
    comments = _as_int(_first(payload, _COMMENT_KEYS))
    shares = _as_int(_first(payload, _SHARE_KEYS))
    followers = _as_int(_first(payload, _FOLLOWER_KEYS))
    completion = _completion(payload)
    watch = _watch_seconds(payload, views)
    available = any(value > 0 for value in (views, likes, comments, shares, followers, watch))
    available = available or completion is not None
    return NormalizedMetrics(
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        watch_time_seconds=round(watch, 2),
        completion_rate=completion,
        followers_gained=followers,
        raw={"platform": platform, "metrics_available": available, "payload": raw},
        simulation=False,
        source="platform",
        metrics_available=available,
    )
