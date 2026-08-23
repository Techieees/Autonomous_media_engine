from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.db.models import ContentItem, Publication
from ame.learning.features import LearningRecord, build_record


async def load_history(session: AsyncSession) -> list[LearningRecord]:
    result = await session.execute(select(Publication))
    records: list[LearningRecord] = []
    for publication in result.scalars():
        content = await session.get(ContentItem, publication.content_id)
        if content is None:
            continue
        record = await build_record(session, content, publication)
        targets = record.targets
        if not any(
            value is not None
            for value in (
                targets.views_1h,
                targets.views_6h,
                targets.views_24h,
                targets.views_72h,
                targets.views_7d,
            )
        ):
            continue
        records.append(record)
    return records
