from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from ame.db.dialect import upsert_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import (
    ContentStatus,
    HumanActionStatus,
    JobName,
    MetricCheckpoint,
    Platform,
    PublishStatus,
    QAVerdict,
)
from ame.db.models import (
    ContentItem,
    HumanAction,
    Job,
    MediaAsset,
    PlatformConnection,
    Publication,
    PublishingJob,
    QAResult,
    Script,
    SystemEvent,
)
from ame.jobs.queue import enqueue
from ame.observability import get_logger
from ame.publishers.base import PreparedPublish, PublishResult
from ame.publishers.dry_run import DryRunPublisher
from ame.publishers.oauth import load_access_token, token_needs_refresh
from ame.publishers.registry import get_adapter

logger = get_logger("ame.publishers.service")

VIDEO_KINDS = frozenset({"video", "render", "final", "final_video", "mp4", "short", "youtube"})
PUBLISHABLE_CONTENT = frozenset(
    {
        ContentStatus.APPROVED.value,
        ContentStatus.PUBLISHING.value,
        ContentStatus.PUBLISHED.value,
        ContentStatus.QA.value,
    }
)
TERMINAL_PUBLICATION = frozenset(
    {
        PublishStatus.PUBLISHED.value,
        PublishStatus.REJECTED_SIMULATION.value,
    }
)
CHECKPOINT_DELAYS = (
    (MetricCheckpoint.H1, timedelta(0)),
    (MetricCheckpoint.H6, timedelta(hours=6)),
    (MetricCheckpoint.H24, timedelta(hours=24)),
    (MetricCheckpoint.H72, timedelta(hours=72)),
    (MetricCheckpoint.D7, timedelta(days=7)),
    (MetricCheckpoint.D30, timedelta(days=30)),
)


def publish_idempotency_key(content_id: UUID, platform: Platform | str) -> str:
    return f"publish:{content_id}:{platform}"


async def handle_publish(session: AsyncSession, job: Job) -> None:
    settings = get_settings()
    payload = dict(job.payload or {})
    content_id = _as_uuid(payload.get("content_id") or job.content_id)
    if content_id is None:
        logger.info("publish_skipped", reason="content_id_missing", job_id=str(job.id))
        return

    requested = _resolve_platform(payload.get("platform"), settings.dry_run)
    content = await session.get(ContentItem, content_id)
    if content is None:
        logger.info("publish_skipped", reason="content_missing", content_id=str(content_id))
        return

    key = publish_idempotency_key(content_id, requested)
    publishing_job = await _get_or_create_publishing_job(
        session, content_id, requested, key, simulation=settings.dry_run or content.simulation
    )

    existing = await _get_publication(session, content_id, requested)
    if existing is not None and existing.status in TERMINAL_PUBLICATION:
        publishing_job.status = existing.status
        publishing_job.error = None
        event_name = (
            "publication.completed"
            if existing.status == PublishStatus.PUBLISHED.value
            else "publication.queued"
        )
        await _emit(
            session,
            event_name,
            {
                "publication_id": str(existing.id),
                "reused": True,
                "platform": requested.value,
            },
            job,
            content,
            existing.simulation,
        )
        logger.info(
            "publish_idempotent_reuse",
            content_id=str(content_id),
            platform=requested.value,
            publication_id=str(existing.id),
        )
        return

    qa = await _latest_qa(session, content_id)
    if qa is None or qa.verdict != QAVerdict.APPROVED.value:
        reason = "qa_not_approved" if qa is None else f"qa_{qa.verdict}"
        if qa is not None and qa.verdict == QAVerdict.REQUIRES_REVIEW.value:
            await _require_human(
                session,
                publishing_job,
                content,
                job,
                requested,
                PublishStatus.REQUIRES_HUMAN_ACTION,
                title="QA review required before publish",
                instructions=(
                    "A QA result marked this content as requires_review. Complete the review "
                    "in the dashboard. Do not publish rejected or unreviewed media."
                ),
                category="qa",
                reason=reason,
            )
            return
        publishing_job.status = PublishStatus.FAILED.value
        publishing_job.error = reason
        if qa is not None and qa.verdict == QAVerdict.REJECTED.value:
            content.status = ContentStatus.REJECTED.value
        content.failure_reason = reason
        await _emit(
            session,
            "publication.failed",
            {"reason": reason},
            job,
            content,
            content.simulation,
        )
        return

    if content.status not in PUBLISHABLE_CONTENT:
        publishing_job.status = PublishStatus.FAILED.value
        publishing_job.error = f"content_status_{content.status}"
        await _emit(
            session,
            "publication.failed",
            {"reason": publishing_job.error},
            job,
            content,
            content.simulation,
        )
        return

    if await _over_daily_cap(session, requested, settings.maximum_per_platform, existing):
        publishing_job.status = PublishStatus.FAILED.value
        publishing_job.error = "daily_platform_cap"
        content.status = ContentStatus.PAUSED_BY_BUDGET.value
        content.failure_reason = "daily_platform_cap"
        await _emit(
            session,
            "publication.failed",
            {"reason": "daily_platform_cap", "platform": requested.value},
            job,
            content,
            content.simulation,
        )
        return

    connection = await _connection_for(session, requested)
    use_dry_run = settings.dry_run or requested is Platform.DRY_RUN
    if use_dry_run:
        adapter = DryRunPublisher()
    else:
        adapter = get_adapter(
            requested,
            connection=connection,
            access_token=load_access_token(connection),
        )
        if connection is not None and token_needs_refresh(connection):
            await adapter.refresh_auth(connection)

    publishing_job.status = PublishStatus.PROCESSING.value
    content.status = ContentStatus.PUBLISHING.value
    await session.flush()
    await _emit(
        session,
        "publication.queued",
        {"platform": requested.value, "simulation": use_dry_run},
        job,
        content,
        use_dry_run or content.simulation,
    )

    validation = await adapter.validate(content, connection)
    if not validation.ok:
        await _handle_validation_block(
            session, publishing_job, content, job, requested, validation.status, validation.reasons
        )
        return

    asset = await _latest_video_asset(session, content_id)
    if not use_dry_run and asset is None:
        publishing_job.status = PublishStatus.FAILED.value
        publishing_job.error = "media_missing"
        content.status = ContentStatus.FAILED.value
        content.failure_reason = "media_missing"
        await _emit(
            session,
            "publication.failed",
            {"reason": "media_missing"},
            job,
            content,
            False,
        )
        return

    prepared = await adapter.prepare(content, asset)
    prepared = await _enrich_prepared(session, content, prepared, payload, use_dry_run)
    result = await adapter.publish(prepared, idempotency_key=key)
    await _persist_result(
        session,
        publishing_job,
        content,
        job,
        requested,
        prepared,
        result,
        existing,
        use_dry_run,
    )


def _resolve_platform(raw: Any, dry_run: bool) -> Platform:
    if raw in {None, ""}:
        return Platform.DRY_RUN if dry_run else Platform.YOUTUBE
    try:
        return Platform(str(raw))
    except ValueError:
        return Platform.DRY_RUN if dry_run else Platform.YOUTUBE


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


async def _get_or_create_publishing_job(
    session: AsyncSession,
    content_id: UUID,
    platform: Platform,
    key: str,
    *,
    simulation: bool,
) -> PublishingJob:
    existing = await session.execute(
        select(PublishingJob).where(PublishingJob.idempotency_key == key)
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found
    job_id = uuid4()
    stmt = (
        upsert_insert(PublishingJob, session)
        .values(
            id=job_id,
            content_id=content_id,
            platform=platform.value,
            status=PublishStatus.QUEUED.value,
            idempotency_key=key,
            simulation=simulation,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(PublishingJob.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is None:
        found = (
            await session.execute(
                select(PublishingJob).where(PublishingJob.idempotency_key == key)
            )
        ).scalar_one()
        return found
    loaded = await session.execute(select(PublishingJob).where(PublishingJob.id == inserted))
    return loaded.scalar_one()


async def _get_publication(
    session: AsyncSession, content_id: UUID, platform: Platform
) -> Publication | None:
    result = await session.execute(
        select(Publication).where(
            Publication.content_id == content_id,
            Publication.platform == platform.value,
        )
    )
    return result.scalar_one_or_none()


async def _latest_qa(session: AsyncSession, content_id: UUID) -> QAResult | None:
    result = await session.execute(
        select(QAResult)
        .where(QAResult.content_id == content_id)
        .order_by(QAResult.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _latest_video_asset(session: AsyncSession, content_id: UUID) -> MediaAsset | None:
    result = await session.execute(
        select(MediaAsset)
        .where(MediaAsset.content_id == content_id)
        .order_by(MediaAsset.created_at.desc())
    )
    assets = list(result.scalars().all())
    for asset in assets:
        if asset.kind in VIDEO_KINDS:
            return asset
    return assets[0] if assets else None


async def _connection_for(session: AsyncSession, platform: Platform) -> PlatformConnection | None:
    if platform is Platform.DRY_RUN:
        return None
    result = await session.execute(
        select(PlatformConnection).where(PlatformConnection.platform == platform.value)
    )
    return result.scalar_one_or_none()


async def _over_daily_cap(
    session: AsyncSession,
    platform: Platform,
    maximum: int,
    existing: Publication | None,
) -> bool:
    if existing is not None:
        return False
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.count(Publication.id)).where(
            Publication.platform == platform.value,
            Publication.status == PublishStatus.PUBLISHED.value,
            Publication.created_at >= start,
        )
    )
    return int(result.scalar_one()) >= maximum


async def _enrich_prepared(
    session: AsyncSession,
    content: ContentItem,
    prepared: PreparedPublish,
    payload: dict[str, Any],
    use_dry_run: bool,
) -> PreparedPublish:
    script: Script | None = None
    if content.selected_script_id:
        script = await session.get(Script, content.selected_script_id)
    if script is None:
        script = (
            await session.execute(
                select(Script).where(Script.content_id == content.id, Script.selected.is_(True))
            )
        ).scalar_one_or_none()
    title = (script.hook if script and script.hook else content.topic).strip() or prepared.title
    parts: list[str] = []
    if script:
        parts.extend([script.body, script.reveal, script.cta, script.caption])
        if script.hashtags:
            tags = " ".join(f"#{str(tag).lstrip('#')}" for tag in script.hashtags if tag)
            parts.append(tags)
    description = "\n\n".join(part.strip() for part in parts if part and str(part).strip())
    privacy = str(
        payload.get("privacy_status") or prepared.metadata.get("privacyStatus") or "unlisted"
    )
    metadata = {
        **prepared.metadata,
        "privacyStatus": privacy,
        "privacy_level": payload.get("privacy_level") or prepared.metadata.get("privacy_level"),
        "hashtags": list(script.hashtags) if script else [],
        "shorts": True,
        "snippet": {
            "title": title[:100],
            "description": description or prepared.description,
        },
        "video_url": payload.get("video_url"),
    }
    return prepared.model_copy(
        update={
            "platform": Platform(prepared.platform) if use_dry_run else prepared.platform,
            "title": title[:100],
            "description": description or prepared.description,
            "simulation": True if use_dry_run else content.simulation,
            "metadata": metadata,
        }
    )


async def _handle_validation_block(
    session: AsyncSession,
    publishing_job: PublishingJob,
    content: ContentItem,
    job: Job,
    platform: Platform,
    status: PublishStatus,
    reasons: list[str],
) -> None:
    reason = reasons[0] if reasons else status.value
    if status is PublishStatus.REJECTED_SIMULATION:
        publishing_job.status = status.value
        publishing_job.error = reason
        publishing_job.simulation = True
        await _emit(
            session,
            "publication.failed",
            {"reason": reason, "status": status.value},
            job,
            content,
            True,
        )
        return
    if status is PublishStatus.CONNECTION_REQUIRED:
        await _require_human(
            session,
            publishing_job,
            content,
            job,
            platform,
            status,
            title=f"Connect {platform.value}",
            instructions=_connection_instructions(platform),
            category="oauth",
            reason=reason,
        )
        return
    if status is PublishStatus.REQUIRES_HUMAN_ACTION:
        await _require_human(
            session,
            publishing_job,
            content,
            job,
            platform,
            status,
            title=f"{platform.value} needs a human action",
            instructions=_human_instructions(platform),
            category="connection",
            reason=reason,
        )
        return
    if status is PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL:
        await _require_human(
            session,
            publishing_job,
            content,
            job,
            platform,
            status,
            title=f"{platform.value} platform approval required",
            instructions=_approval_instructions(platform),
            category="platform_review",
            reason=reason,
        )
        return
    publishing_job.status = status.value
    publishing_job.error = reason
    content.status = ContentStatus.FAILED.value
    content.failure_reason = reason
    await _emit(
        session,
        "publication.failed",
        {"reason": reason},
        job,
        content,
        content.simulation,
    )


async def _persist_result(
    session: AsyncSession,
    publishing_job: PublishingJob,
    content: ContentItem,
    job: Job,
    platform: Platform,
    prepared: PreparedPublish,
    result: PublishResult,
    existing: Publication | None,
    use_dry_run: bool,
) -> None:
    publishing_job.status = result.status.value
    publishing_job.error = result.error
    publishing_job.simulation = bool(result.simulation or use_dry_run)

    if result.status in {
        PublishStatus.CONNECTION_REQUIRED,
        PublishStatus.REQUIRES_HUMAN_ACTION,
        PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL,
        PublishStatus.REJECTED_SIMULATION,
    }:
        await _handle_validation_block(
            session,
            publishing_job,
            content,
            job,
            platform,
            result.status,
            [result.error or result.status.value],
        )
        return

    if result.status not in {PublishStatus.PUBLISHED, PublishStatus.PROCESSING}:
        content.status = ContentStatus.FAILED.value
        content.failure_reason = result.error
        await _emit(
            session,
            "publication.failed",
            {"reason": result.error, "status": result.status.value},
            job,
            content,
            result.simulation,
        )
        return

    raw = {
        **(result.raw or {}),
        "adapter": "dry_run" if use_dry_run else platform.value,
        "real_platform_post": (not use_dry_run) and result.status is PublishStatus.PUBLISHED,
    }
    publication = await _upsert_publication(
        session,
        existing,
        content_id=content.id,
        platform=platform,
        result=result,
        prepared=prepared,
        raw=raw,
        simulation=bool(result.simulation or use_dry_run),
    )

    if result.status is PublishStatus.PUBLISHED:
        content.status = ContentStatus.PUBLISHED.value
        content.failure_reason = None
        await _enqueue_analytics(session, job, content, publication)
        await _emit(
            session,
            "publication.completed",
            {
                "publication_id": str(publication.id),
                "platform": platform.value,
                "external_id": result.external_id,
                "url": result.url,
                "simulation": publication.simulation,
                "real_platform_post": raw["real_platform_post"],
            },
            job,
            content,
            publication.simulation,
        )
        logger.info(
            "publish_completed",
            content_id=str(content.id),
            platform=platform.value,
            simulation=publication.simulation,
            real_platform_post=raw["real_platform_post"],
        )
        return

    content.status = ContentStatus.PUBLISHING.value
    await _emit(
        session,
        "publication.queued",
        {"publication_id": str(publication.id), "status": result.status.value},
        job,
        content,
        publication.simulation,
    )


async def _upsert_publication(
    session: AsyncSession,
    existing: Publication | None,
    *,
    content_id: UUID,
    platform: Platform,
    result: PublishResult,
    prepared: PreparedPublish,
    raw: dict[str, Any],
    simulation: bool,
) -> Publication:
    privacy = str(prepared.metadata.get("privacyStatus") or "unlisted")
    if existing is not None:
        existing.status = result.status.value
        existing.external_id = result.external_id or existing.external_id
        existing.url = result.url or existing.url
        existing.title = prepared.title
        existing.privacy_status = privacy
        existing.simulation = simulation
        existing.raw = raw
        await session.flush()
        return existing
    publication_id = uuid4()
    stmt = (
        upsert_insert(Publication, session)
        .values(
            id=publication_id,
            content_id=content_id,
            platform=platform.value,
            status=result.status.value,
            external_id=result.external_id,
            url=result.url,
            title=prepared.title,
            privacy_status=privacy,
            simulation=simulation,
            raw=raw,
        )
        .on_conflict_do_nothing(index_elements=["content_id", "platform"])
        .returning(Publication.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is None:
        found = await _get_publication(session, content_id, platform)
        if found is None:
            raise RuntimeError("publication upsert lost the row")
        return found
    loaded = await session.execute(select(Publication).where(Publication.id == inserted))
    return loaded.scalar_one()


async def _enqueue_analytics(
    session: AsyncSession, job: Job, content: ContentItem, publication: Publication
) -> None:
    now = datetime.now(UTC)
    for checkpoint, delay in CHECKPOINT_DELAYS:
        await enqueue(
            session,
            JobName.ANALYTICS_SNAPSHOT.value,
            {
                "publication_id": str(publication.id),
                "content_id": str(content.id),
                "platform": publication.platform,
                "checkpoint": checkpoint.value,
                "simulation": publication.simulation,
            },
            idempotency_key=f"analytics:{publication.id}:{checkpoint.value}",
            content_id=content.id,
            workflow_id=job.workflow_id or content.workflow_id,
            correlation_id=job.correlation_id,
            run_after=now + delay,
        )


async def _require_human(
    session: AsyncSession,
    publishing_job: PublishingJob,
    content: ContentItem,
    job: Job,
    platform: Platform,
    status: PublishStatus,
    *,
    title: str,
    instructions: str,
    category: str,
    reason: str,
) -> None:
    publishing_job.status = status.value
    publishing_job.error = reason
    if status is PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL:
        content.status = ContentStatus.AWAITING_PLATFORM_APPROVAL.value
    else:
        content.status = ContentStatus.AWAITING_HUMAN.value
    content.failure_reason = reason
    existing = (
        await session.execute(
            select(HumanAction).where(
                HumanAction.platform == platform.value,
                HumanAction.category == category,
                HumanAction.status == HumanActionStatus.OPEN.value,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            HumanAction(
                title=title[:200],
                instructions=instructions,
                category=category,
                status=HumanActionStatus.OPEN.value,
                platform=platform.value,
                blocking=True,
            )
        )
    await _emit(
        session,
        "human_action.required",
        {
            "platform": platform.value,
            "status": status.value,
            "reason": reason,
            "category": category,
        },
        job,
        content,
        content.simulation,
    )
    await _emit(
        session,
        "publication.failed",
        {"reason": reason, "status": status.value},
        job,
        content,
        content.simulation,
    )


async def _emit(
    session: AsyncSession,
    name: str,
    payload: dict[str, Any],
    job: Job,
    content: ContentItem,
    simulation: bool,
) -> None:
    session.add(
        SystemEvent(
            name=name,
            payload=payload,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id or content.workflow_id,
            content_id=content.id,
            simulation=simulation,
        )
    )


def _connection_instructions(platform: Platform) -> str:
    if platform is Platform.YOUTUBE:
        return (
            "Start YouTube OAuth from the dashboard using a dedicated channel. "
            "YOUTUBE_CLIENT_ID must be configured. Do not paste passwords or tokens into chat."
        )
    if platform is Platform.INSTAGRAM:
        return (
            "Set META_APP_ID and complete Instagram Professional conversion plus Page linking. "
            "Start OAuth from the dashboard. Do not paste passwords or tokens into chat."
        )
    if platform is Platform.TIKTOK:
        return (
            "Set TIKTOK_CLIENT_KEY and authorize Login Kit from the dashboard. "
            "Do not paste passwords or tokens into chat."
        )
    return "Connect the platform from the dashboard. Never share passwords."


def _human_instructions(platform: Platform) -> str:
    if platform is Platform.INSTAGRAM:
        return (
            "Link a Professional Instagram account to a Facebook Page, then reconnect OAuth "
            "so AME can store the ig_user_id. Do not paste passwords or tokens."
        )
    return (
        f"Complete the remaining {platform.value} account setup from the official platform UI. "
        "Do not paste passwords or tokens into chat."
    )


def _approval_instructions(platform: Platform) -> str:
    return (
        "TikTok Direct Post requires developer app review for video.publish and explicit "
        "creator consent. Complete TikTok for Developers audit and record unattended-post "
        "approval on the connection. Do not bypass in-app confirmation or scrape."
        if platform is Platform.TIKTOK
        else (
            f"{platform.value} requires platform review or per-post confirmation. "
            "Complete the official consent flow. Do not bypass it."
        )
    )
