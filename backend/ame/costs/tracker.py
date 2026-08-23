from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ame.config import get_settings
from ame.db.models import CostEvent, ContentItem
from ame.db.session import sync_session_factory


class BudgetExceeded(Exception):
    def __init__(self, kind: str, spent: float, limit: float) -> None:
        self.kind = kind
        self.spent = spent
        self.limit = limit
        super().__init__(f"paused_by_budget:{kind}:{spent:.4f}/{limit:.4f}")


def _day_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def spend_today(session: AsyncSession, kind: str | None = None) -> float:
    stmt: Select[tuple[float]] = select(func.coalesce(func.sum(CostEvent.estimated_cost), 0))
    stmt = stmt.where(CostEvent.created_at >= _day_start())
    if kind:
        stmt = stmt.where(CostEvent.kind == kind)
    result = await session.execute(stmt)
    return float(result.scalar_one())


_NOT_YET_PRODUCED = frozenset({"discovered", "scored"})


async def produced_today(session: AsyncSession) -> int:
    stmt = select(func.count(ContentItem.id)).where(
        ContentItem.created_at >= _day_start(),
        ContentItem.status.notin_(_NOT_YET_PRODUCED),
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def assert_budget(session: AsyncSession, *, kind: str = "ai", extra: float = 0.0) -> None:
    settings = get_settings()
    if kind == "ai":
        spent = await spend_today(session, "ai")
        limit = settings.daily_ai_spend_limit
        if spent + extra > limit:
            raise BudgetExceeded("ai", spent, limit)
    if kind == "media":
        spent = await spend_today(session, "media")
        limit = settings.daily_media_spend_limit
        if spent + extra > limit:
            raise BudgetExceeded("media", spent, limit)
    total = await spend_today(session)
    if total + extra > settings.daily_cost_limit:
        raise BudgetExceeded("total", total, settings.daily_cost_limit)
    count = await produced_today(session)
    if count >= settings.max_content_per_day:
        raise BudgetExceeded("content", float(count), float(settings.max_content_per_day))


async def record_cost(
    session: AsyncSession,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    *,
    job: str | None = None,
    agent: str | None = None,
    content_id: UUID | None = None,
    kind: str = "ai",
) -> CostEvent:
    event = CostEvent(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=estimated_cost,
        job=job,
        agent=agent,
        content_id=content_id,
        kind=kind,
    )
    session.add(event)
    await session.flush()
    return event


def record_cost_sync(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    *,
    job: str | None = None,
    agent: str | None = None,
    content_id: UUID | None = None,
    kind: str = "ai",
) -> None:
    try:
        with sync_session_factory() as session:
            session.add(
                CostEvent(
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost=estimated_cost,
                    job=job,
                    agent=agent,
                    content_id=content_id,
                    kind=kind,
                )
            )
            session.commit()
    except Exception:
        return


def next_backoff(attempts: int) -> timedelta:
    seconds = min(300, 2 ** max(attempts, 1))
    return timedelta(seconds=seconds)
