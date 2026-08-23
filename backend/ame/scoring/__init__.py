from ame.scoring.scorer import FEATURE_WEIGHTS, SCORE_FORMULA, score_signal

__all__ = [
    "FEATURE_WEIGHTS",
    "SCORE_FORMULA",
    "handle_opportunity_score",
    "score_signal",
]


def __getattr__(name: str):
    if name == "handle_opportunity_score":
        from ame.scoring.service import handle_opportunity_score

        return handle_opportunity_score
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
