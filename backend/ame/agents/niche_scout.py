from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import (
    AgentMessage,
    AgentTask,
    ContentItem,
    Job,
    MetricSnapshot,
    StrategyAllocation,
    TrendSignal,
)
from ame.observability import get_logger

logger = get_logger("ame.agent.niche_scout")

DIMENSIONS = (
    "demand",
    "growth",
    "supply",
    "difficulty",
    "monetization",
    "originality",
    "shelf",
    "advertiser",
    "platform",
    "factual_risk",
)


class NicheScoutAgent(Agent):
    name = AgentName.NICHE_SCOUT

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        signals = await _load_signals(self.session)
        snapshots = await _load_snapshots(self.session)
        allocations = await _load_allocations(self.session)
        scores = _score_niches(signals, snapshots, allocations)
        recommendation = _recommend(scores)
        self.session.add(
            AgentMessage(
                from_agent=self.name.value,
                to_agent=AgentName.DIRECTOR.value,
                kind="niche_evaluation",
                body={
                    "scores": scores,
                    "recommendation": recommendation,
                    "allocation_changed": False,
                    "director_applies_allocation": True,
                },
                content_id=agent_input.content_id,
            )
        )
        await self.session.flush()
        logger.info("niche_scored", count=len(scores), top=recommendation.get("niche"))
        confidence = 0.35 if not scores else min(0.8, 0.4 + 0.05 * len(signals))
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "scores": scores,
                "recommendation": recommendation,
                "enqueued": [],
                "allocation_changed": False,
            },
            decision=AgentDecision(
                decision="report_niche_scores",
                reason=(
                    "Niches scored from trend signals and metric snapshots. "
                    "Director applies allocation changes; this agent enqueues nothing."
                ),
                evidence={
                    "signal_count": len(signals),
                    "snapshot_count": len(snapshots),
                    "top": recommendation,
                },
                confidence=confidence,
                expected_effect="director_may_reallocate_within_caps",
                related_entity_type="strategy_allocation",
            ),
        )


async def handle_niche_evaluate(session: AsyncSession, job: Job) -> None:
    agent_input, context, task = await _start(session, job, AgentName.NICHE_SCOUT)
    result = await NicheScoutAgent(session).run(agent_input, context)
    task.status = _task_status(result)
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "niche_evaluate_failed")


async def _start(
    session: AsyncSession, job: Job, agent: AgentName
) -> tuple[AgentInput, AgentContext, AgentTask]:
    payload = dict(job.payload or {})
    content_id = job.content_id
    raw = payload.get("content_id")
    if content_id is None and raw:
        content_id = UUID(str(raw))
    task = AgentTask(
        agent=agent.value,
        status="running",
        payload=payload,
        content_id=content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    content = await session.get(ContentItem, content_id) if content_id else None
    status: ContentStatus | None = None
    if content is not None:
        try:
            status = ContentStatus(content.status)
        except ValueError:
            status = None
    settings = get_settings()
    return (
        AgentInput(
            task_id=task.id,
            agent=agent,
            payload=payload,
            content_id=content_id,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id or (content.workflow_id if content else None),
        ),
        AgentContext(
            content_id=content_id,
            status=status,
            dry_run=settings.dry_run,
            simulation=bool(content.simulation) if content is not None else True,
            extra=payload,
        ),
        task,
    )


def _task_status(result: AgentResult) -> str:
    if result.status in {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.SKIPPED,
        AgentRunStatus.BUDGET_BLOCKED,
    }:
        return "succeeded"
    return "failed"


async def _load_signals(session: AsyncSession) -> list[TrendSignal]:
    result = await session.execute(
        select(TrendSignal).order_by(TrendSignal.trend_score.desc()).limit(80)
    )
    return list(result.scalars().all())


async def _load_snapshots(session: AsyncSession) -> list[tuple[MetricSnapshot, str | None]]:
    result = await session.execute(
        select(MetricSnapshot, ContentItem.niche)
        .join(ContentItem, ContentItem.id == MetricSnapshot.content_id)
        .order_by(MetricSnapshot.created_at.desc())
        .limit(80)
    )
    return [(row[0], row[1]) for row in result.all()]


async def _load_allocations(session: AsyncSession) -> list[StrategyAllocation]:
    result = await session.execute(
        select(StrategyAllocation).where(StrategyAllocation.active.is_(True))
    )
    return list(result.scalars().all())


def _classify_niche(topic: str) -> str:
    text = topic.lower()
    if any(token in text for token in ("ai", "llm", "model", "gpt", "neural")):
        return "ai_systems"
    if any(token in text for token in ("chip", "semiconductor", "hardware", "robot")):
        return "engineering"
    if any(token in text for token in ("market", "startup", "funding", "ipo")):
        return "markets"
    if any(token in text for token in ("policy", "regulation", "law", "privacy")):
        return "policy_tech"
    if any(token in text for token in ("science", "biology", "physics", "climate")):
        return "science"
    return "technology"


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _score_niches(
    signals: list[TrendSignal],
    snapshots: list[tuple[MetricSnapshot, str | None]],
    allocations: list[StrategyAllocation],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[TrendSignal]] = defaultdict(list)
    for signal in signals:
        grouped[_classify_niche(signal.topic)].append(signal)
    for allocation in allocations:
        grouped.setdefault(allocation.niche, [])

    metrics_by_niche: dict[str, list[MetricSnapshot]] = defaultdict(list)
    for snapshot, niche in snapshots:
        key = niche or "technology"
        metrics_by_niche[key].append(snapshot)
        grouped.setdefault(key, [])

    scored: list[dict[str, Any]] = []
    for niche, group in grouped.items():
        count = max(len(group), 1)
        avg_score = sum(item.trend_score for item in group) / count if group else 0.15
        avg_velocity = sum(item.velocity for item in group) / count if group else 0.0
        avg_engage = sum(item.engagement_rate for item in group) / count if group else 0.0
        avg_age = sum(item.age_hours for item in group) / count if group else 72.0
        avg_risk = sum(item.risk_score for item in group) / count if group else 0.2
        avg_authority = sum(item.source_authority for item in group) / count if group else 0.4
        avg_cross = sum(item.cross_platform_count for item in group) / count if group else 1.0
        views = sum((item.views or 0) for item in group)

        snaps = metrics_by_niche.get(niche, [])
        snap_views = sum(item.views for item in snaps)
        snap_complete = (
            sum((item.completion_rate or 0.0) for item in snaps) / len(snaps) if snaps else 0.0
        )
        snap_engage = 0.0
        if snaps:
            snap_engage = sum(
                (item.likes + item.comments + item.shares) / max(item.views, 1) for item in snaps
            ) / len(snaps)

        demand = _clip(
            0.55 * _clip(avg_score)
            + 0.25 * _clip(views / 50_000)
            + 0.2 * _clip(snap_views / 20_000)
        )
        growth = _clip(0.7 * _clip(avg_velocity / 10.0) + 0.3 * _clip(snap_engage * 8))
        supply = _clip(len(group) / 12.0)
        difficulty = _clip(0.5 * supply + 0.5 * avg_risk)
        monetization = _clip(
            0.4 * _clip(avg_engage * 4)
            + 0.3 * _clip(avg_authority)
            + 0.3 * _clip(snap_complete)
        )
        originality = _clip(1.0 - min(0.85, supply * 0.7 + _clip((avg_cross - 1) / 4.0) * 0.3))
        shelf = _clip(1.0 - min(0.9, avg_age / 168.0))
        advertiser = _clip((1.0 - avg_risk) * max(0.35, avg_authority))
        platform = _clip(avg_cross / 3.0)
        factual_risk = _clip(avg_risk)
        total = _clip(
            0.14 * demand
            + 0.12 * growth
            + 0.06 * (1.0 - supply)
            + 0.08 * (1.0 - difficulty)
            + 0.12 * monetization
            + 0.12 * originality
            + 0.08 * shelf
            + 0.10 * advertiser
            + 0.08 * platform
            + 0.10 * (1.0 - factual_risk)
        )
        scored.append(
            {
                "niche": niche,
                "demand": round(demand, 4),
                "growth": round(growth, 4),
                "supply": round(supply, 4),
                "difficulty": round(difficulty, 4),
                "monetization": round(monetization, 4),
                "originality": round(originality, 4),
                "shelf": round(shelf, 4),
                "advertiser": round(advertiser, 4),
                "platform": round(platform, 4),
                "factual_risk": round(factual_risk, 4),
                "total": round(total, 4),
                "signal_count": len(group),
                "snapshot_count": len(snaps),
            }
        )
    scored.sort(key=lambda item: item["total"], reverse=True)
    return scored


def _recommend(scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not scores:
        return {
            "action": "hold",
            "niche": None,
            "reason": "No trend signals or snapshots to score.",
        }
    top = scores[0]
    return {
        "action": "prefer",
        "niche": top["niche"],
        "total": top["total"],
        "reason": (
            f"{top['niche']} leads on demand/growth/originality; "
            f"factual_risk={top['factual_risk']}."
        ),
        "dimensions": list(DIMENSIONS),
    }
