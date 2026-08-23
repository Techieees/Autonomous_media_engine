from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import Settings, get_settings
from ame.contracts.enums import (
    AgentName,
    AgentRunStatus,
    ConnectionState,
    ContentStatus,
    JobName,
    JobStatus,
    Platform,
    QAVerdict,
)
from ame.costs.tracker import BudgetExceeded, assert_budget
from ame.db.models import (
    AgentRun,
    ContentItem,
    Job,
    MediaAsset,
    MetricSnapshot,
    PlatformConnection,
    ProductionManifestRecord,
    Publication,
    QAResult,
    ResearchPack,
    Script,
    SystemEvent,
)
from ame.jobs.queue import enqueue
from ame.observability import get_logger

logger = get_logger("ame.pipeline")

STAGE_ORDER: tuple[str, ...] = (
    "research",
    "pattern",
    "scripts",
    "critic",
    "media",
    "voice",
    "subs",
    "render",
    "qa",
    "publish",
    "analytics",
    "learning",
)

STAGE_JOB: dict[str, str] = {
    "research": JobName.RESEARCH.value,
    "pattern": JobName.PATTERN_ANALYZE.value,
    "scripts": JobName.SCRIPT_GENERATE.value,
    "critic": JobName.SCRIPT_CRITIQUE.value,
    "media": JobName.MEDIA_PLAN.value,
    "voice": JobName.VOICE_SYNTH.value,
    "subs": JobName.SUBTITLE_BUILD.value,
    "render": JobName.VIDEO_RENDER.value,
    "qa": JobName.QA_CHECK.value,
    "publish": JobName.PUBLISH.value,
    "analytics": JobName.ANALYTICS_SNAPSHOT.value,
    "learning": JobName.LEARNING_UPDATE.value,
}

PAID_STAGES = frozenset(
    {"research", "pattern", "scripts", "critic", "media", "voice", "render"}
)

BLOCKED_STATUSES = frozenset(
    {
        ContentStatus.REJECTED.value,
        ContentStatus.FAILED.value,
        ContentStatus.PAUSED_BY_BUDGET.value,
    }
)

_ACTIVE_JOB = frozenset(
    {
        JobStatus.QUEUED.value,
        JobStatus.LEASED.value,
        JobStatus.RUNNING.value,
        JobStatus.RETRY_WAIT.value,
    }
)

_DONE_JOB = frozenset({JobStatus.SUCCEEDED.value})

_STATUS_RANK: dict[str, int] = {
    ContentStatus.DISCOVERED.value: 0,
    ContentStatus.SCORED.value: 1,
    ContentStatus.APPROVED_FOR_RESEARCH.value: 2,
    ContentStatus.RESEARCHED.value: 3,
    ContentStatus.SCRIPTING.value: 4,
    ContentStatus.SCRIPT_SELECTED.value: 5,
    ContentStatus.PRODUCTION.value: 6,
    ContentStatus.QA.value: 7,
    ContentStatus.APPROVED.value: 8,
    ContentStatus.PUBLISHING.value: 9,
    ContentStatus.PUBLISHED.value: 10,
    ContentStatus.MEASURING.value: 11,
    ContentStatus.LEARNING_COMPLETE.value: 12,
}

_READY_CONNECTION = frozenset(
    {
        ConnectionState.CONNECTED.value,
        ConnectionState.READY.value,
    }
)


def _as_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _rank(status: str) -> int:
    return _STATUS_RANK.get(status, -1)


def idempotency_key(stage: str, content_id: UUID, platform: str | None = None) -> str:
    if stage == "publish":
        return f"publish:{content_id}:{platform or Platform.DRY_RUN.value}"
    mapping = {
        "research": f"research:{content_id}",
        "pattern": f"pattern:{content_id}",
        "scripts": f"scripts:{content_id}",
        "critic": f"critic:{content_id}",
        "media": f"media:{content_id}",
        "voice": f"voice:{content_id}",
        "subs": f"subs:{content_id}",
        "render": f"render:{content_id}",
        "qa": f"qa:{content_id}",
        "analytics": f"analytics:{content_id}",
        "learning": f"learning:{content_id}",
    }
    return mapping[stage]


def content_id_from_job(job: Job) -> UUID | None:
    payload = job.payload or {}
    return _as_uuid(payload.get("content_id")) or _as_uuid(job.content_id)


async def handle_pipeline_advance(session: AsyncSession, job: Job) -> None:
    content_id = content_id_from_job(job)
    if content_id is None:
        logger.info("pipeline_advance_skipped", reason="missing_content_id")
        return

    content = await session.get(ContentItem, content_id)
    if content is None:
        logger.info("pipeline_advance_skipped", reason="content_missing", content_id=str(content_id))
        return

    if content.status in BLOCKED_STATUSES:
        logger.info(
            "pipeline_advance_blocked",
            content_id=str(content.id),
            status=content.status,
        )
        return

    snapshot = await _load_snapshot(session, content)
    settings = get_settings()
    platform = await _publish_platform(session, settings, content)
    next_stage = _next_missing_stage(snapshot, platform)
    if next_stage is None:
        logger.info("pipeline_advance_idle", content_id=str(content.id), status=content.status)
        return

    if next_stage == "publish" and not _may_publish(snapshot, content):
        logger.info("pipeline_advance_skip_publish", content_id=str(content.id))
        return

    if next_stage in PAID_STAGES:
        try:
            await assert_budget(session, kind="ai")
            await assert_budget(session, kind="media")
        except BudgetExceeded as exc:
            content.status = ContentStatus.PAUSED_BY_BUDGET.value
            content.failure_reason = str(exc)
            session.add(
                SystemEvent(
                    name="budget.limit_reached",
                    payload={
                        "kind": exc.kind,
                        "spent": exc.spent,
                        "limit": exc.limit,
                        "content_id": str(content.id),
                        "blocked_stage": next_stage,
                    },
                    correlation_id=job.correlation_id,
                    workflow_id=content.workflow_id,
                    content_id=content.id,
                    simulation=content.simulation,
                )
            )
            await session.flush()
            logger.info(
                "pipeline_advance_budget",
                content_id=str(content.id),
                kind=exc.kind,
                stage=next_stage,
            )
            return

    key = idempotency_key(next_stage, content.id, platform)
    if next_stage == "publish" and _publish_already_enqueued(snapshot):
        logger.info("pipeline_advance_publish_exists", content_id=str(content.id))
        return

    payload: dict[str, Any] = {
        "content_id": str(content.id),
        "stage": next_stage,
    }
    if next_stage == "publish":
        payload["platform"] = platform

    await enqueue(
        session,
        STAGE_JOB[next_stage],
        payload,
        idempotency_key=key,
        content_id=content.id,
        workflow_id=content.workflow_id,
        correlation_id=job.correlation_id,
    )
    await session.flush()
    logger.info(
        "pipeline_advance_enqueued",
        content_id=str(content.id),
        stage=next_stage,
        job_name=STAGE_JOB[next_stage],
        idempotency_key=key,
    )


class _Snapshot:
    def __init__(
        self,
        content: ContentItem,
        *,
        has_research: bool,
        script_count: int,
        selected: bool,
        has_manifest: bool,
        has_voice: bool,
        has_subs: bool,
        has_video: bool,
        qa: QAResult | None,
        publications: list[Publication],
        has_metrics: bool,
        jobs: list[Job],
        pattern_succeeded: bool,
        learning_succeeded: bool,
    ) -> None:
        self.content = content
        self.has_research = has_research
        self.script_count = script_count
        self.selected = selected
        self.has_manifest = has_manifest
        self.has_voice = has_voice
        self.has_subs = has_subs
        self.has_video = has_video
        self.qa = qa
        self.publications = publications
        self.has_metrics = has_metrics
        self.jobs = jobs
        self.pattern_succeeded = pattern_succeeded
        self.learning_succeeded = learning_succeeded


async def _load_snapshot(session: AsyncSession, content: ContentItem) -> _Snapshot:
    content_id = content.id
    research = await session.execute(
        select(ResearchPack.id).where(ResearchPack.content_id == content_id).limit(1)
    )
    scripts = await session.execute(select(Script).where(Script.content_id == content_id))
    script_rows = list(scripts.scalars().all())
    manifest = await session.execute(
        select(ProductionManifestRecord.id)
        .where(ProductionManifestRecord.content_id == content_id)
        .limit(1)
    )
    assets = await session.execute(select(MediaAsset).where(MediaAsset.content_id == content_id))
    asset_rows = list(assets.scalars().all())
    qa_rows = await session.execute(
        select(QAResult)
        .where(QAResult.content_id == content_id)
        .order_by(QAResult.created_at.desc())
    )
    publications = await session.execute(
        select(Publication).where(Publication.content_id == content_id)
    )
    metrics = await session.execute(
        select(MetricSnapshot.id).where(MetricSnapshot.content_id == content_id).limit(1)
    )
    jobs = await session.execute(
        select(Job).where(
            or_(
                Job.content_id == content_id,
                Job.idempotency_key.like(f"%{content_id}%"),
            )
        )
    )
    pattern_run = await session.execute(
        select(AgentRun.id).where(
            AgentRun.content_id == content_id,
            AgentRun.agent == AgentName.PATTERN_ANALYST.value,
            AgentRun.status == AgentRunStatus.SUCCEEDED.value,
        ).limit(1)
    )
    learning_run = await session.execute(
        select(Job.id).where(
            Job.content_id == content_id,
            Job.name == JobName.LEARNING_UPDATE.value,
            Job.status == JobStatus.SUCCEEDED.value,
        ).limit(1)
    )

    kinds = {asset.kind for asset in asset_rows}
    return _Snapshot(
        content,
        has_research=research.scalar_one_or_none() is not None,
        script_count=len(script_rows),
        selected=content.selected_script_id is not None or any(row.selected for row in script_rows),
        has_manifest=manifest.scalar_one_or_none() is not None,
        has_voice=bool(kinds & {"voiceover", "voice"}),
        has_subs=bool(kinds & {"subtitles", "subtitle"}),
        has_video=bool(kinds & {"video", "render"}),
        qa=qa_rows.scalars().first(),
        publications=list(publications.scalars().all()),
        has_metrics=metrics.scalar_one_or_none() is not None,
        jobs=list(jobs.scalars().all()),
        pattern_succeeded=pattern_run.scalar_one_or_none() is not None,
        learning_succeeded=learning_run.scalar_one_or_none() is not None,
    )


def _jobs_for(snapshot: _Snapshot, stage: str) -> list[Job]:
    name = STAGE_JOB[stage]
    return [job for job in snapshot.jobs if job.name == name]


def _job_succeeded(snapshot: _Snapshot, stage: str) -> bool:
    return any(job.status in _DONE_JOB for job in _jobs_for(snapshot, stage))


def _job_in_flight(snapshot: _Snapshot, stage: str) -> bool:
    return any(job.status in _ACTIVE_JOB for job in _jobs_for(snapshot, stage))


def _stage_complete(snapshot: _Snapshot, stage: str, platform: str) -> bool:
    status = snapshot.content.status
    rank = _rank(status)
    if stage == "research":
        return snapshot.has_research or _job_succeeded(snapshot, stage) or rank >= 3
    if stage == "pattern":
        return (
            snapshot.pattern_succeeded
            or _job_succeeded(snapshot, stage)
            or snapshot.script_count > 0
            or rank >= 4
        )
    if stage == "scripts":
        return snapshot.script_count > 0 or _job_succeeded(snapshot, stage) or rank >= 5
    if stage == "critic":
        return snapshot.selected or _job_succeeded(snapshot, stage) or rank >= 5
    if stage == "media":
        return snapshot.has_manifest or _job_succeeded(snapshot, stage) or rank >= 6
    if stage == "voice":
        return snapshot.has_voice or _job_succeeded(snapshot, stage) or rank >= 7
    if stage == "subs":
        return snapshot.has_subs or _job_succeeded(snapshot, stage) or rank >= 7
    if stage == "render":
        return snapshot.has_video or _job_succeeded(snapshot, stage) or rank >= 7
    if stage == "qa":
        return snapshot.qa is not None or _job_succeeded(snapshot, stage) or rank >= 8
    if stage == "publish":
        if snapshot.publications:
            return True
        if any(job.status in _DONE_JOB for job in _jobs_for(snapshot, "publish")):
            return True
        expected = idempotency_key("publish", snapshot.content.id, platform)
        return any(job.idempotency_key == expected and job.status in _DONE_JOB for job in snapshot.jobs)
    if stage == "analytics":
        return snapshot.has_metrics or _job_succeeded(snapshot, stage) or rank >= 11
    if stage == "learning":
        return (
            snapshot.learning_succeeded
            or _job_succeeded(snapshot, stage)
            or status == ContentStatus.LEARNING_COMPLETE.value
        )
    return False


def _publish_already_enqueued(snapshot: _Snapshot) -> bool:
    if snapshot.publications:
        return True
    return any(job.name == JobName.PUBLISH.value for job in snapshot.jobs)


def _may_publish(snapshot: _Snapshot, content: ContentItem) -> bool:
    if content.status in {
        ContentStatus.AWAITING_HUMAN.value,
        ContentStatus.AWAITING_PLATFORM_APPROVAL.value,
        ContentStatus.REJECTED.value,
        ContentStatus.FAILED.value,
        ContentStatus.PAUSED_BY_BUDGET.value,
    }:
        return False
    if snapshot.qa is None:
        return False
    if snapshot.qa.verdict != QAVerdict.APPROVED.value:
        return False
    return not _publish_already_enqueued(snapshot)


def _next_missing_stage(snapshot: _Snapshot, platform: str) -> str | None:
    for stage in STAGE_ORDER:
        if _stage_complete(snapshot, stage, platform):
            continue
        if stage == "publish" and _publish_already_enqueued(snapshot):
            return None
        if _job_in_flight(snapshot, stage):
            return None
        if snapshot.qa is not None and snapshot.qa.verdict != QAVerdict.APPROVED.value:
            if stage in {"publish", "analytics", "learning"}:
                return None
        return stage
    return None


async def _publish_platform(
    session: AsyncSession, settings: Settings, content: ContentItem
) -> str:
    if settings.dry_run or content.simulation:
        return Platform.DRY_RUN.value
    result = await session.execute(
        select(PlatformConnection).where(PlatformConnection.state.in_(_READY_CONNECTION))
    )
    connection = result.scalars().first()
    if connection is not None:
        return connection.platform
    return Platform.DRY_RUN.value
