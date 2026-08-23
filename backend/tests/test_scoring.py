"""Opportunity scoring contract.

Public entry: ``ame.scoring.service.score_signal``.

Documented weighted total (weights sum to 1.0; copyright enters as safety):

    score = 0.15*velocity + 0.12*recency + 0.12*engagement + 0.10*corroboration
          + 0.10*novelty + 0.08*shelf_life + 0.12*relevance
          + 0.08*factual_confidence + 0.05*cost_score
          + 0.08*(1 - copyright_risk)

Each feature is clamped to [0, 1]. Titles matching ``full movie|leak|download``
raise copyright_risk (penalty) and therefore lower the weighted total.
Explanation is a required human-readable string on OpportunityScore.
"""

from __future__ import annotations

from ame.contracts.schemas import OpportunityScore, TrendSignalIn
from ame.scoring.service import FEATURE_WEIGHTS, SCORE_FORMULA, score_signal

_UNIT_FIELDS = (
    "score",
    "velocity_score",
    "recency_score",
    "engagement_score",
    "corroboration_score",
    "novelty_score",
    "shelf_life_score",
    "relevance_score",
    "factual_confidence",
    "cost_score",
    "copyright_risk",
)


def _score(signal: TrendSignalIn, **kwargs) -> dict:
    result = score_signal(signal, **kwargs)
    OpportunityScore.model_validate(result)
    return result


def test_feature_weights_sum_to_one() -> None:
    assert abs(sum(FEATURE_WEIGHTS.values()) - 1.0) < 1e-12
    assert "copyright_safety" in FEATURE_WEIGHTS
    assert "(1 - copyright_risk)" in SCORE_FORMULA


def test_score_is_deterministic(trend_signal: TrendSignalIn) -> None:
    first = _score(trend_signal)
    second = _score(trend_signal)
    assert first == second
    assert first["score"] == second["score"]
    assert first["explanation"] == second["explanation"]


def test_score_and_features_stay_in_unit_interval(trend_signal: TrendSignalIn) -> None:
    result = _score(trend_signal)
    for field in _UNIT_FIELDS:
        value = result[field]
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0, f"{field}={value} outside [0, 1]"
    features = result["features"]
    for name in (
        "velocity",
        "recency",
        "engagement",
        "corroboration",
        "novelty",
        "shelf_life",
        "relevance",
        "factual_confidence",
        "cost_score",
        "copyright_risk",
    ):
        assert 0.0 <= float(features[name]) <= 1.0


def test_copyright_risk_penalty_lowers_score(trend_signal: TrendSignalIn) -> None:
    clean = _score(trend_signal)
    risky_signal = trend_signal.model_copy(
        update={"title": "Watch the full movie leak download of humanoid robots"}
    )
    risky = _score(risky_signal)

    assert risky["copyright_risk"] > clean["copyright_risk"]
    assert risky["copyright_risk"] >= 0.9
    assert clean["copyright_risk"] < 0.2
    assert risky["score"] < clean["score"]
    assert risky["features"]["copyright_hit"] is True
    assert clean["features"]["copyright_hit"] is False


def test_explanation_is_present_and_names_copyright(trend_signal: TrendSignalIn) -> None:
    result = _score(trend_signal)
    explanation = result["explanation"]
    assert isinstance(explanation, str)
    assert explanation.strip()
    assert "copyright" in explanation.lower()
    assert "velocity" in explanation.lower()
    assert SCORE_FORMULA in explanation or "Weighted total" in explanation
