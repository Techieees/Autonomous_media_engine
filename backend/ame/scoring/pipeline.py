from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import ContentStatus
from ame.costs.tracker import BudgetExceeded, assert_budget, produced_today
from ame.db.models import ContentItem, Opportunity, StrategyAllocation, SystemEvent, TrendSignal
from ame.llm import get_llm
from ame.observability import get_logger
from ame.scoring.scorer import SCORE_FORMULA, score_signal

logger = get_logger("ame.scoring")

RECENT_SIGNAL_HOURS = 72
EVENT_NAME = "opportunity.scored"


async def score_recent_signals(
    session: AsyncSession,
    *,
    payload: dict[str, Any] | None,
    correlation_id: str | None,
    workflow_id: UUID | None,
    simulation: bool,
) -> dict[str, Any]:
    settings = get_settings()
    body = payload or {}
    recent_hours = int(body.get("recent_hours") or RECENT_SIGNAL_HOURS)
    requested_id = _as_uuid(body.get("trend_signal_id"))
    niches = await _active_niches(session)
    existing_topics = list(
        (await session.execute(select(ContentItem.topic))).scalars().all()
    )
    signals = await _load_unscored_signals(session, requested_id, recent_hours)
    created: list[dict[str, Any]] = []
    for signal in signals:
        scored = score_signal(signal, existing_topics, niches)
        explanation = scored["explanation"]
        extra = await _optional_llm_note(session, signal.topic, scored["features"])
        if extra:
            explanation = f"{explanation} {extra}".strip()
            scored["explanation"] = explanation
        opportunity = Opportunity(
            trend_signal_id=signal.id,
            score=float(scored["score"]),
            explanation=explanation,
            features=scored["features"],
            status="scored",
            approved=False,
            simulation=bool(signal.simulation or simulation),
        )
        session.add(opportunity)
        await session.flush()
        signal.trend_score = float(scored["score"])
        created.append(
            {
                "opportunity_id": opportunity.id,
                "trend_signal_id": signal.id,
                "score": float(scored["score"]),
                "topic": signal.topic,
                "title": signal.title,
                "niche": scored["features"].get("matched_niche"),
                "simulation": opportunity.simulation,
                "explanation": explanation,
                "features": scored["features"],
            }
        )
        existing_topics.append(signal.topic)
        logger.info(
            "opportunity_scored",
            opportunity_id=str(opportunity.id),
            trend_signal_id=str(signal.id),
            score=float(scored["score"]),
        )
    await session.flush()
    content_ids = await _promote_top_opportunities(
        session,
        created,
        settings_top=int(body.get("top_n") or settings.target_daily_content),
        simulation=simulation,
        workflow_id=workflow_id,
    )
    for row in created:
        content_id = row.get("content_id")
        session.add(
            SystemEvent(
                name=EVENT_NAME,
                payload={
                    "opportunity_id": str(row["opportunity_id"]),
                    "trend_signal_id": str(row["trend_signal_id"]),
                    "score": row["score"],
                    "topic": row["topic"],
                    "content_id": str(content_id) if content_id else None,
                    "explanation": row["explanation"],
                    "features": row["features"],
                    "formula": SCORE_FORMULA,
                },
                correlation_id=correlation_id,
                workflow_id=workflow_id,
                content_id=content_id,
                simulation=row["simulation"],
            )
        )
    created.sort(key=lambda item: item["score"], reverse=True)
    summary = (
        f"Scored {len(created)} new opportunities with {SCORE_FORMULA}."
        if created
        else "No recent TrendSignals lacked an Opportunity."
    )
    return {
        "scored_count": len(created),
        "content_created": [str(item) for item in content_ids],
        "opportunities": [
            {
                "opportunity_id": str(item["opportunity_id"]),
                "trend_signal_id": str(item["trend_signal_id"]),
                "score": item["score"],
                "topic": item["topic"],
            }
            for item in created
        ],
        "summary": summary,
        "formula": SCORE_FORMULA,
    }


async def _load_unscored_signals(
    session: AsyncSession,
    requested_id: UUID | None,
    recent_hours: int,
) -> list[TrendSignal]:
    scored = select(Opportunity.trend_signal_id)
    stmt = select(TrendSignal).where(TrendSignal.id.notin_(scored))
    if requested_id is not None:
        stmt = stmt.where(TrendSignal.id == requested_id)
    else:
        cutoff = datetime.now(UTC) - timedelta(hours=recent_hours)
        stmt = stmt.where(TrendSignal.observed_at >= cutoff)
    stmt = stmt.order_by(TrendSignal.observed_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _active_niches(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(StrategyAllocation.niche).where(StrategyAllocation.active.is_(True))
    )
    return [row for row in result.scalars().all() if row]


async def _promote_top_opportunities(
    session: AsyncSession,
    created: list[dict[str, Any]],
    *,
    settings_top: int,
    simulation: bool,
    workflow_id: UUID | None,
) -> list[UUID]:
    if not created:
        return []
    settings = get_settings()
    already = await produced_today(session)
    remaining = max(0, settings.max_content_per_day - already)
    limit = min(max(settings_top, 0), remaining)
    if limit <= 0:
        logger.info("content_cap_reached", produced_today=already)
        return []
    ranked = sorted(created, key=lambda item: item["score"], reverse=True)
    existing_topics = {
        topic.lower()
        for topic in (await session.execute(select(ContentItem.topic))).scalars().all()
        if topic
    }
    existing_opps = set(
        (await session.execute(select(ContentItem.opportunity_id))).scalars().all()
    )
    created_ids: list[UUID] = []
    for row in ranked:
        if len(created_ids) >= limit:
            break
        opportunity_id = row["opportunity_id"]
        topic = row["topic"]
        if opportunity_id in existing_opps:
            continue
        if topic.lower() in existing_topics:
            continue
        item = ContentItem(
            topic=topic[:300],
            niche=(row["niche"][:80] if row["niche"] else None),
            status=ContentStatus.SCORED.value,
            opportunity_id=opportunity_id,
            workflow_id=workflow_id or uuid4(),
            simulation=bool(row["simulation"] or simulation),
        )
        session.add(item)
        await session.flush()
        existing_topics.add(topic.lower())
        existing_opps.add(opportunity_id)
        created_ids.append(item.id)
        row["content_id"] = item.id
        logger.info(
            "content_scored",
            content_id=str(item.id),
            opportunity_id=str(opportunity_id),
            topic=topic,
        )
    return created_ids


async def _optional_llm_note(
    session: AsyncSession, topic: str, features: dict[str, Any]
) -> str:
    settings = get_settings()
    if settings.llm_provider == "dev":
        return ""
    try:
        await assert_budget(session, kind="ai")
    except BudgetExceeded:
        return ""
    try:
        text = await get_llm().generate_text(
            (
                f"One short sentence on why '{topic}' is a content opportunity. "
                "Do not invent facts or change scores. "
                f"Measured features: velocity={features.get('velocity')}, "
                f"recency={features.get('recency')}, "
                f"engagement={features.get('engagement')}, "
                f"novelty={features.get('novelty')}."
            ),
            system="Explain ranked media opportunities in one factual sentence.",
        )
    except Exception:
        logger.info("optional_llm_explanation_skipped")
        return ""
    cleaned = " ".join((text or "").split())[:280]
    return cleaned


def _as_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None
