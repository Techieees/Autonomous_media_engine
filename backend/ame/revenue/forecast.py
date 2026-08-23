from __future__ import annotations

from typing import Any

from ame.contracts.enums import RevenueKind

FORECAST_NOTE = (
    "Modeled scenario only. kind=forecast. Never summed into actual dashboard totals. "
    "Not a claimed CPM and not platform-reported money."
)


def labeled_forecast(
    views: int,
    *,
    currency: str = "EUR",
    platform: str | None = None,
    simulation: bool = False,
    period: str | None = None,
    rate_per_thousand: float = 0.05,
) -> dict[str, Any]:
    """Optional helper. Output is always forecast, never actual."""
    amount = round(max(0, views) * max(0.0, rate_per_thousand) / 1000.0, 4)
    return {
        "kind": RevenueKind.FORECAST.value,
        "amount": amount,
        "currency": currency,
        "source": "internal_forecast_helper",
        "platform": platform,
        "period": period,
        "simulation": simulation,
        "included_in_actual": False,
        "note": FORECAST_NOTE,
        "inputs": {
            "views": views,
            "rate_per_thousand_is_model_only": True,
            "rate_per_thousand": rate_per_thousand,
        },
    }
