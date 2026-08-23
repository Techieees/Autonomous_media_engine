from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.ids import as_uuid
from ame.config import get_settings
from ame.contracts.enums import RevenueKind
from ame.db.models import MetricSnapshot, RevenueEvent
from ame.observability import get_logger
from ame.revenue.connections import connected_platforms, resolve_adapter
from ame.revenue.forecast import labeled_forecast

logger = get_logger("ame.revenue.sync")


@dataclass
class RevenueSyncOutcome:
    connected: list[str]
    actual_written: int = 0
    forecast_written: int = 0
    business_written: int = 0
    noop: bool = False
    reason: str | None = None
    events: list[str] = field(default_factory=list)


def _period(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.strftime("%Y-%m")


def _decimal(amount: float | int | Decimal) -> Decimal:
    return Decimal(str(round(float(amount), 4)))


async def _exists(
    session: AsyncSession,
    *,
    kind: str,
    source: str,
    period: str | None,
    platform: str | None,
    content_id: UUID | None,
    simulation: bool,
) -> bool:
    stmt = select(RevenueEvent.id).where(
        RevenueEvent.kind == kind,
        RevenueEvent.source == source,
        RevenueEvent.simulation.is_(simulation),
    )
    stmt = stmt.where(RevenueEvent.period == period) if period else stmt.where(RevenueEvent.period.is_(None))
    stmt = (
        stmt.where(RevenueEvent.platform == platform)
        if platform
        else stmt.where(RevenueEvent.platform.is_(None))
    )
    stmt = (
        stmt.where(RevenueEvent.content_id == content_id)
        if content_id
        else stmt.where(RevenueEvent.content_id.is_(None))
    )
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _write(
    session: AsyncSession,
    *,
    kind: str,
    amount: float,
    currency: str,
    source: str,
    platform: str | None,
    content_id: UUID | None,
    period: str | None,
    simulation: bool,
) -> RevenueEvent | None:
    if await _exists(
        session,
        kind=kind,
        source=source,
        period=period,
        platform=platform,
        content_id=content_id,
        simulation=simulation,
    ):
        return None
    event = RevenueEvent(
        id=uuid4(),
        kind=kind,
        amount=_decimal(amount),
        currency=currency,
        source=source,
        platform=platform,
        content_id=content_id,
        period=period,
        simulation=simulation,
    )
    session.add(event)
    await session.flush()
    return event


def _extract_revenue(raw: dict[str, Any]) -> float | None:
    for key in (
        "estimatedRevenue",
        "estimated_revenue",
        "finalized_revenue",
        "revenue",
        "ad_revenue",
        "amount",
    ):
        value = raw.get(key)
        if value is None:
            continue
        try:
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount < 0:
            continue
        return amount
    return None


async def record_business_entry(
    session: AsyncSession, payload: dict[str, Any]
) -> RevenueEvent | None:
    amount = payload.get("amount")
    source = str(payload.get("source") or "").strip()
    if amount is None or not source:
        raise RuntimeError("business_entry_requires_amount_and_source")
    try:
        numeric = float(amount)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("business_entry_invalid_amount") from exc
    if numeric < 0:
        raise RuntimeError("business_entry_negative_amount")
    settings = get_settings()
    return await _write(
        session,
        kind=RevenueKind.ACTUAL.value,
        amount=numeric,
        currency=str(payload.get("currency") or settings.default_currency),
        source=source,
        platform=payload.get("platform"),
        content_id=as_uuid(payload.get("content_id")),
        period=payload.get("period") or _period(),
        simulation=bool(payload.get("simulation", False)),
    )


async def _pull_platform_actuals(
    session: AsyncSession, outcome: RevenueSyncOutcome, period: str
) -> None:
    settings = get_settings()
    for connection in await connected_platforms(session):
        adapter = resolve_adapter(connection.platform)
        if adapter is None:
            continue
        fetcher = getattr(adapter, "fetch_revenue", None)
        raw_rows: list[dict[str, Any]] = []
        if callable(fetcher):
            try:
                fetched = await fetcher(connection, period=period)
                if isinstance(fetched, list):
                    raw_rows = [dict(item) for item in fetched if isinstance(item, dict)]
                elif isinstance(fetched, dict):
                    raw_rows = [dict(fetched)]
            except Exception:  # noqa: BLE001
                logger.exception("revenue_adapter_failed", platform=connection.platform)
                continue
        for raw in raw_rows:
            amount = _extract_revenue(raw)
            if amount is None:
                continue
            written = await _write(
                session,
                kind=RevenueKind.ACTUAL.value,
                amount=amount,
                currency=str(raw.get("currency") or settings.default_currency),
                source=str(raw.get("source") or f"{connection.platform}_official"),
                platform=connection.platform,
                content_id=as_uuid(raw.get("content_id")),
                period=str(raw.get("period") or period),
                simulation=False,
            )
            if written is not None:
                outcome.actual_written += 1


async def _maybe_forecast(session: AsyncSession, payload: dict[str, Any], outcome: RevenueSyncOutcome) -> None:
    if not payload.get("include_forecast"):
        return
    settings = get_settings()
    period = payload.get("period") or _period()
    result = await session.execute(select(MetricSnapshot))
    latest: dict[Any, MetricSnapshot] = {}
    for row in result.scalars():
        current = latest.get(row.publication_id)
        if current is None or row.created_at > current.created_at:
            latest[row.publication_id] = row
    views_actual = sum(item.views for item in latest.values() if not item.simulation)
    views_sim = sum(item.views for item in latest.values() if item.simulation)
    for views, simulation, source in (
        (views_actual, False, "internal_forecast_helper"),
        (views_sim, True, "internal_forecast_helper_simulation"),
    ):
        if views <= 0:
            continue
        forecast = labeled_forecast(
            views,
            currency=settings.default_currency,
            simulation=simulation,
            period=period,
        )
        written = await _write(
            session,
            kind=RevenueKind.FORECAST.value,
            amount=float(forecast["amount"]),
            currency=settings.default_currency,
            source=source,
            platform=None,
            content_id=None,
            period=period,
            simulation=simulation,
        )
        if written is not None:
            outcome.forecast_written += 1


async def sync_revenue(session: AsyncSession, payload: dict[str, Any]) -> RevenueSyncOutcome:
    period = str(payload.get("period") or _period())
    connected = await connected_platforms(session)
    outcome = RevenueSyncOutcome(connected=[item.platform for item in connected])

    business = payload.get("business_entry")
    if isinstance(business, dict):
        written = await record_business_entry(session, business)
        if written is not None:
            outcome.business_written += 1
            outcome.actual_written += 1
            outcome.events.append("revenue.recorded")

    if connected:
        before = outcome.actual_written
        await _pull_platform_actuals(session, outcome, period)
        if outcome.actual_written > before:
            outcome.events.append("revenue.recorded")
    elif not outcome.actual_written:
        outcome.noop = True
        outcome.reason = "no_connected_platforms"
        logger.info("revenue_sync_noop", reason=outcome.reason)

    await _maybe_forecast(session, payload, outcome)
    return outcome
