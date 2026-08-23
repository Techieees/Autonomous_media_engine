from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.ids import as_uuid
from ame.analytics.snapshot import load_publication
from ame.contracts.enums import ContentStatus
from ame.db.models import AgentMessage, ContentItem, Job, LearningRecommendation
from ame.learning.features import LearningRecord, build_record
from ame.learning.history import load_history
from ame.learning.recommend import persist_recommendation
from ame.observability import get_logger

logger = get_logger("ame.learning.update")

_COMPLETE_FROM = {
    ContentStatus.PUBLISHED.value,
    ContentStatus.PUBLISHING.value,
    ContentStatus.MEASURING.value,
    ContentStatus.LEARNING_COMPLETE.value,
}


@dataclass
class LearningOutcome:
    record: LearningRecord
    recommendation: LearningRecommendation
    history_n: int


async def run_learning_update(session: AsyncSession, job: Job) -> LearningOutcome:
    payload = dict(job.payload or {})
    publication = await load_publication(session, payload, job)
    content = await session.get(ContentItem, publication.content_id)
    if content is None:
        raise RuntimeError(f"content_not_found:{publication.content_id}")
    record = await build_record(session, content, publication)
    history = await load_history(session)
    recommendation = await persist_recommendation(
        session,
        record=record,
        history=history,
        rng=random.Random(str(publication.id)),
    )
    if content.status in _COMPLETE_FROM:
        content.status = ContentStatus.LEARNING_COMPLETE.value
    session.add(
        AgentMessage(
            id=uuid4(),
            from_agent="learning",
            to_agent="director",
            kind="recommendation",
            body={
                "recommendation_id": str(recommendation.id),
                "recommendation": recommendation.recommendation,
                "method": recommendation.method,
                "confidence": recommendation.confidence,
                "simulation": record.simulation,
                "content_id": str(content.id),
            },
            content_id=content.id,
        )
    )
    await session.flush()
    logger.info(
        "learning_update",
        content_id=str(content.id),
        publication_id=str(publication.id),
        method=recommendation.method,
        history_n=len(history),
        simulation=record.simulation,
    )
    return LearningOutcome(
        record=record, recommendation=recommendation, history_n=len(history)
    )


def outcome_payload(outcome: LearningOutcome) -> dict[str, Any]:
    return {
        "recommendation_id": str(outcome.recommendation.id),
        "recommendation": outcome.recommendation.recommendation,
        "method": outcome.recommendation.method,
        "confidence": outcome.recommendation.confidence,
        "features": outcome.record.features.model_dump(mode="json"),
        "targets": outcome.record.targets.model_dump(mode="json"),
        "simulation": outcome.record.simulation,
        "history_n": outcome.history_n,
        "llm_learning": False,
        "note": "Deterministic ranking over persisted outcomes. Not LLM learning.",
    }
