from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import RevenueKind
from ame.db.models import ContentItem, MetricSnapshot, RevenueEvent
from ame.revenue.connections import connected_platforms
from ame.revenue.forecast import FORECAST_NOTE


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def day_start(now: datetime | None = None) -> datetime:
    moment = _aware(now or datetime.now(UTC))
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(now: datetime | None = None) -> datetime:
    return day_start(now).replace(day=1)


def _sum_stmt(
    *,
    kind: str,
    simulation: bool | None,
    since: datetime | None = None,
) -> Select[tuple[Any]]:
    stmt = select(func.coalesce(func.sum(RevenueEvent.amount), 0)).where(RevenueEvent.kind == kind)
    if simulation is not None:
        stmt = stmt.where(RevenueEvent.simulation.is_(simulation))
    if since is not None:
        stmt = stmt.where(RevenueEvent.created_at >= since)
    return stmt


async def _amount(
    session: AsyncSession,
    *,
    kind: str,
    simulation: bool | None,
    since: datetime | None = None,
) -> float:
    value = await session.scalar(_sum_stmt(kind=kind, simulation=simulation, since=since))
    return float(value or 0)


async def _has_actual_rows(session: AsyncSession) -> bool:
    value = await session.scalar(
        select(func.count(RevenueEvent.id)).where(
            RevenueEvent.kind == RevenueKind.ACTUAL.value,
            RevenueEvent.simulation.is_(False),
        )
    )
    return int(value or 0) > 0


def _null_if_missing(amount: float, present: bool) -> float | None:
    if not present:
        return None
    return amount


async def revenue_overview(session: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(UTC)
    start = day_start(now)
    week = now - timedelta(days=7)
    month = month_start(now)
    connected = await connected_platforms(session)
    has_actual = await _has_actual_rows(session)

    actual_today = await _amount(
        session, kind=RevenueKind.ACTUAL.value, simulation=False, since=start
    )
    actual_7d = await _amount(
        session, kind=RevenueKind.ACTUAL.value, simulation=False, since=week
    )
    actual_mtd = await _amount(
        session, kind=RevenueKind.ACTUAL.value, simulation=False, since=month
    )
    actual_life = await _amount(session, kind=RevenueKind.ACTUAL.value, simulation=False)

    forecast_today = await _amount(
        session, kind=RevenueKind.FORECAST.value, simulation=None, since=start
    )
    forecast_7d = await _amount(
        session, kind=RevenueKind.FORECAST.value, simulation=None, since=week
    )
    forecast_mtd = await _amount(
        session, kind=RevenueKind.FORECAST.value, simulation=None, since=month
    )
    forecast_life = await _amount(session, kind=RevenueKind.FORECAST.value, simulation=None)

    views = await _actual_views(session)
    per_thousand = None
    per_content = None
    if has_actual and views > 0:
        per_thousand = round((actual_life / views) * 1000.0, 4)
    if has_actual:
        content_count = await session.scalar(
            select(func.count(func.distinct(RevenueEvent.content_id))).where(
                RevenueEvent.kind == RevenueKind.ACTUAL.value,
                RevenueEvent.simulation.is_(False),
                RevenueEvent.content_id.is_not(None),
            )
        )
        if content_count:
            per_content = round(actual_life / int(content_count), 4)

    by_niche = await _actual_by_niche(session) if has_actual else []
    by_platform = await _group_amount(session, RevenueKind.ACTUAL.value, False) if has_actual else []
    forecast_by_platform = await _group_amount(session, RevenueKind.FORECAST.value, None)

    return {
        "actual": {
            "today": _null_if_missing(actual_today, has_actual),
            "d7": _null_if_missing(actual_7d, has_actual),
            "mtd": _null_if_missing(actual_mtd, has_actual),
            "lifetime": _null_if_missing(actual_life, has_actual),
            "per_1000_views": per_thousand,
            "per_content": per_content,
            "by_niche": by_niche,
            "by_platform": by_platform,
            "currency": settings.default_currency,
            "kind": RevenueKind.ACTUAL.value,
            "simulation": False,
            "connected": bool(connected),
        },
        "forecast": {
            "today": forecast_today,
            "d7": forecast_7d,
            "mtd": forecast_mtd,
            "lifetime": forecast_life,
            "by_platform": forecast_by_platform,
            "currency": settings.default_currency,
            "kind": RevenueKind.FORECAST.value,
            "included_in_actual": False,
            "note": FORECAST_NOTE,
        },
        "connected": bool(connected),
        "connected_platforms": [item.platform for item in connected],
        "note": (
            "No connected platform revenue and no explicit business entry. "
            "Actual is null, not a fabricated CPM."
            if not has_actual
            else "Actual totals exclude every forecast row."
        ),
    }


async def _actual_views(session: AsyncSession) -> int:
    result = await session.execute(
        select(MetricSnapshot).where(MetricSnapshot.simulation.is_(False))
    )
    latest: dict[Any, MetricSnapshot] = {}
    for row in result.scalars():
        current = latest.get(row.publication_id)
        if current is None or row.created_at > current.created_at:
            latest[row.publication_id] = row
    return sum(item.views for item in latest.values() if (item.raw or {}).get("source") != "unavailable")


async def _group_amount(
    session: AsyncSession, kind: str, simulation: bool | None
) -> list[dict[str, Any]]:
    stmt = select(
        RevenueEvent.platform,
        func.coalesce(func.sum(RevenueEvent.amount), 0),
    ).where(RevenueEvent.kind == kind)
    if simulation is not None:
        stmt = stmt.where(RevenueEvent.simulation.is_(simulation))
    stmt = stmt.group_by(RevenueEvent.platform)
    rows = (await session.execute(stmt)).all()
    return [{"platform": platform, "amount": float(amount or 0)} for platform, amount in rows]


async def _actual_by_niche(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(ContentItem.niche, func.coalesce(func.sum(RevenueEvent.amount), 0))
        .join(ContentItem, ContentItem.id == RevenueEvent.content_id)
        .where(
            RevenueEvent.kind == RevenueKind.ACTUAL.value,
            RevenueEvent.simulation.is_(False),
        )
        .group_by(ContentItem.niche)
    )
    rows = (await session.execute(stmt)).all()
    return [{"niche": niche, "amount": float(amount or 0)} for niche, amount in rows]
