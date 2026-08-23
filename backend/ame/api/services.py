from __future__ import annotations

import inspect
import math
import shutil
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ame.api.errors import APIError, redact_secrets
from ame.config import Settings, get_settings
from ame.contracts.enums import (
    ConnectionState,
    ContentStatus,
    HumanActionStatus,
    JobName,
    JobStatus,
    Platform,
    PublishStatus,
    QAVerdict,
    RevenueKind,
)
from ame.costs.tracker import spend_today
from ame.db.models import (
    AgentDecisionRecord,
    AgentRun,
    AgentTask,
    ContentItem,
    Experiment,
    HumanAction,
    Job,
    LearningRecommendation,
    MetricSnapshot,
    Opportunity,
    PlatformConnection,
    Publication,
    PublishingJob,
    QAResult,
    RevenueEvent,
    Script,
    StrategyAllocation,
    SystemEvent,
    TrendSignal,
)
from ame.db.session import async_session_factory
from ame.jobs.queue import enqueue
from ame.observability import get_logger

log = get_logger("ame.api.services")

CHECKLIST: tuple[tuple[str, str, str | None, str], ...] = (
    (
        "Create dedicated Google/YouTube brand account",
        "account",
        Platform.YOUTUBE.value,
        "Create a dedicated Google/YouTube brand account in the official Google account flow. "
        "Do not paste passwords or recovery codes into AME.",
    ),
    (
        "Complete YouTube OAuth",
        "oauth",
        Platform.YOUTUBE.value,
        "Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, and YOUTUBE_REDIRECT_URI, then open "
        "the YouTube OAuth start URL in this dashboard. Complete Google consent in the browser.",
    ),
    (
        "Create dedicated Instagram account",
        "account",
        Platform.INSTAGRAM.value,
        "Create a dedicated Instagram account in the official Instagram app or website. "
        "AME never asks for the account password.",
    ),
    (
        "Convert/configure eligible Instagram professional account",
        "account",
        Platform.INSTAGRAM.value,
        "Convert the account to an eligible professional/creator account in Instagram settings, "
        "then attach it to a Meta app you control.",
    ),
    (
        "Complete Meta/Instagram authorization",
        "oauth",
        Platform.INSTAGRAM.value,
        "Set META_APP_ID, META_APP_SECRET, and META_REDIRECT_URI. Open the Instagram OAuth start "
        "URL and finish Meta consent in the browser.",
    ),
    (
        "Create dedicated TikTok account",
        "account",
        Platform.TIKTOK.value,
        "Create a dedicated TikTok account through the official TikTok app or website. "
        "Do not send credentials to AME.",
    ),
    (
        "Configure TikTok developer application",
        "oauth",
        Platform.TIKTOK.value,
        "Create a TikTok developer application in the official portal and set TIKTOK_CLIENT_KEY, "
        "TIKTOK_CLIENT_SECRET, and TIKTOK_REDIRECT_URI.",
    ),
    (
        "Complete TikTok platform review/authorization if required",
        "review",
        Platform.TIKTOK.value,
        "Finish any TikTok app review or creator authorization the official API requires. "
        "If the platform demands per-post confirmation, AME will wait without blocking other work.",
    ),
    (
        "Monetization not yet eligible",
        "monetization",
        None,
        "Monetization applications, KYC, and payout setup are owner-only. AME will not invent "
        "revenue or apply on your behalf.",
    ),
)

AWAITING_PUBLISH = {
    PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL.value,
    PublishStatus.REQUIRES_HUMAN_ACTION.value,
    PublishStatus.CONNECTION_REQUIRED.value,
    ContentStatus.AWAITING_HUMAN.value,
    ContentStatus.AWAITING_PLATFORM_APPROVAL.value,
}

ANALYTICS_WINDOWS = {"24h", "7d", "30d", "lifetime"}


def _now() -> datetime:
    return datetime.now(UTC)


def _day_start(now: datetime | None = None) -> datetime:
    current = now or _now()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: datetime | None = None) -> datetime:
    current = now or _now()
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _window_start(window: str) -> datetime | None:
    now = _now()
    if window == "24h":
        return now - timedelta(hours=24)
    if window == "7d":
        return now - timedelta(days=7)
    if window == "30d":
        return now - timedelta(days=30)
    return None


def _as_float(value: Any) -> float:
    return float(value or 0)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_external(module_name: str, attr: str, *args: Any) -> bool:
    try:
        module = __import__(module_name, fromlist=[attr])
        fn = getattr(module, attr)
    except (ImportError, AttributeError):
        return False
    await _maybe_await(fn(*args))
    return True


async def seed_bootstrap(session: AsyncSession) -> None:
    if await _call_external("ame.bootstrap.service", "seed_bootstrap", session):
        return
    existing = await session.execute(select(func.count()).select_from(PlatformConnection))
    if int(existing.scalar_one()) == 0:
        for platform in (Platform.YOUTUBE.value, Platform.INSTAGRAM.value, Platform.TIKTOK.value):
            session.add(
                PlatformConnection(
                    platform=platform,
                    state=ConnectionState.NOT_CONFIGURED.value,
                    scopes=[],
                    metadata_json={},
                )
            )
    await session.flush()


async def run_startup_seed() -> None:
    try:
        async with async_session_factory() as session:
            await seed_bootstrap(session)
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("bootstrap_seed_failed")


async def ping_db() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception as exc:  # noqa: BLE001
        log.warning("health_db_failed", error=type(exc).__name__)
        return {"ok": False, "latency_ms": None, "error": type(exc).__name__}


async def ping_redis() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
        try:
            await client.ping()
        finally:
            await client.aclose()
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception as exc:  # noqa: BLE001
        log.warning("health_redis_failed", error=type(exc).__name__)
        return {"ok": False, "latency_ms": None, "error": type(exc).__name__}


def ffmpeg_status() -> dict[str, Any]:
    try:
        from ame.media.ffmpeg import find_ffmpeg

        path = find_ffmpeg()
    except Exception:
        path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    return {"ok": bool(path), "path": path, "error": None if path else "not_found"}


async def queue_depth(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(select(Job.status, func.count()).group_by(Job.status))
    ).all()
    counts = {status: int(count) for status, count in rows}
    queued = counts.get(JobStatus.QUEUED.value, 0)
    leased = counts.get(JobStatus.LEASED.value, 0)
    running = counts.get(JobStatus.RUNNING.value, 0)
    retry_wait = counts.get(JobStatus.RETRY_WAIT.value, 0)
    dead = counts.get(JobStatus.DEAD.value, 0)
    return {
        "queued": queued,
        "leased": leased,
        "running": running,
        "retry_wait": retry_wait,
        "dead": dead,
        "depth": queued + leased + retry_wait,
    }


async def worker_hint(session: AsyncSession) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Job.leased_by, func.count())
            .where(Job.status.in_([JobStatus.LEASED.value, JobStatus.RUNNING.value]))
            .where(Job.leased_by.is_not(None))
            .group_by(Job.leased_by)
        )
    ).all()
    leases = {str(name): int(count) for name, count in rows if name}
    if leases:
        hint = "Active worker leases detected. Compose service: worker."
    else:
        hint = (
            "No active worker leases. Start `python -m ame.jobs.worker` "
            "or `./scripts/dev.ps1` (Docker `worker` service is optional)."
        )
    return {"hint": hint, "active_leases": leases}


async def budget_health(session: AsyncSession, settings: Settings) -> dict[str, float | int]:
    try:
        total = await spend_today(session)
        ai = await spend_today(session, "ai")
        media = await spend_today(session, "media")
    except Exception:  # noqa: BLE001
        total = ai = media = 0.0
    return {
        "spent_today": _as_float(total),
        "ai_spent_today": _as_float(ai),
        "media_spent_today": _as_float(media),
        "daily_ai_spend_limit": settings.daily_ai_spend_limit,
        "daily_media_spend_limit": settings.daily_media_spend_limit,
        "daily_cost_limit": settings.daily_cost_limit,
        "max_content_per_day": settings.max_content_per_day,
    }


async def health_payload() -> dict[str, Any]:
    settings = get_settings()
    db = await ping_db()
    redis = await ping_redis()
    ffmpeg = ffmpeg_status()
    budget = {
        "spent_today": 0.0,
        "ai_spent_today": 0.0,
        "media_spent_today": 0.0,
        "daily_ai_spend_limit": settings.daily_ai_spend_limit,
        "daily_media_spend_limit": settings.daily_media_spend_limit,
        "daily_cost_limit": settings.daily_cost_limit,
        "max_content_per_day": settings.max_content_per_day,
    }
    queue = {"queued": 0, "leased": 0, "running": 0, "retry_wait": 0, "dead": 0, "depth": 0}
    worker = {
        "hint": "Start `python -m ame.jobs.worker` or `./scripts/dev.ps1`.",
        "active_leases": {},
    }
    if db["ok"]:
        try:
            async with async_session_factory() as session:
                budget = await budget_health(session, settings)
                queue = await queue_depth(session)
                worker = await worker_hint(session)
        except Exception:  # noqa: BLE001
            log.exception("health_extended_failed")
    if not db["ok"]:
        status = "down"
    elif not redis["ok"] or not ffmpeg["ok"] or queue["dead"] > 0:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "db": db,
        "redis": redis,
        "ffmpeg": ffmpeg,
        "dry_run": settings.dry_run,
        "budget": budget,
        "queue": queue,
        "worker": worker,
    }


async def _count_where(session: AsyncSession, stmt: Select[tuple[int]]) -> int:
    return int((await session.execute(stmt)).scalar_one())


async def _latest_snapshots(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    checkpoint: str | None = None,
) -> list[MetricSnapshot]:
    stmt = select(MetricSnapshot)
    if since is not None:
        stmt = stmt.where(MetricSnapshot.created_at >= since)
    if checkpoint is not None:
        stmt = stmt.where(MetricSnapshot.checkpoint == checkpoint)
    stmt = stmt.order_by(MetricSnapshot.created_at.desc())
    rows = list((await session.execute(stmt)).scalars().all())
    latest: dict[UUID, MetricSnapshot] = {}
    for row in rows:
        latest.setdefault(row.content_id, row)
    return list(latest.values())


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "median": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
        "count": len(values),
    }


def _performance_class(views: int) -> str:
    if views >= 1_000_000:
        return "viral"
    if views >= 100_000:
        return "breakout"
    if views >= 25_000:
        return "strong"
    if views >= 5_000:
        return "good"
    return "baseline"


async def _sum_actual_revenue(
    session: AsyncSession,
    *,
    kind: str,
    since: datetime | None = None,
) -> tuple[float | None, bool]:
    filters = [RevenueEvent.kind == kind]
    if since is not None:
        filters.append(RevenueEvent.created_at >= since)
    count = await _count_where(
        session, select(func.count()).select_from(RevenueEvent).where(*filters)
    )
    if count == 0:
        return None, False
    total = (
        await session.execute(
            select(func.coalesce(func.sum(RevenueEvent.amount), 0)).where(*filters)
        )
    ).scalar_one()
    return _as_float(total), True


async def _director_decision(session: AsyncSession) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(AgentDecisionRecord)
            .where(AgentDecisionRecord.agent == "director")
            .order_by(AgentDecisionRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "agent": row.agent,
        "decision": row.decision,
        "reason": row.reason,
        "confidence": row.confidence,
        "expected_effect": row.expected_effect,
        "created_at": row.created_at,
        "evidence": redact_secrets(row.evidence or {}),
    }


async def local_overview_metrics(session: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    start = _day_start()
    week = _now() - timedelta(days=7)
    produced = await _count_where(
        session,
        select(func.count())
        .select_from(ContentItem)
        .where(ContentItem.created_at >= start),
    )
    published = await _count_where(
        session,
        select(func.count())
        .select_from(Publication)
        .where(
            Publication.status == PublishStatus.PUBLISHED.value,
            Publication.created_at >= start,
        ),
    )
    rejected = await _count_where(
        session,
        select(func.count())
        .select_from(ContentItem)
        .where(ContentItem.status == ContentStatus.REJECTED.value, ContentItem.updated_at >= start),
    )
    qa_rejected = await _count_where(
        session,
        select(func.count())
        .select_from(QAResult)
        .where(QAResult.verdict == QAVerdict.REJECTED.value, QAResult.created_at >= start),
    )
    today_snaps = await _latest_snapshots(session, since=start)
    week_snaps = await _latest_snapshots(session, since=week)
    views_today = sum(int(item.views or 0) for item in today_snaps)
    views_7d = sum(int(item.views or 0) for item in week_snaps)
    followers_7d = sum(int(item.followers_gained or 0) for item in week_snaps)
    simulated = any(item.simulation for item in week_snaps)
    revenue_today, _ = await _sum_actual_revenue(
        session, kind=RevenueKind.ACTUAL.value, since=start
    )
    revenue_mtd, _ = await _sum_actual_revenue(
        session, kind=RevenueKind.ACTUAL.value, since=_month_start()
    )
    experiments_active = await _count_where(
        session,
        select(func.count()).select_from(Experiment).where(Experiment.status == "active"),
    )
    winning_topic = None
    if week_snaps:
        best = max(week_snaps, key=lambda item: int(item.views or 0))
        content = (
            await session.execute(select(ContentItem).where(ContentItem.id == best.content_id))
        ).scalar_one_or_none()
        if content is not None:
            winning_topic = content.topic
    if winning_topic is None:
        trend = (
            await session.execute(
                select(TrendSignal).order_by(TrendSignal.trend_score.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if trend is not None:
            winning_topic = trend.topic
    queue = await queue_depth(session)
    ffmpeg = ffmpeg_status()
    system_status = "degraded" if (not ffmpeg["ok"] or queue["dead"] > 0) else "running"
    return {
        "produced_today": produced,
        "published_today": published,
        "rejected_today": max(rejected, qa_rejected),
        "views_today": views_today,
        "views_7d": views_7d,
        "followers_7d": followers_7d,
        "revenue_today": revenue_today,
        "revenue_mtd": revenue_mtd,
        "experiments_active": experiments_active,
        "winning_topic": winning_topic,
        "director_decision": await _director_decision(session),
        "system_status": system_status,
        "dry_run": settings.dry_run,
        "simulation": simulated,
    }


async def overview_metrics(session: AsyncSession) -> dict[str, Any]:
    try:
        from ame.analytics.service import overview_metrics as external

        payload = await _maybe_await(external(session))
        if isinstance(payload, dict) and "produced_today" in payload:
            return await enrich_overview(session, payload)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("external_overview_failed")
    payload = await local_overview_metrics(session)
    return await enrich_overview(session, payload)


async def enrich_overview(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from ame.bootstrap.orchestrator import bootstrap_snapshot
    from ame.ops.human_actions import is_owner_visible
    from ame.ops.notifications import list_notifications, serialize_notification
    from ame.ops.reports import latest_report, serialize_report

    try:
        activation = await bootstrap_snapshot(session)
    except Exception:  # noqa: BLE001
        activation = {"platforms": {}}
    report = await latest_report(session)
    actions = [
        serialize_human_action(row)
        for row in (
            await session.execute(
                select(HumanAction).where(HumanAction.status == HumanActionStatus.OPEN.value)
            )
        ).scalars()
        if is_owner_visible(row)
    ]
    notes = [serialize_notification(row) for row in await list_notifications(session, limit=8)]
    payload["autonomous_mode"] = get_settings().autonomous_mode
    payload["owner_timezone"] = get_settings().owner_timezone
    payload["account_activation"] = activation.get("platforms") or {}
    payload["daily_report"] = serialize_report(report) if report else None
    payload["human_actions"] = actions
    payload["notifications"] = notes
    if actions:
        payload["system_status"] = "action required"
    return payload


async def list_content(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    total = await _count_where(session, select(func.count()).select_from(ContentItem))
    items = list(
        (
            await session.execute(
                select(ContentItem)
                .order_by(ContentItem.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    ids = [item.id for item in items]
    scripts: dict[UUID, Script] = {}
    qa_by_content: dict[UUID, QAResult] = {}
    pubs_by_content: dict[UUID, list[Publication]] = {}
    views_by_content: dict[UUID, MetricSnapshot] = {}
    if ids:
        script_ids = [item.selected_script_id for item in items if item.selected_script_id]
        selected = (Script.content_id.in_(ids)) & (Script.selected.is_(True))
        script_filter = Script.id.in_(script_ids) | selected if script_ids else selected
        script_rows = (await session.execute(select(Script).where(script_filter))).scalars().all()
        for script in script_rows:
            scripts[script.content_id] = script
            scripts[script.id] = script
        for qa in (
            await session.execute(
                select(QAResult)
                .where(QAResult.content_id.in_(ids))
                .order_by(QAResult.created_at.desc())
            )
        ).scalars().all():
            qa_by_content.setdefault(qa.content_id, qa)
        for pub in (
            await session.execute(select(Publication).where(Publication.content_id.in_(ids)))
        ).scalars().all():
            pubs_by_content.setdefault(pub.content_id, []).append(pub)
        for snap in (
            await session.execute(
                select(MetricSnapshot)
                .where(MetricSnapshot.content_id.in_(ids))
                .order_by(MetricSnapshot.created_at.desc())
            )
        ).scalars().all():
            views_by_content.setdefault(snap.content_id, snap)
    rows = []
    for item in items:
        script = None
        if item.selected_script_id and item.selected_script_id in scripts:
            script = scripts[item.selected_script_id]
        elif item.id in scripts:
            script = scripts[item.id]
        qa = qa_by_content.get(item.id)
        snap = views_by_content.get(item.id)
        rows.append(
            {
                "id": item.id,
                "topic": item.topic,
                "niche": item.niche,
                "status": item.status,
                "script": None
                if script is None
                else {
                    "id": script.id,
                    "hook": script.hook,
                    "candidate_label": script.candidate_label,
                    "selected": script.selected,
                },
                "platforms": [
                    {
                        "platform": pub.platform,
                        "status": pub.status,
                        "url": pub.url,
                        "simulation": pub.simulation,
                    }
                    for pub in pubs_by_content.get(item.id, [])
                ],
                "views": int(snap.views or 0) if snap else 0,
                "qa": None
                if qa is None
                else {"verdict": qa.verdict, "reasons": qa.reasons or []},
                "simulation": item.simulation or (snap.simulation if snap else False),
                "created_at": item.created_at,
            }
        )
    return {"items": rows, "limit": limit, "offset": offset, "total": total}


async def list_trends(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    total = await _count_where(session, select(func.count()).select_from(TrendSignal))
    signals = list(
        (
            await session.execute(
                select(TrendSignal)
                .order_by(TrendSignal.trend_score.desc(), TrendSignal.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    ids = [item.id for item in signals]
    opportunities: dict[UUID, Opportunity] = {}
    if ids:
        for opp in (
            await session.execute(
                select(Opportunity)
                .where(Opportunity.trend_signal_id.in_(ids))
                .order_by(Opportunity.created_at.desc())
            )
        ).scalars().all():
            opportunities.setdefault(opp.trend_signal_id, opp)
    rows = []
    for signal in signals:
        opp = opportunities.get(signal.id)
        rows.append(
            {
                "id": signal.id,
                "source": signal.source,
                "topic": signal.topic,
                "title": signal.title,
                "url": signal.url,
                "trend_score": signal.trend_score,
                "velocity": signal.velocity,
                "engagement_rate": signal.engagement_rate,
                "opportunity": None
                if opp is None
                else {
                    "id": opp.id,
                    "score": opp.score,
                    "status": opp.status,
                    "approved": opp.approved,
                    "explanation": opp.explanation,
                    "simulation": opp.simulation,
                },
                "simulation": signal.simulation,
                "observed_at": signal.observed_at,
                "created_at": signal.created_at,
            }
        )
    return {"items": rows, "limit": limit, "offset": offset, "total": total}


async def list_agents(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    run_total = await _count_where(session, select(func.count()).select_from(AgentRun))
    task_total = await _count_where(session, select(func.count()).select_from(AgentTask))
    decision_total = await _count_where(
        session, select(func.count()).select_from(AgentDecisionRecord)
    )
    runs = (
        await session.execute(
            select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    tasks = (
        await session.execute(
            select(AgentTask).order_by(AgentTask.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    decisions = (
        await session.execute(
            select(AgentDecisionRecord)
            .order_by(AgentDecisionRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "runs": [
            {
                "id": row.id,
                "agent": row.agent,
                "status": row.status,
                "duration_ms": row.duration_ms,
                "error": row.error,
                "content_id": row.content_id,
                "task_id": row.task_id,
                "created_at": row.created_at,
            }
            for row in runs
        ],
        "tasks": [
            {
                "id": row.id,
                "agent": row.agent,
                "status": row.status,
                "parent_task_id": row.parent_task_id,
                "content_id": row.content_id,
                "created_at": row.created_at,
            }
            for row in tasks
        ],
        "decisions": [
            {
                "id": row.id,
                "agent": row.agent,
                "decision": row.decision,
                "reason": row.reason,
                "confidence": row.confidence,
                "expected_effect": row.expected_effect,
                "content_id": row.content_id,
                "created_at": row.created_at,
                "evidence": redact_secrets(row.evidence or {}),
            }
            for row in decisions
        ],
        "limit": limit,
        "offset": offset,
        "totals": {"runs": run_total, "tasks": task_total, "decisions": decision_total},
    }


async def list_strategy(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    allocations = (
        await session.execute(
            select(StrategyAllocation)
            .order_by(StrategyAllocation.active.desc(), StrategyAllocation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    experiments = (
        await session.execute(
            select(Experiment)
            .order_by(Experiment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    recommendations = (
        await session.execute(
            select(LearningRecommendation)
            .order_by(
                LearningRecommendation.consumed.asc(),
                LearningRecommendation.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "allocations": [
            {
                "id": row.id,
                "niche": row.niche,
                "allocation": row.allocation,
                "reason": row.reason,
                "active": row.active,
                "decided_by": row.decided_by,
                "created_at": row.created_at,
            }
            for row in allocations
        ],
        "experiments": [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "locked": row.locked,
                "dimensions": row.dimensions,
                "created_at": row.created_at,
            }
            for row in experiments
        ],
        "recommendations": [
            {
                "id": row.id,
                "recommendation": row.recommendation,
                "method": row.method,
                "confidence": row.confidence,
                "consumed": row.consumed,
                "features": row.features or {},
                "created_at": row.created_at,
            }
            for row in recommendations
        ],
        "limit": limit,
        "offset": offset,
    }


async def local_distributions(session: AsyncSession, window: str) -> dict[str, Any]:
    since = _window_start(window)
    snaps = await _latest_snapshots(session, since=since)
    views = [float(item.views or 0) for item in snaps]
    likes = [float(item.likes or 0) for item in snaps]
    comments = [float(item.comments or 0) for item in snaps]
    shares = [float(item.shares or 0) for item in snaps]
    completion = [float(item.completion_rate) for item in snaps if item.completion_rate is not None]
    watch = [float(item.watch_time_seconds or 0) for item in snaps]
    by_platform: dict[str, dict[str, float | int | bool]] = {}
    classes = {"baseline": 0, "good": 0, "strong": 0, "breakout": 0, "viral": 0}
    simulated = False
    for item in snaps:
        simulated = simulated or item.simulation
        bucket = by_platform.setdefault(
            item.platform,
            {
                "platform": item.platform,
                "views": 0,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "followers_gained": 0,
                "count": 0,
                "simulation": False,
            },
        )
        bucket["views"] = int(bucket["views"]) + int(item.views or 0)
        bucket["likes"] = int(bucket["likes"]) + int(item.likes or 0)
        bucket["comments"] = int(bucket["comments"]) + int(item.comments or 0)
        bucket["shares"] = int(bucket["shares"]) + int(item.shares or 0)
        gained = int(item.followers_gained or 0)
        bucket["followers_gained"] = int(bucket["followers_gained"]) + gained
        bucket["count"] = int(bucket["count"]) + 1
        bucket["simulation"] = bool(bucket["simulation"]) or item.simulation
        classes[_performance_class(int(item.views or 0))] += 1
    return {
        "window": window,
        "totals": {
            "views": int(sum(views)),
            "likes": int(sum(likes)),
            "comments": int(sum(comments)),
            "shares": int(sum(shares)),
            "followers_gained": sum(int(item.followers_gained or 0) for item in snaps),
            "content": len(snaps),
        },
        "distributions": {
            "views": _distribution(views),
            "likes": _distribution(likes),
            "comments": _distribution(comments),
            "shares": _distribution(shares),
            "completion_rate": _distribution(completion),
            "watch_time_seconds": _distribution(watch),
        },
        "by_platform": list(by_platform.values()),
        "performance_classes": classes,
        "simulation": simulated,
    }


async def analytics_payload(session: AsyncSession, window: str) -> dict[str, Any]:
    if window not in ANALYTICS_WINDOWS:
        raise APIError(
            "invalid_window",
            "window must be one of 24h, 7d, 30d, lifetime",
            status_code=400,
            details={"window": window},
        )
    try:
        from ame.analytics.service import distributions as external

        payload = await _maybe_await(external(session, window))
        if isinstance(payload, dict):
            payload.setdefault("window", window)
            return payload
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("external_distributions_failed")
    return await local_distributions(session, window)


async def _revenue_bucket(
    session: AsyncSession,
    *,
    kind: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    today, today_data = await _sum_actual_revenue(session, kind=kind, since=_day_start())
    mtd, mtd_data = await _sum_actual_revenue(session, kind=kind, since=_month_start())
    lifetime, lifetime_data = await _sum_actual_revenue(session, kind=kind)
    platform_rows = (
        await session.execute(
            select(
                RevenueEvent.platform,
                func.coalesce(func.sum(RevenueEvent.amount), 0),
                func.count(),
                func.max(case((RevenueEvent.simulation.is_(True), 1), else_=0)),
            )
            .where(RevenueEvent.kind == kind)
            .group_by(RevenueEvent.platform)
        )
    ).all()
    events = (
        await session.execute(
            select(RevenueEvent)
            .where(RevenueEvent.kind == kind)
            .order_by(RevenueEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "today": today if today_data else None,
        "mtd": mtd if mtd_data else None,
        "lifetime": lifetime if lifetime_data else None,
        "has_data": lifetime_data,
        "by_platform": [
            {
                "platform": platform,
                "amount": _as_float(amount),
                "count": int(count),
                "simulation": bool(simulated),
            }
            for platform, amount, count, simulated in platform_rows
        ],
        "events": [
            {
                "id": row.id,
                "kind": row.kind,
                "amount": _as_float(row.amount),
                "currency": row.currency,
                "source": row.source,
                "platform": row.platform,
                "content_id": row.content_id,
                "period": row.period,
                "simulation": row.simulation,
                "created_at": row.created_at,
            }
            for row in events
        ],
    }


async def revenue_payload(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    try:
        from ame.revenue.queries import revenue_overview

        payload = await revenue_overview(session)
        if isinstance(payload, dict) and "actual" in payload and "forecast" in payload:
            events = await _revenue_bucket(
                session, kind=RevenueKind.ACTUAL.value, limit=limit, offset=offset
            )
            forecast_events = await _revenue_bucket(
                session, kind=RevenueKind.FORECAST.value, limit=limit, offset=offset
            )
            payload["actual"] = {**payload["actual"], "events": events["events"]}
            payload["forecast"] = {**payload["forecast"], "events": forecast_events["events"]}
            payload.setdefault("currency", get_settings().default_currency)
            payload["simulation"] = any(
                event.get("simulation") for event in forecast_events["events"]
            )
            return payload
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("external_revenue_failed")
    actual = await _revenue_bucket(
        session, kind=RevenueKind.ACTUAL.value, limit=limit, offset=offset
    )
    forecast = await _revenue_bucket(
        session, kind=RevenueKind.FORECAST.value, limit=limit, offset=offset
    )
    simulated = any(event["simulation"] for event in actual["events"] + forecast["events"])
    return {
        "actual": actual,
        "forecast": forecast,
        "currency": get_settings().default_currency,
        "simulation": simulated,
    }


def _publish_bucket(status: str) -> str:
    if status == PublishStatus.QUEUED.value:
        return "queued"
    if status == PublishStatus.PROCESSING.value:
        return "processing"
    if status == PublishStatus.PUBLISHED.value:
        return "published"
    if status == PublishStatus.FAILED.value:
        return "failed"
    if status == PublishStatus.RETRY.value:
        return "retry"
    if status in AWAITING_PUBLISH:
        return "awaiting"
    return "awaiting" if "await" in status else status


async def publishing_payload(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    counts = {
        key: 0 for key in ("queued", "processing", "published", "failed", "retry", "awaiting")
    }
    for status, count in (
        await session.execute(
            select(PublishingJob.status, func.count()).group_by(PublishingJob.status)
        )
    ).all():
        bucket = _publish_bucket(str(status))
        if bucket in counts:
            counts[bucket] += int(count)
    total = await _count_where(session, select(func.count()).select_from(PublishingJob))
    items = (
        await session.execute(
            select(PublishingJob)
            .order_by(PublishingJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "counts": counts,
        "items": [
            {
                "id": row.id,
                "content_id": row.content_id,
                "platform": row.platform,
                "status": row.status,
                "error": row.error,
                "simulation": row.simulation,
                "created_at": row.created_at,
            }
            for row in items
        ],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


def _client_configured(settings: Settings, platform: str) -> bool:
    if platform == Platform.YOUTUBE.value:
        return bool(settings.youtube_client_id)
    if platform == Platform.INSTAGRAM.value:
        return bool(settings.meta_app_id)
    if platform == Platform.TIKTOK.value:
        return bool(settings.tiktok_client_key)
    return False


def effective_connection_state(conn: PlatformConnection, settings: Settings) -> str:
    if conn.token_encrypted:
        if conn.expires_at and conn.expires_at < _now():
            return ConnectionState.NEEDS_REAUTHORIZATION.value
        if conn.state in {
            ConnectionState.READY.value,
            ConnectionState.CONNECTED.value,
            ConnectionState.NEEDS_PLATFORM_REVIEW.value,
        }:
            return conn.state
        return ConnectionState.CONNECTED.value
    if conn.state == ConnectionState.NEEDS_PLATFORM_REVIEW.value:
        return conn.state
    if not _client_configured(settings, conn.platform):
        return ConnectionState.NOT_CONFIGURED.value
    if conn.state == ConnectionState.NOT_CONFIGURED.value:
        return ConnectionState.CONNECTION_REQUIRED.value
    return conn.state or ConnectionState.CONNECTION_REQUIRED.value


def serialize_connection(conn: PlatformConnection, settings: Settings) -> dict[str, Any]:
    return {
        "id": conn.id,
        "platform": conn.platform,
        "state": effective_connection_state(conn, settings),
        "account_label": conn.account_label,
        "scopes": conn.scopes or [],
        "expires_at": conn.expires_at,
        "has_access_token": bool(conn.token_encrypted),
        "has_refresh_token": bool(conn.refresh_encrypted),
        "metadata": redact_secrets(conn.metadata_json or {}),
        "created_at": conn.created_at,
        "updated_at": conn.updated_at,
    }


def serialize_human_action(row: HumanAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "instructions": row.instructions,
        "category": row.category,
        "status": row.status,
        "platform": row.platform,
        "blocking": row.blocking,
        "classification": getattr(row, "classification", None),
        "checkpoint_kind": getattr(row, "checkpoint_kind", None),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def bootstrap_payload(session: AsyncSession) -> dict[str, Any]:
    try:
        from ame.bootstrap.service import get_bootstrap_snapshot

        snap = await get_bootstrap_snapshot(session)
        await session.commit()
        settings = get_settings()
        rows = (
            await session.execute(
                select(PlatformConnection).order_by(PlatformConnection.platform.asc())
            )
        ).scalars().all()
        by_platform = {item.platform: item for item in snap.connections}
        connections = []
        for row in rows:
            item = serialize_connection(row, settings)
            extra = by_platform.get(row.platform)
            if extra is not None:
                state = extra.state.value if hasattr(extra.state, "value") else extra.state
                item["state"] = state
                item["has_access_token"] = extra.token_present
                item["has_refresh_token"] = extra.refresh_present
                item["authorize_available"] = extra.authorize_available
                item["publish_gate"] = extra.publish_gate
                item["simulation_only"] = extra.simulation_only
            connections.append(item)
        actions = [
            {
                "id": action.id,
                "key": getattr(action, "key", None),
                "title": action.title,
                "instructions": action.instructions,
                "category": action.category,
                "status": action.status,
                "platform": action.platform,
                "blocking": action.blocking,
            }
            for action in snap.checklist
            if action.status == HumanActionStatus.OPEN.value
        ]
        return {
            "connections": connections,
            "human_actions": actions,
            "production_accounts_connected": snap.production_ready,
            "message": snap.message,
            "first_run": snap.first_run,
            "activation": getattr(snap, "activation", {}),
        }
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("external_bootstrap_failed")
    await seed_bootstrap(session)
    await session.commit()
    settings = get_settings()
    connections = (
        await session.execute(
            select(PlatformConnection).order_by(PlatformConnection.platform.asc())
        )
    ).scalars().all()
    actions = (
        await session.execute(
            select(HumanAction)
            .where(HumanAction.status == HumanActionStatus.OPEN.value)
            .order_by(HumanAction.created_at.asc())
        )
    ).scalars().all()
    serialized = [serialize_connection(row, settings) for row in connections]
    connected = {
        ConnectionState.CONNECTED.value,
        ConnectionState.READY.value,
    }
    production_ready = any(item["state"] in connected for item in serialized)
    message = (
        "Production social accounts are connected."
        if production_ready
        else "No production social accounts connected."
    )
    return {
        "connections": serialized,
        "human_actions": [serialize_human_action(row) for row in actions],
        "production_accounts_connected": production_ready,
        "message": message,
    }


async def list_human_actions(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    try:
        from ame.bootstrap.service import list_open_human_actions

        items = await list_open_human_actions(session)
        sliced = items[offset : offset + limit]
        return {
            "items": [item.model_dump() for item in sliced],
            "limit": limit,
            "offset": offset,
            "total": len(items),
        }
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("external_human_actions_failed")
    owner_filter = (
        HumanAction.status == HumanActionStatus.OPEN.value,
        HumanAction.category != "oauth_state",
    )
    stmt = select(HumanAction).where(*owner_filter)
    total = await _count_where(
        session,
        select(func.count()).select_from(HumanAction).where(*owner_filter),
    )
    items = (
        await session.execute(
            stmt.order_by(HumanAction.blocking.desc(), HumanAction.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "items": [serialize_human_action(row) for row in items],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


async def complete_human_action(session: AsyncSession, action_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(select(HumanAction).where(HumanAction.id == action_id))
    ).scalar_one_or_none()
    if row is None:
        raise APIError("not_found", "Human action not found", status_code=404)
    row.status = HumanActionStatus.COMPLETED.value
    await session.flush()
    try:
        from ame.bootstrap.orchestrator import resume_after_human_action

        await resume_after_human_action(session, row)
    except Exception:  # noqa: BLE001
        log.exception("bootstrap_resume_failed")
    await session.commit()
    await session.refresh(row)
    return serialize_human_action(row)


async def run_cycle(session: AsyncSession) -> dict[str, Any]:
    correlation_id = str(uuid4())
    director = await enqueue(
        session,
        JobName.DIRECTOR_TICK.value,
        {"trigger": "api.run_cycle"},
        idempotency_key=f"api:run-cycle:director:{correlation_id}",
        correlation_id=correlation_id,
    )
    trend = await enqueue(
        session,
        JobName.TREND_INGEST.value,
        {"trigger": "api.run_cycle"},
        idempotency_key=f"api:run-cycle:trend:{correlation_id}",
        correlation_id=correlation_id,
    )
    await session.commit()
    return {
        "job_ids": {
            "director_tick": str(director.id),
            "trend_ingest": str(trend.id),
        },
        "jobs": [
            {"id": str(director.id), "name": director.name, "status": director.status},
            {"id": str(trend.id), "name": trend.name, "status": trend.status},
        ],
        "correlation_id": correlation_id,
        "blocked": False,
    }


async def list_events(session: AsyncSession, limit: int, offset: int) -> dict[str, Any]:
    total = await _count_where(session, select(func.count()).select_from(SystemEvent))
    items = (
        await session.execute(
            select(SystemEvent)
            .order_by(SystemEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "payload": redact_secrets(row.payload or {}),
                "correlation_id": row.correlation_id,
                "workflow_id": row.workflow_id,
                "content_id": row.content_id,
                "agent_run_id": row.agent_run_id,
                "simulation": row.simulation,
                "created_at": row.created_at,
            }
            for row in items
        ],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


async def get_platform_connection(
    session: AsyncSession, platform: str
) -> PlatformConnection | None:
    return (
        await session.execute(
            select(PlatformConnection).where(PlatformConnection.platform == platform)
        )
    ).scalar_one_or_none()


async def upsert_platform_connection(session: AsyncSession, platform: str) -> PlatformConnection:
    row = await get_platform_connection(session, platform)
    if row is not None:
        return row
    row = PlatformConnection(
        platform=platform,
        state=ConnectionState.NOT_CONFIGURED.value,
        scopes=[],
        metadata_json={},
    )
    session.add(row)
    await session.flush()
    return row
