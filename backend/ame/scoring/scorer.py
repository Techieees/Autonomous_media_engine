from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from ame.contracts.schemas import OpportunityScore

# Weighted total (weights sum to 1.0). copyright_safety = (1 - copyright_risk).
# score = 0.15*velocity + 0.12*recency + 0.12*engagement + 0.10*corroboration
#       + 0.10*novelty + 0.08*shelf_life + 0.12*relevance
#       + 0.08*factual_confidence + 0.05*cost_score + 0.08*(1 - copyright_risk)
FEATURE_WEIGHTS: dict[str, float] = {
    "velocity": 0.15,
    "recency": 0.12,
    "engagement": 0.12,
    "corroboration": 0.10,
    "novelty": 0.10,
    "shelf_life": 0.08,
    "relevance": 0.12,
    "factual_confidence": 0.08,
    "cost_score": 0.05,
    "copyright_safety": 0.08,
}

SCORE_FORMULA = (
    "score = 0.15*velocity + 0.12*recency + 0.12*engagement + 0.10*corroboration"
    " + 0.10*novelty + 0.08*shelf_life + 0.12*relevance"
    " + 0.08*factual_confidence + 0.05*cost_score + 0.08*(1 - copyright_risk)"
)

assert abs(sum(FEATURE_WEIGHTS.values()) - 1.0) < 1e-12

RECENCY_HORIZON_HOURS = 48.0
SHELF_LIFE_TAU_HOURS = 36.0
VELOCITY_SATURATION = 100.0
ENGAGEMENT_RATE_SATURATION = 0.10
CORROBORATION_SATURATION = 3.0
COST_WORD_SOFT_MAX = 16.0
DEFAULT_RELEVANCE = 0.6
COPYRIGHT_HIGH = 0.95
COPYRIGHT_BASE = 0.08
COPYRIGHT_PATTERN = re.compile(r"full movie|\bleak\b|\bdownload\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "from",
        "with",
        "by",
        "at",
        "is",
        "are",
        "as",
        "new",
        "how",
        "why",
        "what",
    }
)


def score_signal(
    signal: Any,
    existing_topics: list[str] | tuple[str, ...] | None = None,
    active_niches: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Deterministic OpportunityScore-like dict. Scores never come from an LLM."""
    fields = _signal_fields(signal)
    meta = fields["metadata"]
    topics = list(existing_topics) if existing_topics is not None else list(
        meta.get("existing_topics") or []
    )
    niches = list(active_niches) if active_niches is not None else list(
        meta.get("active_niches") or []
    )
    if meta.get("similar_topic_exists") and fields["topic"] not in topics:
        topics.append(fields["topic"])

    velocity = _velocity_score(fields["velocity"])
    recency = _recency_score(fields["age_hours"])
    engagement = _engagement_score(
        fields["engagement_rate"],
        fields["views"],
        fields["likes"],
        fields["comments"],
    )
    corroboration = _corroboration_score(fields["cross_platform_count"])
    novelty, similar_topic = _novelty_score(fields["topic"], fields["title"], topics)
    shelf_life = _shelf_life_score(fields["age_hours"])
    relevance, matched_niche = _relevance_score(fields["topic"], fields["title"], niches)
    factual_confidence = _factual_confidence(fields["risk_score"])
    cost = _cost_score(fields["topic"], fields["title"], meta)
    copyright_risk, copyright_hit = _copyright_risk(fields["title"], fields["topic"])

    features_unit = {
        "velocity": velocity,
        "recency": recency,
        "engagement": engagement,
        "corroboration": corroboration,
        "novelty": novelty,
        "shelf_life": shelf_life,
        "relevance": relevance,
        "factual_confidence": factual_confidence,
        "cost_score": cost,
        "copyright_risk": copyright_risk,
    }
    total = _weighted_total(features_unit)
    explanation = _explanation(
        total,
        features_unit,
        fields["title"] or fields["topic"],
        copyright_hit,
        matched_niche,
        similar_topic,
    )
    features: dict[str, Any] = {
        **features_unit,
        "weights": dict(FEATURE_WEIGHTS),
        "formula": SCORE_FORMULA,
        "copyright_hit": copyright_hit,
        "matched_niche": matched_niche,
        "similar_topic": similar_topic,
        "raw": {
            "velocity": fields["velocity"],
            "age_hours": fields["age_hours"],
            "engagement_rate": fields["engagement_rate"],
            "views": fields["views"],
            "likes": fields["likes"],
            "comments": fields["comments"],
            "cross_platform_count": fields["cross_platform_count"],
            "risk_score": fields["risk_score"],
            "topic": fields["topic"],
            "title": fields["title"],
        },
    }
    scored = OpportunityScore(
        trend_signal_id=fields["trend_signal_id"],
        score=total,
        velocity_score=velocity,
        recency_score=recency,
        engagement_score=engagement,
        corroboration_score=corroboration,
        novelty_score=novelty,
        shelf_life_score=shelf_life,
        relevance_score=relevance,
        factual_confidence=factual_confidence,
        cost_score=cost,
        copyright_risk=copyright_risk,
        explanation=explanation,
        features=features,
    )
    return scored.model_dump()


def weighted_total(features: dict[str, float]) -> float:
    return _weighted_total(features)


def _weighted_total(features: dict[str, float]) -> float:
    safety = 1.0 - _clamp01(features["copyright_risk"])
    raw = (
        FEATURE_WEIGHTS["velocity"] * features["velocity"]
        + FEATURE_WEIGHTS["recency"] * features["recency"]
        + FEATURE_WEIGHTS["engagement"] * features["engagement"]
        + FEATURE_WEIGHTS["corroboration"] * features["corroboration"]
        + FEATURE_WEIGHTS["novelty"] * features["novelty"]
        + FEATURE_WEIGHTS["shelf_life"] * features["shelf_life"]
        + FEATURE_WEIGHTS["relevance"] * features["relevance"]
        + FEATURE_WEIGHTS["factual_confidence"] * features["factual_confidence"]
        + FEATURE_WEIGHTS["cost_score"] * features["cost_score"]
        + FEATURE_WEIGHTS["copyright_safety"] * safety
    )
    return _round01(raw)


def _velocity_score(raw: float) -> float:
    if raw < 0:
        return 0.0
    if raw <= 1.0:
        return _round01(raw)
    return _round01(raw / VELOCITY_SATURATION)


def _recency_score(age_hours: float) -> float:
    return _round01(1.0 - max(age_hours, 0.0) / RECENCY_HORIZON_HOURS)


def _shelf_life_score(age_hours: float) -> float:
    return _round01(1.0 / (1.0 + max(age_hours, 0.0) / SHELF_LIFE_TAU_HOURS))


def _engagement_score(
    engagement_rate: float,
    views: int | None,
    likes: int | None,
    comments: int | None,
) -> float:
    rate = engagement_rate
    if rate <= 0 and views and views > 0:
        interactions = float((likes or 0) + (comments or 0))
        rate = interactions / float(views)
    if rate < 0:
        return 0.0
    if rate > 1.0:
        return _round01(rate / 100.0)
    return _round01(rate / ENGAGEMENT_RATE_SATURATION)


def _corroboration_score(cross_platform_count: int) -> float:
    return _round01(max(cross_platform_count, 0) / CORROBORATION_SATURATION)


def _novelty_score(
    topic: str, title: str, existing_topics: list[str]
) -> tuple[float, str | None]:
    if not existing_topics:
        return 1.0, None
    candidate = _tokenize(f"{topic} {title}")
    if not candidate:
        return 0.7, None
    best = 0.0
    match: str | None = None
    for existing in existing_topics:
        overlap = _jaccard(candidate, _tokenize(existing))
        if overlap > best:
            best = overlap
            match = existing
    return _round01(1.0 - best), match if best >= 0.35 else None


def _relevance_score(
    topic: str, title: str, niches: list[str]
) -> tuple[float, str | None]:
    if not niches:
        return DEFAULT_RELEVANCE, None
    text = f"{topic} {title}".lower()
    tokens = _tokenize(text)
    best = 0.0
    matched: str | None = None
    for niche in niches:
        label = (niche or "").strip()
        if not label:
            continue
        lowered = label.lower()
        if lowered in text:
            return 1.0, label
        niche_tokens = _tokenize(lowered)
        if not niche_tokens:
            continue
        overlap = len(tokens & niche_tokens) / len(niche_tokens)
        if overlap > best:
            best = overlap
            matched = label
    if best <= 0:
        return 0.25, None
    return _round01(best), matched


def _factual_confidence(risk_score: float) -> float:
    return _round01(1.0 - _clamp01(risk_score))


def _cost_score(topic: str, title: str, metadata: dict[str, Any]) -> float:
    words = max(1, len((topic or "").split()))
    from_length = _clamp01(1.0 - (words - 2) / COST_WORD_SOFT_MAX)
    estimated = metadata.get("estimated_cost")
    if estimated is not None:
        try:
            from_cost = _clamp01(1.0 - float(estimated) / 2.0)
            from_length = min(from_length, from_cost)
        except (TypeError, ValueError):
            pass
    duration = metadata.get("estimated_duration")
    if duration is not None:
        try:
            from_duration = _clamp01(1.0 - (float(duration) - 20.0) / 70.0)
            from_length = (from_length + from_duration) / 2.0
        except (TypeError, ValueError):
            pass
    title_penalty = 0.0 if len(title or "") < 180 else 0.15
    return _round01(max(0.15, from_length - title_penalty))


def _copyright_risk(title: str, topic: str) -> tuple[float, bool]:
    haystack = f"{title} {topic}"
    if COPYRIGHT_PATTERN.search(haystack):
        return COPYRIGHT_HIGH, True
    return COPYRIGHT_BASE, False


def _explanation(
    score: float,
    features: dict[str, float],
    title: str,
    copyright_hit: bool,
    matched_niche: str | None,
    similar_topic: str | None,
) -> str:
    relevance_note = (
        f"matched niche '{matched_niche}'"
        if matched_niche
        else "keyword overlap with active niches; default 0.60 when none are active"
    )
    novelty_note = (
        f"similar existing topic '{similar_topic}'"
        if similar_topic
        else "no similar ContentItem topic"
    )
    copyright_note = (
        "high — title or topic matched full movie|leak|download"
        if copyright_hit
        else "low — no full movie/leak/download markers"
    )
    return (
        f"Opportunity score {score:.2f} for '{title}'. "
        f"Velocity {features['velocity']:.2f} (momentum, not lifetime views). "
        f"Recency {features['recency']:.2f} (newer is better). "
        f"Engagement {features['engagement']:.2f}. "
        f"Corroboration {features['corroboration']:.2f} (cross_platform_count). "
        f"Novelty {features['novelty']:.2f} ({novelty_note}). "
        f"Shelf life {features['shelf_life']:.2f} (inverse age). "
        f"Relevance {features['relevance']:.2f} ({relevance_note}). "
        f"Factual confidence {features['factual_confidence']:.2f} (inverse risk_score). "
        f"Cost {features['cost_score']:.2f} (cheaper/shorter topics score higher). "
        f"Copyright risk {features['copyright_risk']:.2f} ({copyright_note}). "
        f"Weighted total: {SCORE_FORMULA}."
    )


def _signal_fields(signal: Any) -> dict[str, Any]:
    meta = _metadata(signal)
    topic = str(_get(signal, "topic", default="") or meta.get("topic") or "untitled")
    title = str(_get(signal, "title", default="") or topic)
    raw_id = _get(signal, "id") or _get(signal, "trend_signal_id")
    if raw_id is None:
        trend_id = UUID(int=0)
    elif isinstance(raw_id, UUID):
        trend_id = raw_id
    else:
        try:
            trend_id = UUID(str(raw_id))
        except ValueError:
            trend_id = uuid4()
    return {
        "trend_signal_id": trend_id,
        "topic": topic[:300],
        "title": title[:500],
        "velocity": _as_float(_get(signal, "velocity", default=0.0), 0.0),
        "engagement_rate": _as_float(_get(signal, "engagement_rate", default=0.0), 0.0),
        "age_hours": _as_float(_get(signal, "age_hours", default=0.0), 0.0),
        "cross_platform_count": int(_as_float(_get(signal, "cross_platform_count", default=1), 1)),
        "risk_score": _as_float(_get(signal, "risk_score", default=0.1), 0.1),
        "views": _as_optional_int(_get(signal, "views")),
        "likes": _as_optional_int(_get(signal, "likes")),
        "comments": _as_optional_int(_get(signal, "comments")),
        "metadata": meta,
    }


def _metadata(signal: Any) -> dict[str, Any]:
    if isinstance(signal, dict):
        raw = signal.get("metadata") or signal.get("metadata_json") or {}
        return dict(raw) if isinstance(raw, dict) else {}
    raw = getattr(signal, "metadata_json", None)
    if raw is None:
        raw = getattr(signal, "metadata", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _get(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    if hasattr(signal, name):
        value = getattr(signal, name)
        return default if value is None else value
    return default


def _tokenize(text: str) -> set[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return {tok for tok in tokens if len(tok) > 2 and tok not in _STOPWORDS}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _round01(value: float) -> float:
    return round(_clamp01(value), 6)


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
