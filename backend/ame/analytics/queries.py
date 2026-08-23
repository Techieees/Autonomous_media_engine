from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.checkpoints import preferred_snapshot
from ame.analytics.classify import (
    classify_performance,
    distribution_block,
    load_thresholds,
    thresholds_payload,
)
from ame.config import get_settings
from ame.contracts.enums import ContentStatus, JobStatus
from ame.db.models import (
    AgentDecisionRecord,
    ContentItem,
    Experiment,
    Job,
    MetricSnapshot,
    Publication,
)
from ame.revenue.queries import revenue_overview


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _day_start(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _metrics_usable(snapshot: MetricSnapshot) -> bool:
    raw = snapshot.raw or {}
    if snapshot.simulation:
        return True
    if raw.get("metrics_available") is False:
        return False
    if raw.get("source") == "unavailable":
        return False
    return True


def _latest_by_publication(
    rows: list[MetricSnapshot], *, window: str | None = None
) -> list[MetricSnapshot]:
    grouped: dict[Any, list[MetricSnapshot]] = defaultdict(list)
    for row in rows:
        grouped[row.publication_id].append(row)
    latest: list[MetricSnapshot] = []
    for items in grouped.values():
        chosen = preferred_snapshot(items, window=window)
        if chosen is not None:
            latest.append(chosen)
    return latest


def _sum_views(rows: list[MetricSnapshot]) -> int:
    return sum(item.views for item in rows if _metrics_usable(item))


def _sum_followers(rows: list[MetricSnapshot]) -> int:
    return sum(item.followers_gained for item in rows if _metrics_usable(item))


def _split(rows: list[MetricSnapshot]) -> tuple[list[MetricSnapshot], list[MetricSnapshot]]:
    actual = [item for item in rows if not item.simulation]
    simulated = [item for item in rows if item.simulation]
    return actual, simulated


async def _load_snapshots(session: AsyncSession) -> list[MetricSnapshot]:
    result = await session.execute(select(MetricSnapshot))
    return list(result.scalars().all())


async def overview_metrics(session: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(UTC)
    start = _day_start(now)
    week = now - timedelta(days=7)
    snapshots = await _load_snapshots(session)

    today_snaps = [item for item in snapshots if _aware(item.created_at) >= start]
    week_snaps = [item for item in snapshots if _aware(item.created_at) >= week]
    latest_today = _latest_by_publication(today_snaps)
    latest_week = _latest_by_publication(week_snaps)
    actual_today, sim_today = _split(latest_today)
    actual_week, sim_week = _split(latest_week)

    produced = await session.scalar(
        select(func.count(ContentItem.id)).where(ContentItem.created_at >= start)
    )
    published = await session.scalar(
        select(func.count(Publication.id)).where(
            Publication.created_at >= start,
            Publication.status.in_(["published", ContentStatus.PUBLISHED.value]),
        )
    )
    rejected = await session.scalar(
        select(func.count(ContentItem.id)).where(
            ContentItem.status == ContentStatus.REJECTED.value,
            ContentItem.updated_at >= start,
        )
    )
    experiments = await session.scalar(
        select(func.count(Experiment.id)).where(Experiment.status == "active")
    )
    dead = await session.scalar(
        select(func.count(Job.id)).where(
            Job.status == JobStatus.DEAD.value,
            Job.updated_at >= now - timedelta(hours=24),
        )
    )
    decision_row = (
        await session.execute(
            select(AgentDecisionRecord)
            .where(AgentDecisionRecord.agent == "director")
            .order_by(AgentDecisionRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    winning = await _winning_topic(session, snapshots)
    money = await revenue_overview(session)

    actual_views_today = _sum_views(actual_today)
    actual_views_7d = _sum_views(actual_week)
    actual_followers_7d = _sum_followers(actual_week)
    has_actual_views = any(_metrics_usable(item) for item in actual_week)
    revenue_today = money["actual"]["today"]
    revenue_mtd = money["actual"]["mtd"]

    system_status = "degraded" if (dead or 0) > 5 else "running"
    return {
        "produced_today": int(produced or 0),
        "published_today": int(published or 0),
        "rejected_today": int(rejected or 0),
        "views_today": actual_views_today if has_actual_views else None,
        "views_7d": actual_views_7d if has_actual_views else None,
        "followers_7d": actual_followers_7d if has_actual_views else None,
        "revenue_today": revenue_today,
        "revenue_mtd": revenue_mtd,
        "experiments_active": int(experiments or 0),
        "winning_topic": winning,
        "director_decision": _decision_payload(decision_row),
        "system_status": system_status,
        "dry_run": settings.dry_run,
        "actual": {
            "views_today": actual_views_today if has_actual_views else None,
            "views_7d": actual_views_7d if has_actual_views else None,
            "followers_7d": actual_followers_7d if has_actual_views else None,
            "revenue_today": revenue_today,
            "revenue_mtd": revenue_mtd,
            "simulation": False,
        },
        "simulation": {
            "views_today": _sum_views(sim_today),
            "views_7d": _sum_views(sim_week),
            "followers_7d": _sum_followers(sim_week),
            "labeled": True,
            "simulation": True,
            "note": "Synthetic dry-run metrics. Not actual platform reach.",
        },
        "forecast": money["forecast"],
    }


async def distributions(session: AsyncSession, window: str = "24h") -> dict[str, Any]:
    snapshots = await _load_snapshots(session)
    latest = _latest_by_publication(snapshots, window=window)
    actual, simulated = _split(latest)
    thresholds = load_thresholds()
    return {
        "window": window,
        "actual": _distribution_side(actual, thresholds),
        "simulation": {
            **_distribution_side(simulated, thresholds),
            "labeled": True,
            "simulation": True,
            "note": "Synthetic dry-run distributions. Not actual performance.",
        },
        "thresholds": thresholds_payload(thresholds),
    }


def _distribution_side(
    rows: list[MetricSnapshot],
    thresholds: Any,
) -> dict[str, Any]:
    usable = [item for item in rows if _metrics_usable(item)]
    views = [float(item.views) for item in usable]
    completion = [float(item.completion_rate) for item in usable if item.completion_rate is not None]
    shares = [float(item.shares) for item in usable]
    watch = [float(item.watch_time_seconds) for item in usable]
    followers = [float(item.followers_gained) for item in usable]
    likes = [float(item.likes) for item in usable]
    comments = [float(item.comments) for item in usable]
    classes: dict[str, int] = defaultdict(int)
    by_platform: dict[str, list[float]] = defaultdict(list)
    for item in usable:
        label = classify_performance(
            views=item.views,
            completion_rate=item.completion_rate,
            shares=item.shares,
            followers_gained=item.followers_gained,
            thresholds=thresholds,
        )
        classes[label.value] += 1
        by_platform[item.platform].append(float(item.views))
    return {
        "n": len(usable),
        "views": distribution_block(views),
        "likes": distribution_block(likes),
        "comments": distribution_block(comments),
        "shares": distribution_block(shares),
        "completion": distribution_block(completion),
        "watch_time_seconds": distribution_block(watch),
        "followers_gained": distribution_block(followers),
        "classes": {
            "baseline": classes.get("baseline", 0),
            "good": classes.get("good", 0),
            "strong": classes.get("strong", 0),
            "breakout": classes.get("breakout", 0),
            "viral": classes.get("viral", 0),
        },
        "by_platform": {
            platform: distribution_block(values) for platform, values in by_platform.items()
        },
        "simulation": False,
    }


async def _winning_topic(
    session: AsyncSession, snapshots: list[MetricSnapshot]
) -> dict[str, Any] | None:
    latest = _latest_by_publication(snapshots, window="24h")
    usable = [item for item in latest if _metrics_usable(item)]
    if not usable:
        return None
    actual = [item for item in usable if not item.simulation]
    pool = actual or usable
    winner = max(pool, key=lambda item: item.views)
    content = await session.get(ContentItem, winner.content_id)
    if content is None:
        return None
    return {
        "topic": content.topic,
        "niche": content.niche,
        "views": winner.views,
        "checkpoint": winner.checkpoint,
        "simulation": winner.simulation,
        "note": (
            "Synthetic dry-run leader. Not actual reach."
            if winner.simulation
            else None
        ),
    }


def _decision_payload(row: AgentDecisionRecord | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "decision": row.decision,
        "reason": row.reason,
        "confidence": row.confidence,
        "expected_effect": row.expected_effect,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
