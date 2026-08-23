from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import RevenueKind
from ame.db.models import (
    ContentItem,
    MetricSnapshot,
    ProductionManifestRecord,
    Publication,
    RevenueEvent,
    Script,
)


class LearningFeatures(BaseModel):
    topic: str
    niche: str
    hook_type: str
    duration_bucket: str
    voice: str
    visual_template: str
    publication_hour: int | None = None
    platform: str
    day_of_week: int | None = None
    content_age_hours: float | None = None


class LearningTargets(BaseModel):
    views_1h: int | None = None
    views_6h: int | None = None
    views_24h: int | None = None
    views_72h: int | None = None
    views_7d: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    completion: float | None = None
    watch_time: float | None = None
    followers: int | None = None
    revenue: float | None = None


class LearningRecord(BaseModel):
    content_id: UUID
    publication_id: UUID
    simulation: bool
    features: LearningFeatures
    targets: LearningTargets
    extra: dict[str, Any] = Field(default_factory=dict)


_QUESTION = re.compile(
    r"^(why|what|how|when|where|who|did|do|does|is|are|can|should)\b", re.I
)


def classify_hook(text: str) -> str:
    hook = (text or "").strip()
    lowered = hook.lower()
    if hook.endswith("?") or _QUESTION.match(lowered):
        return "question"
    if re.match(r"^\d+\s", lowered) or " reasons" in lowered or " ways" in lowered:
        return "list"
    if any(token in lowered for token in (" vs ", "versus", "but not", "instead of")):
        return "contrast"
    if any(token in lowered for token in ("percent", "%", "study", "data", "million")):
        return "stat"
    if any(token in lowered for token in ("story", "once ", "i ", "we ")):
        return "story"
    if any(token in lowered for token in ("stop", "don't", "never", "wait")):
        return "challenge"
    return "direct"


def duration_bucket(seconds: int | None) -> str:
    value = int(seconds or 0)
    if value < 20:
        return "under_20s"
    if value < 35:
        return "20_35s"
    if value < 50:
        return "35_50s"
    if value <= 60:
        return "50_60s"
    return "over_60s"


async def _selected_script(session: AsyncSession, content: ContentItem) -> Script | None:
    if content.selected_script_id:
        script = await session.get(Script, content.selected_script_id)
        if script is not None:
            return script
    result = await session.execute(
        select(Script)
        .where(Script.content_id == content.id, Script.selected.is_(True))
        .order_by(Script.created_at.desc())
    )
    return result.scalars().first()


async def _manifest(
    session: AsyncSession, content_id: UUID
) -> ProductionManifestRecord | None:
    result = await session.execute(
        select(ProductionManifestRecord)
        .where(ProductionManifestRecord.content_id == content_id)
        .order_by(ProductionManifestRecord.created_at.desc())
    )
    return result.scalars().first()


def _hour_dow(moment: datetime | None) -> tuple[int | None, int | None]:
    if moment is None:
        return None, None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.hour, moment.weekday()


async def extract_features(
    session: AsyncSession,
    content: ContentItem,
    publication: Publication,
    *,
    now: datetime | None = None,
) -> LearningFeatures:
    script = await _selected_script(session, content)
    manifest = await _manifest(session, content.id)
    hour, dow = _hour_dow(publication.created_at)
    age = None
    if publication.created_at is not None:
        created = publication.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = max(0.0, ((now or datetime.now(UTC)) - created).total_seconds() / 3600.0)
    spec = (manifest.spec if manifest else {}) or {}
    template = (
        (manifest.template_id if manifest else None)
        or spec.get("template_id")
        or "vertical_clean_v1"
    )
    return LearningFeatures(
        topic=content.topic,
        niche=content.niche or "unspecified",
        hook_type=classify_hook(script.hook if script else ""),
        duration_bucket=duration_bucket(script.estimated_duration if script else None),
        voice=(script.voice_style if script else None) or "clear_authoritative",
        visual_template=str(template),
        publication_hour=hour,
        platform=publication.platform,
        day_of_week=dow,
        content_age_hours=round(age, 3) if age is not None else None,
    )


def _views(snapshot: MetricSnapshot | None) -> int | None:
    return None if snapshot is None else snapshot.views


async def _actual_revenue(session: AsyncSession, content_id: UUID) -> float | None:
    value = await session.scalar(
        select(func.coalesce(func.sum(RevenueEvent.amount), 0)).where(
            RevenueEvent.content_id == content_id,
            RevenueEvent.kind == RevenueKind.ACTUAL.value,
            RevenueEvent.simulation.is_(False),
        )
    )
    amount = float(value or 0)
    return amount if amount > 0 else None


async def extract_targets(
    session: AsyncSession, publication: Publication
) -> LearningTargets:
    result = await session.execute(
        select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
    )
    by_checkpoint = {row.checkpoint: row for row in result.scalars()}
    latest = max(by_checkpoint.values(), key=lambda item: item.created_at) if by_checkpoint else None
    revenue = await _actual_revenue(session, publication.content_id)
    return LearningTargets(
        views_1h=_views(by_checkpoint.get("1h")),
        views_6h=_views(by_checkpoint.get("6h")),
        views_24h=_views(by_checkpoint.get("24h")),
        views_72h=_views(by_checkpoint.get("72h")),
        views_7d=_views(by_checkpoint.get("7d")),
        likes=latest.likes if latest else None,
        comments=latest.comments if latest else None,
        shares=latest.shares if latest else None,
        completion=latest.completion_rate if latest else None,
        watch_time=latest.watch_time_seconds if latest else None,
        followers=latest.followers_gained if latest else None,
        revenue=revenue,
    )


async def build_record(
    session: AsyncSession, content: ContentItem, publication: Publication
) -> LearningRecord:
    features = await extract_features(session, content, publication)
    targets = await extract_targets(session, publication)
    return LearningRecord(
        content_id=content.id,
        publication_id=publication.id,
        simulation=bool(publication.simulation or content.simulation),
        features=features,
        targets=targets,
    )
