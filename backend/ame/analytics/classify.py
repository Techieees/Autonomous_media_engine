from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ame.contracts.enums import PerformanceClass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class PerformanceThresholds:
    """Internal bands. Viral is not a 1M-view promise."""

    good_views: int = 150
    strong_views: int = 800
    breakout_views: int = 4000
    viral_views: int = 20000
    good_completion: float = 0.35
    strong_completion: float = 0.50
    breakout_share_rate: float = 0.02
    viral_share_rate: float = 0.05
    good_follower_rate: float = 0.004


def load_thresholds() -> PerformanceThresholds:
    return PerformanceThresholds(
        good_views=_env_int("AME_PERF_GOOD_VIEWS", 150),
        strong_views=_env_int("AME_PERF_STRONG_VIEWS", 800),
        breakout_views=_env_int("AME_PERF_BREAKOUT_VIEWS", 4000),
        viral_views=_env_int("AME_PERF_VIRAL_VIEWS", 20000),
        good_completion=_env_float("AME_PERF_GOOD_COMPLETION", 0.35),
        strong_completion=_env_float("AME_PERF_STRONG_COMPLETION", 0.50),
        breakout_share_rate=_env_float("AME_PERF_BREAKOUT_SHARE_RATE", 0.02),
        viral_share_rate=_env_float("AME_PERF_VIRAL_SHARE_RATE", 0.05),
        good_follower_rate=_env_float("AME_PERF_GOOD_FOLLOWER_RATE", 0.004),
    )


def classify_performance(
    *,
    views: int,
    completion_rate: float | None = None,
    shares: int = 0,
    followers_gained: int = 0,
    thresholds: PerformanceThresholds | None = None,
) -> PerformanceClass:
    bands = thresholds or load_thresholds()
    completion = completion_rate or 0.0
    share_rate = shares / views if views > 0 else 0.0
    follower_rate = followers_gained / views if views > 0 else 0.0
    quality = (
        completion >= bands.strong_completion
        or share_rate >= bands.breakout_share_rate
        or follower_rate >= bands.good_follower_rate
    )
    if views >= bands.viral_views and (
        completion >= bands.good_completion or share_rate >= bands.viral_share_rate
    ):
        return PerformanceClass.VIRAL
    if views >= bands.breakout_views and quality:
        return PerformanceClass.BREAKOUT
    if views >= bands.breakout_views:
        return PerformanceClass.STRONG
    if views >= bands.strong_views and completion >= bands.good_completion:
        return PerformanceClass.STRONG
    if views >= bands.good_views or (
        views > 0 and completion >= bands.strong_completion
    ):
        return PerformanceClass.GOOD
    return PerformanceClass.BASELINE


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * weight)


def distribution_block(values: list[float]) -> dict[str, float | None]:
    return {
        "median": percentile(values, 50),
        "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
        "n": len(values),
    }


def thresholds_payload(thresholds: PerformanceThresholds | None = None) -> dict[str, Any]:
    bands = thresholds or load_thresholds()
    return {
        "good_views": bands.good_views,
        "strong_views": bands.strong_views,
        "breakout_views": bands.breakout_views,
        "viral_views": bands.viral_views,
        "note": (
            "Configurable internal classes. Viral is a relative band, "
            "not a promise of 1,000,000 views."
        ),
    }
