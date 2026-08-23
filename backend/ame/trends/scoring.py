from __future__ import annotations

import math

from ame.contracts.schemas import TrendSignalIn

VELOCITY_REF = 100.0
RECENCY_HORIZON_HOURS = 72.0


def clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def norm(value: float, *, cap: float = VELOCITY_REF) -> float:
    """Map a non-negative velocity onto [0, 1] with a stable log cap."""
    if value <= 0 or cap <= 0:
        return 0.0
    return clip(math.log1p(float(value)) / math.log1p(cap))


def recency_feature(age_hours: float) -> float:
    if age_hours <= 0:
        return 1.0
    return clip(1.0 - (float(age_hours) / RECENCY_HORIZON_HOURS))


def compute_trend_score(signal: TrendSignalIn) -> float:
    raw = (
        0.45 * norm(signal.velocity)
        + 0.2 * recency_feature(signal.age_hours)
        + 0.15 * clip(signal.engagement_rate)
        + 0.1 * clip(signal.source_authority)
        - 0.1 * clip(signal.risk_score)
    )
    return clip(raw)
