from typing import Any

__all__ = ["handle_revenue_sync", "revenue_overview"]


def __getattr__(name: str) -> Any:
    if name == "handle_revenue_sync":
        from ame.revenue.service import handle_revenue_sync

        return handle_revenue_sync
    if name == "revenue_overview":
        from ame.revenue.queries import revenue_overview

        return revenue_overview
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
