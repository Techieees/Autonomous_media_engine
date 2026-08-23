"""Schedule analytics checkpoints and mark breakouts without waiting real hours."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.classify import classify_performance, load_thresholds
from ame.contracts.enums import (
    AgentMessageType,
    AgentName,
    JobName,
    MetricCheckpoint,
    NotificationKind,
    PerformanceClass,
    PublishStatus,
)
from ame.db.models import ContentItem, Job, MetricSnapshot, Publication, SystemEvent
from ame.jobs.queue import enqueue
from ame.ops.clock import now_utc, scheduler_fast
from ame.agents.messaging import post_message
from ame.ops.notifications import notify

CHECKPOINT_OFFSETS = {
    MetricCheckpoint.H1.value: timedelta(hours=1),
    MetricCheckpoint.H6.value: timedelta(hours=6),
    MetricCheckpoint.H24.value: timedelta(hours=24),
    MetricCheckpoint.H72.value: timedelta(hours=72),
    MetricCheckpoint.D7.value: timedelta(days=7),
    MetricCheckpoint.D30.value: timedelta(days=30),
}


async def handle_analytics_sweep(session: AsyncSession, job: Job) -> None:
    pubs = list(
        (
            await session.execute(
                select(Publication).where(
                    Publication.status.in_([PublishStatus.PUBLISHED.value, "published"])
                )
            )
        ).scalars()
    )
    now = now_utc()
    for pub in pubs:
        existing = {
            row.checkpoint
            for row in (
                await session.execute(
                    select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)
                )
            ).scalars()
        }
        published_at = pub.created_at or now
        for checkpoint, offset in CHECKPOINT_OFFSETS.items():
            if checkpoint in existing:
                continue
            due = now if scheduler_fast() else published_at + offset
            await enqueue(
                session,
                JobName.ANALYTICS_SNAPSHOT.value,
                {
                    "publication_id": str(pub.id),
                    "content_id": str(pub.content_id),
                    "platform": pub.platform,
                    "checkpoint": checkpoint,
                },
                idempotency_key=f"analytics:{pub.id}:{checkpoint}",
                content_id=pub.content_id,
                correlation_id=job.correlation_id,
                run_after=due,
            )
        await _maybe_mark_breakout(session, pub)
    await session.flush()


async def _maybe_mark_breakout(session: AsyncSession, pub: Publication) -> None:
    snaps = list(
        (
            await session.execute(
                select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)
            )
        ).scalars()
    )
    if not snaps:
        return
    latest = max(snaps, key=lambda item: item.views)
    baseline = await session.scalar(
        select(MetricSnapshot.views).order_by(MetricSnapshot.views.desc()).limit(1)
    )
    klass = classify_performance(
        views=latest.views,
        completion_rate=latest.completion_rate,
        shares=latest.shares,
        followers_gained=latest.followers_gained,
        thresholds=load_thresholds(),
    )
    is_breakout = klass in {PerformanceClass.BREAKOUT, PerformanceClass.VIRAL}
    if not is_breakout and baseline and latest.views >= max(int(baseline) * 3, 50):
        is_breakout = True
    if not is_breakout:
        return
    raw = dict(pub.raw or {})
    if raw.get("breakout"):
        return
    raw["breakout"] = True
    raw["performance_class"] = klass.value
    pub.raw = raw
    session.add(
        SystemEvent(
            name="breakout.detected",
            payload={"publication_id": str(pub.id), "views": latest.views, "class": klass.value},
            content_id=pub.content_id,
            simulation=pub.simulation,
        )
    )
    await post_message(
        session,
        sender=AgentName.ANALYTICS,
        recipient=AgentName.DIRECTOR,
        message_type=AgentMessageType.RECOMMENDATION,
        task="breakout_followup",
        related_entity_type="publication",
        related_entity_id=pub.id,
        content_id=pub.content_id,
        payload={"views": latest.views, "class": klass.value, "duplicate_original": False},
        confidence=0.7,
    )
    content = await session.get(ContentItem, pub.content_id)
    title = content.topic if content is not None else "publication"
    await notify(
        session,
        NotificationKind.MAJOR_BREAKOUT_CONTENT,
        "Breakout content detected",
        f"{title} is substantially outperforming the current baseline.",
        related_entity_type="publication",
        related_entity_id=pub.id,
    )
