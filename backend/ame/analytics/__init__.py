from typing import Any

__all__ = ["distributions", "handle_analytics_snapshot", "overview_metrics"]


def __getattr__(name: str) -> Any:
    if name == "handle_analytics_snapshot":
        from ame.analytics.service import handle_analytics_snapshot

        return handle_analytics_snapshot
    if name in {"overview_metrics", "distributions"}:
        from ame.analytics.queries import distributions, overview_metrics

        return overview_metrics if name == "overview_metrics" else distributions
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
