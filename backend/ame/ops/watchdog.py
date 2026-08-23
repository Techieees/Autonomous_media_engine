"""Autonomous recovery: stuck leases, dead jobs, quarantine, platform isolation."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import ContentStatus, JobName, JobStatus, NotificationKind
from ame.db.models import ContentItem, Job, PlatformConnection, SystemEvent
from ame.jobs.queue import JobQueue
from ame.jobs.queue import enqueue
from ame.ops.notifications import notify


async def handle_watchdog(session: AsyncSession, job: Job) -> None:
    recovered = await JobQueue(session).recover_stuck()
    quarantined = await _quarantine_dead(session)
    await _requeue_inflight(session, job)
    if recovered:
        session.add(
            SystemEvent(
                name="ops.job_recovered",
                payload={"recovered": recovered, "source": "watchdog"},
            )
        )
    if quarantined:
        session.add(
            SystemEvent(
                name="ops.quarantined",
                payload={"count": quarantined},
            )
        )
    dead = int(
        (
            await session.scalar(
                select(Job.id).where(Job.status == JobStatus.DEAD.value).limit(8)
            )
            is not None
        )
    )
    if quarantined >= 3:
        await notify(
            session,
            NotificationKind.CRITICAL_SYSTEM_FAILURE,
            "Repeated job failures quarantined",
            f"{quarantined} items were quarantined after bounded retries.",
        )
    _ = dead
    await session.flush()


async def _quarantine_dead(session: AsyncSession) -> int:
    dead_jobs = list(
        (
            await session.execute(
                select(Job).where(Job.status == JobStatus.DEAD.value, Job.dead_letter.is_(True))
            )
        ).scalars()
    )
    count = 0
    for dead in dead_jobs:
        if dead.content_id is None:
            continue
        content = await session.get(ContentItem, dead.content_id)
        if content is None:
            continue
        if content.status in {
            ContentStatus.FAILED.value,
            ContentStatus.REJECTED.value,
            ContentStatus.LEARNING_COMPLETE.value,
        }:
            continue
        content.status = ContentStatus.FAILED.value
        content.failure_reason = (dead.last_error or "quarantined after bounded retries")[:4000]
        count += 1
    return count


async def _requeue_inflight(session: AsyncSession, job: Job) -> None:
    items = list(
        (
            await session.execute(
                select(ContentItem)
                .where(
                    ContentItem.status.in_(
                        [
                            ContentStatus.APPROVED_FOR_RESEARCH.value,
                            ContentStatus.RESEARCHED.value,
                            ContentStatus.SCRIPTING.value,
                            ContentStatus.SCRIPT_SELECTED.value,
                            ContentStatus.PRODUCTION.value,
                            ContentStatus.QA.value,
                            ContentStatus.APPROVED.value,
                            ContentStatus.PUBLISHING.value,
                            ContentStatus.PUBLISHED.value,
                            ContentStatus.MEASURING.value,
                        ]
                    )
                )
                .limit(25)
            )
        ).scalars()
    )
    for item in items:
        await enqueue(
            session,
            JobName.PIPELINE_ADVANCE.value,
            {"content_id": str(item.id), "source": "watchdog"},
            idempotency_key=f"advance:{item.id}:{item.status}",
            content_id=item.id,
            workflow_id=item.workflow_id,
            correlation_id=job.correlation_id,
        )


async def isolate_unhealthy_platforms(session: AsyncSession) -> list[str]:
    rows = list((await session.execute(select(PlatformConnection))).scalars())
    isolated: list[str] = []
    for row in rows:
        meta = dict(row.metadata_json or {})
        failures = int(meta.get("recent_publish_failures") or 0)
        if failures >= 5 and row.state not in {"restricted", "disabled"}:
            row.state = "restricted"
            isolated.append(row.platform)
    return isolated
