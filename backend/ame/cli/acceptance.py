"""Dry-run acceptance seed + verifier.

This module starts the real pipeline. It never writes a fake Publication or
simulated success row — the dry-run publisher adapter does that.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from ame.db.dialect import upsert_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import (
    AgentName,
    ConnectionState,
    ContentStatus,
    JobName,
    JobStatus,
    Platform,
)
from ame.db.models import (
    AgentDecisionRecord,
    ContentItem,
    Job,
    LearningRecommendation,
    MediaAsset,
    MetricSnapshot,
    Opportunity,
    PlatformConnection,
    ProductionManifestRecord,
    Publication,
    QAResult,
    ResearchPack,
    Script,
    TrendSignal,
)
from ame.db.runtime import database_backend
from ame.db.session import async_session_factory, init_database
from ame.jobs.queue import enqueue
from ame.observability import configure_logging, get_logger

logger = get_logger("ame.cli.acceptance")

ACCEPTANCE_TIMEOUT_SECONDS = 8 * 60
POLL_SECONDS = 1.0
DIRECTOR_TICK_DELAY = timedelta(seconds=3)
REPORT_PATH = Path(__file__).resolve().with_name("last_acceptance.json")

BOOTSTRAP_PLATFORMS = (
    Platform.YOUTUBE.value,
    Platform.INSTAGRAM.value,
    Platform.TIKTOK.value,
)

VOICE_KINDS = frozenset({"voiceover", "voice", "audio", "tts"})
SUBTITLE_KINDS = frozenset({"subtitles", "subtitle", "srt", "captions"})
VIDEO_KINDS = frozenset({"video", "render", "mp4"})

TERMINAL_STOP = frozenset(
    {
        ContentStatus.REJECTED.value,
        ContentStatus.FAILED.value,
    }
)
PUBLISHED_OR_BEYOND = frozenset(
    {
        ContentStatus.PUBLISHED.value,
        ContentStatus.MEASURING.value,
        ContentStatus.LEARNING_COMPLETE.value,
    }
)
WAIT_STATUSES = TERMINAL_STOP | PUBLISHED_OR_BEYOND

_STATUS_RANK = {
    ContentStatus.LEARNING_COMPLETE.value: 100,
    ContentStatus.MEASURING.value: 90,
    ContentStatus.PUBLISHED.value: 80,
    ContentStatus.PUBLISHING.value: 70,
    ContentStatus.APPROVED.value: 60,
    ContentStatus.QA.value: 50,
    ContentStatus.PRODUCTION.value: 40,
    ContentStatus.SCRIPT_SELECTED.value: 30,
    ContentStatus.SCRIPTING.value: 20,
    ContentStatus.RESEARCHED.value: 16,
    ContentStatus.APPROVED_FOR_RESEARCH.value: 12,
    ContentStatus.SCORED.value: 8,
    ContentStatus.DISCOVERED.value: 4,
    ContentStatus.AWAITING_HUMAN.value: 3,
    ContentStatus.AWAITING_PLATFORM_APPROVAL.value: 3,
    ContentStatus.PAUSED_BY_BUDGET.value: 2,
    ContentStatus.REJECTED.value: 1,
    ContentStatus.FAILED.value: 1,
}

ACTIVE_JOB_STATUSES = frozenset(
    {
        JobStatus.QUEUED.value,
        JobStatus.LEASED.value,
        JobStatus.RUNNING.value,
        JobStatus.RETRY_WAIT.value,
    }
)


def _timeout_seconds() -> int:
    raw = os.getenv("AME_ACCEPTANCE_TIMEOUT", str(ACCEPTANCE_TIMEOUT_SECONDS))
    try:
        return max(30, int(raw))
    except ValueError:
        return ACCEPTANCE_TIMEOUT_SECONDS


def _drive_jobs() -> bool:
    return os.getenv("AME_ACCEPTANCE_DRIVE_JOBS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not jsonable: {type(value)!r}")


async def ensure_bootstrap_connections(session: AsyncSession) -> list[str]:
    """Idempotently insert youtube/instagram/tiktok as not_configured."""
    try:
        from ame.bootstrap.service import seed_bootstrap

        await seed_bootstrap(session)
    except ImportError:
        logger.info("bootstrap_package_missing_using_local_seed")
    except Exception:  # noqa: BLE001
        logger.exception("bootstrap_seed_failed_falling_back")
        await session.rollback()

    created: list[str] = []
    for platform in BOOTSTRAP_PLATFORMS:
        stmt = (
            upsert_insert(PlatformConnection, session)
            .values(
                id=uuid4(),
                platform=platform,
                state=ConnectionState.NOT_CONFIGURED.value,
                scopes=[],
                metadata_json={},
            )
            .on_conflict_do_nothing(index_elements=["platform"])
        )
        result = await session.execute(stmt)
        if result.rowcount:
            created.append(platform)
    await session.flush()
    return created


async def handle_acceptance_seed(session: AsyncSession, job: Job) -> None:
    """Job handler: bootstrap connections, then start the real pipeline."""
    await ensure_bootstrap_connections(session)
    correlation_id = job.correlation_id or str(job.id)
    workflow_id = job.workflow_id
    now = datetime.now(UTC)
    payload = {
        "source": "acceptance_seed",
        "seed_job_id": str(job.id),
    }
    await enqueue(
        session,
        JobName.TREND_INGEST.value,
        payload,
        idempotency_key=f"acceptance:{job.id}:{JobName.TREND_INGEST.value}",
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        run_after=now,
    )
    await enqueue(
        session,
        JobName.DIRECTOR_TICK.value,
        payload,
        idempotency_key=f"acceptance:{job.id}:{JobName.DIRECTOR_TICK.value}",
        correlation_id=correlation_id,
        workflow_id=workflow_id,
        run_after=now + DIRECTOR_TICK_DELAY,
    )
    logger.info(
        "acceptance_seeded",
        seed_job_id=str(job.id),
        correlation_id=correlation_id,
    )


async def _process_one_job() -> bool:
    from ame.jobs.worker import process_one

    return await process_one()


async def _latest_run_content(cutoff: datetime) -> ContentItem | None:
    async with async_session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(ContentItem).where(ContentItem.created_at >= cutoff)
                )
            ).all()
        )
    if not rows:
        return None
    return max(rows, key=lambda item: (_STATUS_RANK.get(item.status, 0), item.created_at))


async def _active_job_count() -> int:
    async with async_session_factory() as session:
        value = await session.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES))
        )
        return int(value or 0)


async def _wait_for_content(cutoff: datetime, deadline: float) -> ContentItem | None:
    """Poll until published/rejected/failed (keep going after publish for learning)."""
    idle_streak = 0
    published: ContentItem | None = None
    drive_error_logged = False
    while time.monotonic() < deadline:
        if _drive_jobs():
            try:
                worked = await _process_one_job()
            except Exception:  # noqa: BLE001
                if not drive_error_logged:
                    logger.exception("acceptance_job_drive_failed")
                    drive_error_logged = True
                worked = False
        else:
            worked = False

        content = await _latest_run_content(cutoff)
        if content is not None:
            if content.status in PUBLISHED_OR_BEYOND:
                published = content
                if content.status == ContentStatus.LEARNING_COMPLETE.value:
                    return content
                if await _learning_ready(cutoff, content.id):
                    return content
            elif content.status in TERMINAL_STOP:
                return content

        active = await _active_job_count()
        if worked:
            idle_streak = 0
            continue
        if active == 0:
            idle_streak += 1
            # Pipeline went idle. If we already published, finish; if we have a
            # hard failure, finish; otherwise give a few extra idle polls.
            if published is not None and idle_streak >= 3:
                return published
            if content is not None and content.status in TERMINAL_STOP and idle_streak >= 2:
                return content
            if idle_streak >= 8 and content is not None:
                return content
        await asyncio.sleep(POLL_SECONDS)
    return published or await _latest_run_content(cutoff)


async def _learning_ready(cutoff: datetime, content_id: UUID) -> bool:
    async with async_session_factory() as session:
        rec = await session.scalar(
            select(func.count())
            .select_from(LearningRecommendation)
            .where(LearningRecommendation.created_at >= cutoff)
        )
        decision = await session.scalar(
            select(func.count())
            .select_from(AgentDecisionRecord)
            .where(
                AgentDecisionRecord.agent == AgentName.DIRECTOR.value,
                AgentDecisionRecord.created_at >= cutoff,
            )
        )
        metrics = await session.scalar(
            select(func.count())
            .select_from(MetricSnapshot)
            .where(MetricSnapshot.content_id == content_id)
        )
        return bool((rec or 0) or (decision or 0)) and bool(metrics or 0)


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


async def _verify_artifacts(
    session: AsyncSession, *, cutoff: datetime, content: ContentItem | None
) -> list[dict[str, Any]]:
    content_id = content.id if content else None

    trend_n = await session.scalar(select(func.count()).select_from(TrendSignal)) or 0
    opp_n = await session.scalar(select(func.count()).select_from(Opportunity)) or 0

    pack_n = 0
    script_n = 0
    selected_n = 0
    manifest_n = 0
    qa_n = 0
    kinds: list[str] = []
    pub_n = 0
    sim_pub_n = 0
    metric_n = 0
    if content_id is not None:
        pack_n = (
            await session.scalar(
                select(func.count()).select_from(ResearchPack).where(ResearchPack.content_id == content_id)
            )
            or 0
        )
        script_n = (
            await session.scalar(
                select(func.count()).select_from(Script).where(Script.content_id == content_id)
            )
            or 0
        )
        selected_n = (
            await session.scalar(
                select(func.count())
                .select_from(Script)
                .where(Script.content_id == content_id, Script.selected.is_(True))
            )
            or 0
        )
        manifest_n = (
            await session.scalar(
                select(func.count())
                .select_from(ProductionManifestRecord)
                .where(ProductionManifestRecord.content_id == content_id)
            )
            or 0
        )
        qa_n = (
            await session.scalar(
                select(func.count()).select_from(QAResult).where(QAResult.content_id == content_id)
            )
            or 0
        )
        kinds = list(
            (
                await session.scalars(
                    select(MediaAsset.kind).where(MediaAsset.content_id == content_id)
                )
            ).all()
        )
        pub_n = (
            await session.scalar(
                select(func.count()).select_from(Publication).where(Publication.content_id == content_id)
            )
            or 0
        )
        sim_pub_n = (
            await session.scalar(
                select(func.count())
                .select_from(Publication)
                .where(Publication.content_id == content_id, Publication.simulation.is_(True))
            )
            or 0
        )
        metric_n = (
            await session.scalar(
                select(func.count())
                .select_from(MetricSnapshot)
                .where(MetricSnapshot.content_id == content_id)
            )
            or 0
        )

    rec_n = (
        await session.scalar(
            select(func.count())
            .select_from(LearningRecommendation)
            .where(LearningRecommendation.created_at >= cutoff)
        )
        or 0
    )
    director_n = (
        await session.scalar(
            select(func.count())
            .select_from(AgentDecisionRecord)
            .where(
                AgentDecisionRecord.agent == AgentName.DIRECTOR.value,
                AgentDecisionRecord.created_at >= cutoff,
            )
        )
        or 0
    )

    kind_set = {k.lower() for k in kinds}
    has_voice = bool(kind_set & VOICE_KINDS)
    has_subs = bool(kind_set & SUBTITLE_KINDS)
    has_video = bool(kind_set & VIDEO_KINDS)
    selected_ok = selected_n >= 1 or bool(content and content.selected_script_id)
    mp4_check = await _verify_playable_mp4(session, content_id)

    return [
        _check("trend_signal", trend_n >= 1, f"count={trend_n}"),
        _check("opportunity", opp_n >= 1, f"count={opp_n}"),
        _check("research_pack", pack_n >= 1, f"count={pack_n}"),
        _check("scripts_at_least_two", script_n >= 2, f"count={script_n}"),
        _check("script_selected", selected_ok, f"selected_rows={selected_n}"),
        _check("production_manifest", manifest_n >= 1, f"count={manifest_n}"),
        _check("voice_asset", has_voice, f"kinds={sorted(kind_set)}"),
        _check("subtitle_asset", has_subs, f"kinds={sorted(kind_set)}"),
        _check("video_asset", has_video, f"kinds={sorted(kind_set)}"),
        mp4_check,
        _check("qa_result", qa_n >= 1, f"count={qa_n}"),
        _check(
            "publication_simulation",
            sim_pub_n >= 1,
            f"simulation_true={sim_pub_n} total={pub_n}",
        ),
        _check("metric_snapshot", metric_n >= 1, f"count={metric_n}"),
        _check(
            "learning_or_director_decision",
            rec_n >= 1 or director_n >= 1,
            f"learning_recommendations={rec_n} director_decisions={director_n}",
        ),
    ]


async def _verify_playable_mp4(session: AsyncSession, content_id: UUID | None) -> dict[str, Any]:
    if content_id is None:
        return _check("playable_mp4", False, "no content item")
    assets = list(
        (
            await session.scalars(select(MediaAsset).where(MediaAsset.content_id == content_id))
        ).all()
    )
    video = next((row for row in assets if str(row.kind).lower() in VIDEO_KINDS), None)
    if video is None:
        return _check("playable_mp4", False, "no video media asset")
    from ame.qa.ffprobe import probe_media
    from ame.storage.base import get_store

    path = get_store().local_path(video.storage_key)
    if not path.is_file():
        return _check("playable_mp4", False, f"missing file {path}")
    size = path.stat().st_size
    header = path.read_bytes()[:64]
    looks_mp4 = b"ftyp" in header
    probe = await probe_media(path)
    passed = (
        size > 8000
        and looks_mp4
        and probe.available
        and probe.has_video
        and probe.resolution_ok
        and probe.error is None
    )
    return _check(
        "playable_mp4",
        passed,
        (
            f"path={path} size={size} {probe.width}x{probe.height} "
            f"duration_s={probe.duration_s} audio={probe.has_audio} "
            f"ftyp={looks_mp4} error={probe.error}"
        ),
    )


async def _verify_duplicate_publish(
    session: AsyncSession, content: ContentItem | None
) -> dict[str, Any]:
    if content is None:
        return _check("duplicate_publish", False, "no content item to publish")

    existing = (
        await session.scalars(select(Publication).where(Publication.content_id == content.id))
    ).first()
    platform = existing.platform if existing is not None else Platform.DRY_RUN.value
    key = f"publish:{content.id}:{platform}"
    payload = {"content_id": str(content.id), "platform": platform, "source": "acceptance_dup"}
    first = await enqueue(
        session,
        JobName.PUBLISH.value,
        payload,
        idempotency_key=key,
        content_id=content.id,
        workflow_id=content.workflow_id,
    )
    second = await enqueue(
        session,
        JobName.PUBLISH.value,
        payload,
        idempotency_key=key,
        content_id=content.id,
        workflow_id=content.workflow_id,
    )
    await session.commit()
    same_job = first.id == second.id

    if _drive_jobs():
        for _ in range(6):
            try:
                worked = await _process_one_job()
            except Exception:  # noqa: BLE001
                logger.exception("acceptance_dup_drive_failed")
                break
            if not worked:
                break

    async with async_session_factory() as fresh:
        pub_n = (
            await fresh.scalar(
                select(func.count()).select_from(Publication).where(Publication.content_id == content.id)
            )
            or 0
        )
        job_n = (
            await fresh.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key == key))
            or 0
        )

    passed = same_job and job_n == 1 and pub_n == 1
    return _check(
        "duplicate_publish",
        passed,
        (
            f"same_enqueued_job={same_job} jobs_with_key={job_n} "
            f"publications={pub_n} key={key}"
        ),
    )


async def _failed_jobs(cutoff: datetime) -> list[dict[str, Any]]:
    async with async_session_factory() as session:
        rows = (
            await session.scalars(
                select(Job)
                .where(
                    Job.created_at >= cutoff,
                    Job.status.in_(
                        [JobStatus.FAILED.value, JobStatus.DEAD.value, JobStatus.RETRY_WAIT.value]
                    ),
                )
                .order_by(Job.created_at.desc())
                .limit(20)
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "name": row.name,
                "status": row.status,
                "attempts": row.attempts,
                "last_error": row.last_error,
            }
            for row in rows
        ]


async def _connection_states() -> dict[str, str]:
    async with async_session_factory() as session:
        rows = (await session.scalars(select(PlatformConnection))).all()
        return {row.platform: row.state for row in rows}


async def run_acceptance() -> dict[str, Any]:
    """Seed the real pipeline, wait, verify artifacts, write last_acceptance.json."""
    configure_logging()
    init_database()
    settings = get_settings()
    timeout = _timeout_seconds()
    started = datetime.now(UTC)
    cutoff = started - timedelta(seconds=5)
    deadline = time.monotonic() + timeout
    correlation_id = str(uuid4())
    workflow_id = uuid4()

    async with async_session_factory() as session:
        await ensure_bootstrap_connections(session)
        seed_job = await enqueue(
            session,
            JobName.ACCEPTANCE_SEED.value,
            {
                "source": "cli",
                "started_at": started.isoformat(),
            },
            idempotency_key=f"acceptance:seed:{correlation_id}",
            correlation_id=correlation_id,
            workflow_id=workflow_id,
        )
        await handle_acceptance_seed(session, seed_job)
        await session.commit()
        seed_job_id = str(seed_job.id)

    logger.info(
        "acceptance_started",
        seed_job_id=seed_job_id,
        correlation_id=correlation_id,
        timeout_seconds=timeout,
        drive_jobs=_drive_jobs(),
    )

    content = await _wait_for_content(cutoff, deadline)
    timed_out = time.monotonic() >= deadline and (
        content is None or content.status not in WAIT_STATUSES
    )

    async with async_session_factory() as session:
        artifact_checks = await _verify_artifacts(session, cutoff=cutoff, content=content)
        dup_check = await _verify_duplicate_publish(session, content)

    checks = [*artifact_checks, dup_check]
    content_ok = bool(content and content.status in PUBLISHED_OR_BEYOND)
    checks.insert(
        0,
        _check(
            "content_published",
            content_ok,
            (
                f"status={content.status if content else 'none'} "
                f"content_id={content.id if content else None}"
            ),
        ),
    )

    failed = [item for item in checks if not item["passed"]]
    passed = not failed and not timed_out
    finished = datetime.now(UTC)
    report: dict[str, Any] = {
        "passed": passed,
        "verdict": "pass" if passed else "fail",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": round((finished - started).total_seconds(), 2),
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "dry_run": settings.dry_run,
        "database_backend": database_backend(),
        "drive_jobs": _drive_jobs(),
        "correlation_id": correlation_id,
        "seed_job_id": seed_job_id,
        "content_id": str(content.id) if content else None,
        "content_status": content.status if content else None,
        "content_simulation": content.simulation if content else None,
        "connections": await _connection_states(),
        "checks": checks,
        "failed_checks": [item["name"] for item in failed],
        "failed_or_retry_jobs": await _failed_jobs(cutoff),
        "notes": [
            "Publication rows must come from the dry-run publisher, not this CLI.",
            "Missing artifacts are a fail. rejected/failed without a full artifact set is a fail.",
            "docs/acceptance-report.md is not written by this module (no write access).",
            "Run with a live API+worker stack, or let this process drive jobs (default).",
        ],
        "how_to_run": [
            "./scripts/dev.ps1",
            "./scripts/acceptance.ps1",
            "python -m ame.cli.acceptance",
        ],
        "report_path": str(REPORT_PATH),
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=_jsonable) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=_jsonable), flush=True)
    logger.info("acceptance_finished", passed=passed, failed_checks=report["failed_checks"])
    return report


def main() -> None:
    report = asyncio.run(run_acceptance())
    raise SystemExit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
