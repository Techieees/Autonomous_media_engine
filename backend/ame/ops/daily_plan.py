"""Persist one daily operating plan per owner-local day."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import DailyPlanStatus, JobName
from ame.db.models import ContentItem, DailyPlan, Job, Opportunity, SystemEvent, TrendSignal
from ame.ops.clock import owner_local_date, owner_zone, scheduler_fast


async def get_active_plan(session: AsyncSession) -> DailyPlan | None:
    local_date = owner_local_date()
    timezone = str(owner_zone())
    result = await session.execute(
        select(DailyPlan).where(
            DailyPlan.local_date == local_date, DailyPlan.timezone == timezone
        )
    )
    return result.scalar_one_or_none()


async def ensure_daily_plan(session: AsyncSession) -> DailyPlan:
    existing = await get_active_plan(session)
    if existing is not None:
        return existing
    settings = get_settings()
    trends = int(await session.scalar(select(func.count()).select_from(TrendSignal)) or 0)
    opportunities = int(await session.scalar(select(func.count()).select_from(Opportunity)) or 0)
    in_flight = int(
        await session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(
                ContentItem.status.notin_(
                    ["rejected", "failed", "learning_complete", "paused_by_budget"]
                )
            )
        )
        or 0
    )
    payload: dict[str, Any] = {
        "target_content": settings.target_daily_content,
        "maximum_content": min(settings.maximum_daily_content, settings.max_content_per_day),
        "ingest_cadence": "fast" if scheduler_fast() else "standard",
        "preferences": {
            "target_duration_s": 40,
            "hook_style": "question",
        },
        "snapshot": {
            "trends": trends,
            "opportunities": opportunities,
            "in_flight": in_flight,
        },
        "work": [
            "trend discovery",
            "opportunity scoring",
            "research and production",
            "qa and publishing",
            "analytics checkpoints",
            "learning and allocation",
        ],
    }
    plan = DailyPlan(
        local_date=owner_local_date(),
        timezone=str(owner_zone()),
        status=DailyPlanStatus.ACTIVE.value,
        payload=payload,
    )
    session.add(plan)
    session.add(
        SystemEvent(
            name="daily_plan.created",
            payload={"local_date": plan.local_date, "timezone": plan.timezone},
        )
    )
    await session.flush()
    return plan


async def update_preferences(session: AsyncSession, updates: dict[str, Any]) -> DailyPlan:
    plan = await ensure_daily_plan(session)
    payload = dict(plan.payload or {})
    prefs = dict(payload.get("preferences") or {})
    prefs.update({key: value for key, value in updates.items() if value is not None})
    payload["preferences"] = prefs
    plan.payload = payload
    await session.flush()
    return plan


async def plan_preferences(session: AsyncSession) -> dict[str, Any]:
    plan = await get_active_plan(session)
    if plan is None:
        return {"target_duration_s": 40, "hook_style": "question"}
    return dict((plan.payload or {}).get("preferences") or {})


async def handle_daily_plan(session: AsyncSession, job: Job) -> None:
    await ensure_daily_plan(session)
    _ = job
