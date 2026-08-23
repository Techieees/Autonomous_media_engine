"""Persistent publishing calendar with spacing and volume limits."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import CalendarSlotStatus, ContentStatus, JobName, Platform
from ame.db.models import ContentItem, Job, Publication, PublishingCalendarSlot, SystemEvent
from ame.jobs.queue import enqueue
from ame.ops.clock import now_utc, scheduler_fast


MIN_SPACING = timedelta(minutes=90)


async def schedule_publication(
    session: AsyncSession,
    content: ContentItem,
    platform: str,
    *,
    reason: str,
    experiment: str | None = None,
) -> PublishingCalendarSlot:
    existing = await session.execute(
        select(PublishingCalendarSlot).where(
            PublishingCalendarSlot.content_id == content.id,
            PublishingCalendarSlot.platform == platform,
        )
    )
    slot = existing.scalar_one_or_none()
    if slot is not None:
        return slot
    planned = await _next_slot_time(session, platform)
    slot = PublishingCalendarSlot(
        content_id=content.id,
        platform=platform,
        planned_at=planned,
        reason=reason,
        experiment=experiment,
        status=CalendarSlotStatus.PLANNED.value,
        payload={"simulation": bool(content.simulation)},
    )
    session.add(slot)
    session.add(
        SystemEvent(
            name="calendar.planned",
            payload={
                "content_id": str(content.id),
                "platform": platform,
                "planned_at": planned.isoformat(),
            },
            content_id=content.id,
            simulation=content.simulation,
        )
    )
    await session.flush()
    return slot


async def _next_slot_time(session: AsyncSession, platform: str):
    now = now_utc()
    if scheduler_fast():
        return now
    latest = await session.scalar(
        select(func.max(PublishingCalendarSlot.planned_at)).where(
            PublishingCalendarSlot.platform == platform,
            PublishingCalendarSlot.status.in_(
                [CalendarSlotStatus.PLANNED.value, CalendarSlotStatus.DUE.value, CalendarSlotStatus.PUBLISHED.value]
            ),
        )
    )
    if latest is None:
        return now + timedelta(minutes=15)
    candidate = latest + MIN_SPACING
    return candidate if candidate > now else now + timedelta(minutes=5)


async def handle_calendar_tick(session: AsyncSession, job: Job) -> None:
    settings = get_settings()
    now = now_utc()
    due = list(
        (
            await session.execute(
                select(PublishingCalendarSlot).where(
                    PublishingCalendarSlot.status == CalendarSlotStatus.PLANNED.value,
                    PublishingCalendarSlot.planned_at <= now,
                )
            )
        ).scalars()
    )
    published_today = int(
        await session.scalar(
            select(func.count()).select_from(Publication).where(Publication.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))
        )
        or 0
    )
    cap = min(settings.maximum_per_platform, settings.max_content_per_day)
    for slot in due:
        if published_today >= cap:
            slot.status = CalendarSlotStatus.SKIPPED.value
            continue
        content = await session.get(ContentItem, slot.content_id)
        if content is None or content.status in {
            ContentStatus.REJECTED.value,
            ContentStatus.FAILED.value,
            ContentStatus.PAUSED_BY_BUDGET.value,
        }:
            slot.status = CalendarSlotStatus.CANCELLED.value
            continue
        existing_pub = await session.execute(
            select(Publication).where(
                Publication.content_id == slot.content_id,
                Publication.platform == slot.platform,
            )
        )
        if existing_pub.scalar_one_or_none() is not None:
            slot.status = CalendarSlotStatus.PUBLISHED.value
            slot.published_at = now
            continue
        slot.status = CalendarSlotStatus.PUBLISHING.value
        await enqueue(
            session,
            JobName.PUBLISH.value,
            {"content_id": str(slot.content_id), "platform": slot.platform, "calendar_slot_id": str(slot.id)},
            idempotency_key=f"publish:{slot.content_id}:{slot.platform}",
            content_id=slot.content_id,
            correlation_id=job.correlation_id,
        )
        published_today += 1
    await session.flush()


async def mark_slot_published(session: AsyncSession, content_id, platform: str) -> None:
    row = (
        await session.execute(
            select(PublishingCalendarSlot).where(
                PublishingCalendarSlot.content_id == content_id,
                PublishingCalendarSlot.platform == platform,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.status = CalendarSlotStatus.PUBLISHED.value
    row.published_at = now_utc()
    await session.flush()
