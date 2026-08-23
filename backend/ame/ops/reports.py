"""Daily executive report persisted once per owner-local day."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import (
    ContentStatus,
    HumanActionStatus,
    NotificationKind,
    PublishStatus,
)
from ame.db.models import (
    AccountBootstrap,
    AgentDecisionRecord,
    ContentItem,
    ExecutiveReport,
    HumanAction,
    Job,
    LearningRecommendation,
    MetricSnapshot,
    Opportunity,
    Publication,
    QAResult,
    RevenueEvent,
    SystemEvent,
    TrendSignal,
)
from ame.ops.clock import near_owner_day_end, owner_day_bounds, owner_local_date, owner_zone
from ame.ops.human_actions import is_owner_visible
from ame.ops.notifications import notify


async def generate_daily_report(session: AsyncSession, *, finalize: bool | None = None) -> ExecutiveReport:
    local_date = owner_local_date()
    timezone = str(owner_zone())
    result = await session.execute(
        select(ExecutiveReport).where(
            ExecutiveReport.local_date == local_date,
            ExecutiveReport.timezone == timezone,
        )
    )
    report = result.scalar_one_or_none()
    start, end = owner_day_bounds()
    body = await _build_body(session, start, end)
    should_finalize = finalize if finalize is not None else near_owner_day_end()
    headline = _headline(body)
    if report is None:
        report = ExecutiveReport(
            local_date=local_date,
            timezone=timezone,
            status="final" if should_finalize else "draft",
            headline=headline,
            body=body,
            finalized=should_finalize,
        )
        session.add(report)
        await session.flush()
        await notify(
            session,
            NotificationKind.DAILY_REPORT_READY,
            "Daily executive report ready",
            headline,
            related_entity_type="executive_report",
            related_entity_id=report.id,
        )
        session.add(SystemEvent(name="daily_report.ready", payload={"local_date": local_date}))
    else:
        report.body = body
        report.headline = headline
        if should_finalize:
            report.finalized = True
            report.status = "final"
    await session.flush()
    return report


async def latest_report(session: AsyncSession) -> ExecutiveReport | None:
    result = await session.execute(
        select(ExecutiveReport).order_by(ExecutiveReport.local_date.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def list_reports(session: AsyncSession, limit: int = 14) -> list[ExecutiveReport]:
    result = await session.execute(
        select(ExecutiveReport).order_by(ExecutiveReport.local_date.desc()).limit(limit)
    )
    return list(result.scalars().all())


def serialize_report(row: ExecutiveReport) -> dict[str, Any]:
    data = {
        "id": str(row.id),
        "local_date": row.local_date,
        "timezone": row.timezone,
        "status": row.status,
        "headline": row.headline,
        "body": dict(row.body or {}),
        "finalized": bool(row.finalized),
    }
    created = row.__dict__.get("created_at")
    updated = row.__dict__.get("updated_at")
    data["created_at"] = created.isoformat() if created else None
    data["updated_at"] = updated.isoformat() if updated else None
    return data


async def handle_daily_report(session: AsyncSession, job: Job) -> None:
    await generate_daily_report(session)
    _ = job


def _headline(body: dict[str, Any]) -> str:
    today = body.get("today") or {}
    return (
        f"{body.get('date')} · produced {today.get('videos_produced', 0)} · "
        f"published {today.get('published', 0)} · system {body.get('system', {}).get('status')}"
    )[:240]


async def _count(session: AsyncSession, stmt) -> int:
    return int(await session.scalar(stmt) or 0)


async def _build_body(session: AsyncSession, start, end) -> dict[str, Any]:
    trends = await _count(
        session, select(func.count()).select_from(TrendSignal).where(TrendSignal.created_at >= start)
    )
    opportunities = await _count(
        session,
        select(func.count()).select_from(Opportunity).where(Opportunity.created_at >= start),
    )
    research = await _count(
        session,
        select(func.count())
        .select_from(ContentItem)
        .where(ContentItem.created_at >= start, ContentItem.status != ContentStatus.DISCOVERED.value),
    )
    produced = await _count(
        session,
        select(func.count())
        .select_from(ContentItem)
        .where(ContentItem.created_at >= start, ContentItem.status.in_([
            ContentStatus.APPROVED.value,
            ContentStatus.PUBLISHING.value,
            ContentStatus.PUBLISHED.value,
            ContentStatus.MEASURING.value,
            ContentStatus.LEARNING_COMPLETE.value,
        ])),
    )
    qa_approved = await _count(
        session,
        select(func.count()).select_from(QAResult).where(
            QAResult.created_at >= start, QAResult.verdict == "approved"
        ),
    )
    qa_rejected = await _count(
        session,
        select(func.count()).select_from(QAResult).where(
            QAResult.created_at >= start, QAResult.verdict == "rejected"
        ),
    )
    published = await _count(
        session,
        select(func.count()).select_from(Publication).where(
            Publication.created_at >= start,
            Publication.status.in_([PublishStatus.PUBLISHED.value, "published"]),
        ),
    )
    pubs = list(
        (
            await session.execute(
                select(Publication).where(Publication.created_at >= start).order_by(Publication.created_at.desc())
            )
        ).scalars()
    )
    snaps = list((await session.execute(select(MetricSnapshot))).scalars())
    views_today = sum(item.views for item in snaps if item.created_at and item.created_at >= start)
    views_7d = sum(s.views for s in snaps)
    followers = sum(s.followers_gained for s in snaps)
    watch = sum(s.watch_time_seconds or 0 for s in snaps)
    completions = [s.completion_rate for s in snaps if s.completion_rate is not None]
    shares = sum(s.shares for s in snaps)
    actual_rev = list(
        (
            await session.execute(select(RevenueEvent).where(RevenueEvent.kind == "actual"))
        ).scalars()
    )
    rev_today = sum(float(r.amount) for r in actual_rev if r.created_at and r.created_at >= start)
    rev_mtd = sum(float(r.amount) for r in actual_rev)
    decisions = list(
        (
            await session.execute(
                select(AgentDecisionRecord)
                .where(AgentDecisionRecord.agent == "director", AgentDecisionRecord.created_at >= start)
                .order_by(AgentDecisionRecord.created_at.desc())
                .limit(12)
            )
        ).scalars()
    )
    recovered = list(
        (
            await session.execute(
                select(SystemEvent)
                .where(SystemEvent.name.in_(["ops.job_recovered", "ops.quarantined"]))
                .where(SystemEvent.created_at >= start)
                .limit(20)
            )
        ).scalars()
    )
    actions = [
        row
        for row in (await session.execute(select(HumanAction).where(HumanAction.status == HumanActionStatus.OPEN.value))).scalars()
        if is_owner_visible(row)
    ]
    bootstraps = list((await session.execute(select(AccountBootstrap))).scalars())
    best = None
    if snaps:
        top = max(snaps, key=lambda item: item.views)
        pub = next((p for p in pubs if p.id == top.publication_id), None)
        content = await session.get(ContentItem, top.content_id) if top.content_id else None
        best = {
            "title": (content.topic if content else None) or (pub.title if pub else None),
            "platform": top.platform,
            "url": pub.url if pub else None,
            "views": top.views,
            "retention": top.completion_rate,
            "why": "Highest observed views in current snapshots.",
        }
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for pub in pubs:
        by_platform.setdefault(pub.platform, []).append(
            {
                "title": pub.title,
                "url": pub.url,
                "status": pub.status,
                "simulation": pub.simulation,
            }
        )
    system = "running"
    if actions:
        system = "action required"
    elif any(row.state in {"restricted", "failed_retryable"} for row in bootstraps):
        system = "degraded"
    return {
        "title": "AUTONOMOUS MEDIA ENGINE DAILY REPORT",
        "date": owner_local_date(),
        "timezone": str(owner_zone()),
        "system": {"status": system},
        "accounts": {
            row.platform: {
                "state": row.state,
                "blocked_reason": row.blocked_reason,
                "checkpoint": row.checkpoint_kind,
            }
            for row in bootstraps
        },
        "today": {
            "trends_analysed": trends,
            "opportunities_scored": opportunities,
            "research_jobs": research,
            "videos_produced": produced,
            "qa_approved": qa_approved,
            "qa_rejected": qa_rejected,
            "published": published,
        },
        "by_platform": by_platform,
        "performance": {
            "views_today": views_today,
            "views_7d": views_7d,
            "followers_gained": followers,
            "watch_time": watch,
            "completion": round(sum(completions) / len(completions), 4) if completions else None,
            "shares": shares,
        },
        "revenue": {
            "actual_today": rev_today,
            "actual_mtd": rev_mtd,
            "forecast_note": "Forecast remains separate and is never treated as actual.",
        },
        "best_content": best,
        "director_decisions": [
            {"decision": row.decision, "reason": row.reason, "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in decisions
        ],
        "experiments": {
            "winning": [],
            "losing": [],
            "still_collecting_data": True,
        },
        "failures_recovered": [
            {"name": row.name, "payload": row.payload} for row in recovered
        ],
        "owner_action_required": [
            {"title": row.title, "platform": row.platform, "instructions": row.instructions}
            for row in actions
        ]
        or "None",
        "next_plan": "Continue autonomous production within owner caps; advance ready platforms only.",
    }
