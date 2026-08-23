from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from ame.db.dialect import upsert_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.checkpoints import checkpoint_run_after, next_checkpoint, parse_checkpoint
from ame.analytics.ids import as_uuid
from ame.analytics.platform import fetch_real_metrics
from ame.analytics.synthetic import generate_synthetic_metrics
from ame.config import get_settings
from ame.contracts.enums import ContentStatus, JobName, MetricCheckpoint
from ame.db.models import ContentItem, Job, MetricSnapshot, Publication
from ame.jobs.queue import enqueue
from ame.observability import get_logger

logger = get_logger("ame.analytics.snapshot")

_MEASURING_FROM = {
    ContentStatus.PUBLISHED.value,
    ContentStatus.PUBLISHING.value,
    ContentStatus.MEASURING.value,
}


@dataclass
class SnapshotOutcome:
    snapshot: MetricSnapshot | None
    created: bool
    publication: Publication | None
    learning_enqueued: bool
    next_checkpoint: str | None
    simulation: bool
    source: str
    skipped_reason: str | None = None


async def load_publication(session: AsyncSession, payload: dict[str, Any], job: Job) -> Publication:
    publication_id = as_uuid(payload.get("publication_id"))
    if publication_id:
        publication = await session.get(Publication, publication_id)
        if publication is None:
            raise RuntimeError(f"publication_not_found:{publication_id}")
        return publication
    content_id = job.content_id or as_uuid(payload.get("content_id"))
    if content_id is None:
        raise RuntimeError("analytics_snapshot_missing_publication")
    result = await session.execute(
        select(Publication)
        .where(Publication.content_id == content_id)
        .order_by(Publication.created_at.desc())
    )
    publication = result.scalars().first()
    if publication is None:
        raise RuntimeError(f"publication_not_found_for_content:{content_id}")
    return publication


async def _existing_snapshot(
    session: AsyncSession, publication_id: UUID, checkpoint: str
) -> MetricSnapshot | None:
    result = await session.execute(
        select(MetricSnapshot).where(
            MetricSnapshot.publication_id == publication_id,
            MetricSnapshot.checkpoint == checkpoint,
        )
    )
    return result.scalar_one_or_none()


async def _insert_snapshot(session: AsyncSession, values: dict[str, Any]) -> MetricSnapshot:
    stmt = (
        upsert_insert(MetricSnapshot, session)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["publication_id", "checkpoint"])
        .returning(MetricSnapshot.id)
    )
    result = await session.execute(stmt)
    snapshot_id = result.scalar_one_or_none()
    if snapshot_id is None:
        existing = await _existing_snapshot(
            session, values["publication_id"], values["checkpoint"]
        )
        if existing is None:
            raise RuntimeError("metric_snapshot_conflict_unreadable")
        return existing
    loaded = await session.get(MetricSnapshot, snapshot_id)
    if loaded is None:
        raise RuntimeError("metric_snapshot_insert_unreadable")
    return loaded


async def _enqueue_next(
    session: AsyncSession,
    job: Job,
    publication: Publication,
    current: str,
) -> str | None:
    upcoming = next_checkpoint(current)
    if upcoming is None:
        return None
    await enqueue(
        session,
        JobName.ANALYTICS_SNAPSHOT.value,
        {
            "publication_id": str(publication.id),
            "content_id": str(publication.content_id),
            "checkpoint": upcoming,
        },
        idempotency_key=f"analytics:{publication.id}:{upcoming}",
        content_id=publication.content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
        run_after=checkpoint_run_after(publication.created_at, upcoming),
    )
    return upcoming


async def _enqueue_learning(
    session: AsyncSession,
    job: Job,
    publication: Publication,
    checkpoint: str,
    *,
    trigger: str,
) -> None:
    await enqueue(
        session,
        JobName.LEARNING_UPDATE.value,
        {
            "publication_id": str(publication.id),
            "content_id": str(publication.content_id),
            "checkpoint": checkpoint,
            "trigger": trigger,
        },
        idempotency_key=f"learning:{publication.id}:{trigger}",
        content_id=publication.content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )


async def _mark_measuring(session: AsyncSession, content_id: UUID) -> None:
    content = await session.get(ContentItem, content_id)
    if content is None:
        return
    if content.status in _MEASURING_FROM:
        content.status = ContentStatus.MEASURING.value
        await session.flush()


async def take_snapshot(session: AsyncSession, job: Job) -> SnapshotOutcome:
    payload = dict(job.payload or {})
    publication = await load_publication(session, payload, job)
    checkpoint = parse_checkpoint(payload.get("checkpoint"))
    prior = await _existing_snapshot(session, publication.id, checkpoint)
    settings = get_settings()
    dry_run = bool(settings.dry_run or publication.simulation)

    if prior is None:
        if publication.simulation:
            generated = generate_synthetic_metrics(publication.id, checkpoint)
            values = {
                "id": uuid4(),
                "publication_id": publication.id,
                "content_id": publication.content_id,
                "platform": publication.platform,
                "checkpoint": checkpoint,
                "views": generated["views"],
                "likes": generated["likes"],
                "comments": generated["comments"],
                "shares": generated["shares"],
                "watch_time_seconds": generated["watch_time_seconds"],
                "completion_rate": generated["completion_rate"],
                "followers_gained": generated["followers_gained"],
                "raw": generated["raw"],
                "simulation": True,
            }
            source = "synthetic"
        else:
            normalized = await fetch_real_metrics(session, publication)
            values = {
                "id": uuid4(),
                "publication_id": publication.id,
                "content_id": publication.content_id,
                "platform": publication.platform,
                "checkpoint": checkpoint,
                "views": normalized.views,
                "likes": normalized.likes,
                "comments": normalized.comments,
                "shares": normalized.shares,
                "watch_time_seconds": normalized.watch_time_seconds,
                "completion_rate": normalized.completion_rate,
                "followers_gained": normalized.followers_gained,
                "raw": normalized.raw,
                "simulation": False,
            }
            source = normalized.source
        snapshot = await _insert_snapshot(session, values)
        created = snapshot.id == values["id"]
        if not created:
            source = "existing"
    else:
        snapshot = prior
        created = False
        source = "existing"

    snapshot_count = int(
        await session.scalar(
            select(func.count(MetricSnapshot.id)).where(
                MetricSnapshot.publication_id == publication.id
            )
        )
        or 0
    )

    upcoming = await _enqueue_next(session, job, publication, checkpoint)
    learning = False
    if checkpoint == MetricCheckpoint.H24.value:
        await _enqueue_learning(session, job, publication, checkpoint, trigger="24h")
        learning = True
    elif snapshot_count == 1 and dry_run:
        await _enqueue_learning(
            session, job, publication, checkpoint, trigger="first_dry_run"
        )
        learning = True

    await _mark_measuring(session, publication.content_id)
    logger.info(
        "analytics_snapshot",
        publication_id=str(publication.id),
        checkpoint=checkpoint,
        created=created,
        simulation=publication.simulation,
        source=source,
        at=datetime.now(UTC).isoformat(),
    )
    return SnapshotOutcome(
        snapshot=snapshot,
        created=created,
        publication=publication,
        learning_enqueued=learning,
        next_checkpoint=upcoming,
        simulation=bool(snapshot.simulation if snapshot else publication.simulation),
        source=source,
    )
