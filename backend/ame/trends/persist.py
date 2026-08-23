from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ame.db.dialect import upsert_insert

from ame.contracts.schemas import TrendSignalIn
from ame.db.models import SystemEvent, TrendSignal


@dataclass(slots=True)
class CollectedSignal:
    signal: TrendSignalIn
    simulation: bool


def dedupe_signals(items: list[CollectedSignal]) -> list[CollectedSignal]:
    best: dict[tuple[str, str], CollectedSignal] = {}
    for item in items:
        key = (item.signal.source, item.signal.external_id)
        previous = best.get(key)
        if previous is None or item.signal.trend_score >= previous.signal.trend_score:
            best[key] = item
    return list(best.values())


async def upsert_trend_signals(
    session: AsyncSession,
    items: list[CollectedSignal],
    *,
    now: datetime,
    correlation_id: str | None,
    workflow_id: UUID | None,
) -> list[UUID]:
    if not items:
        return []
    payloads = [_values(item.signal, item.simulation, now) for item in items]
    intended_ids = {payload["id"] for payload in payloads}
    stmt = upsert_insert(TrendSignal, session).values(payloads)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_={
            TrendSignal.topic: excluded.topic,
            TrendSignal.title: excluded.title,
            TrendSignal.url: excluded.url,
            TrendSignal.published_at: excluded.published_at,
            TrendSignal.observed_at: excluded.observed_at,
            TrendSignal.views: excluded.views,
            TrendSignal.likes: excluded.likes,
            TrendSignal.comments: excluded.comments,
            TrendSignal.velocity: excluded.velocity,
            TrendSignal.engagement_rate: excluded.engagement_rate,
            TrendSignal.age_hours: excluded.age_hours,
            TrendSignal.cross_platform_count: excluded.cross_platform_count,
            TrendSignal.source_authority: excluded.source_authority,
            TrendSignal.risk_score: excluded.risk_score,
            TrendSignal.trend_score: excluded.trend_score,
            TrendSignal.metadata_json: excluded.metadata,
            TrendSignal.simulation: excluded.simulation,
            TrendSignal.updated_at: now,
        },
    ).returning(TrendSignal)
    result = await session.execute(stmt)
    created_ids: list[UUID] = []
    for row in result.scalars():
        if row.id not in intended_ids:
            continue
        created_ids.append(row.id)
        session.add(
            SystemEvent(
                name="trend.discovered",
                payload={
                    "trend_signal_id": str(row.id),
                    "source": row.source,
                    "external_id": row.external_id,
                    "topic": row.topic,
                    "title": row.title,
                    "trend_score": row.trend_score,
                    "simulation": row.simulation,
                },
                correlation_id=correlation_id,
                workflow_id=workflow_id,
                simulation=bool(row.simulation),
            )
        )
    await session.flush()
    return created_ids


def _values(signal: TrendSignalIn, simulation: bool, now: datetime) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "source": signal.source[:80],
        "external_id": signal.external_id[:200],
        "topic": signal.topic[:300],
        "title": signal.title[:500],
        "url": signal.url,
        "published_at": signal.published_at,
        "observed_at": now,
        "views": signal.views,
        "likes": signal.likes,
        "comments": signal.comments,
        "velocity": signal.velocity,
        "engagement_rate": signal.engagement_rate,
        "age_hours": signal.age_hours,
        "cross_platform_count": signal.cross_platform_count,
        "source_authority": signal.source_authority,
        "risk_score": signal.risk_score,
        "trend_score": signal.trend_score,
        "metadata_json": dict(signal.metadata),
        "simulation": simulation,
    }
