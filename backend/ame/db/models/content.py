import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from ame.db.types import GUID, JSONType
from sqlalchemy.orm import Mapped, mapped_column

from ame.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TrendSignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trend_signals"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_trend_source_external"),
        Index("ix_trend_signals_score", "trend_score"),
    )

    source: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    velocity: Mapped[float] = mapped_column(Float, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0)
    age_hours: Mapped[float] = mapped_column(Float, default=0)
    cross_platform_count: Mapped[int] = mapped_column(Integer, default=1)
    source_authority: Mapped[float] = mapped_column(Float, default=0.5)
    risk_score: Mapped[float] = mapped_column(Float, default=0.1)
    trend_score: Mapped[float] = mapped_column(Float, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)
    simulation: Mapped[bool] = mapped_column(default=False)


class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"

    trend_signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trend_signals.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="scored")
    approved: Mapped[bool] = mapped_column(default=False)
    simulation: Mapped[bool] = mapped_column(default=False)


class ContentItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (Index("ix_content_items_status", "status"),)

    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    niche: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="discovered")
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id"))
    selected_script_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    workflow_id: Mapped[uuid.UUID] = mapped_column(GUID(), default=uuid.uuid4)
    simulation: Mapped[bool] = mapped_column(default=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(GUID())


class ResearchPack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_packs"

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    claims: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    source_urls: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    uncertain_claims: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    unsuitable_claims: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    simulation: Mapped[bool] = mapped_column(default=False)


class Script(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scripts"

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    candidate_label: Mapped[str] = mapped_column(String(8), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reveal: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_duration: Mapped[int] = mapped_column(Integer, default=35)
    on_screen_text: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    scene_plan: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    voice_style: Mapped[str] = mapped_column(String(80), default="clear_authoritative")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    sources_used: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    claims: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    selected: Mapped[bool] = mapped_column(default=False)
    critique: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    normalized_hash: Mapped[str | None] = mapped_column(String(64))


class ProductionManifestRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "production_manifests"

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    template_id: Mapped[str] = mapped_column(String(80), default="vertical_clean_v1")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_assets"

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    sha256: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(80), default="generated")
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(120))
    usage_type: Mapped[str] = mapped_column(String(40), default="original")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)


class QAResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "qa_results"

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    reasons: Mapped[list[Any]] = mapped_column(JSONType, default=list)


class OriginalityFingerprint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "originality_fingerprints"
    __table_args__ = (Index("ix_originality_hash", "script_hash"),)

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    script_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hook_hash: Mapped[str | None] = mapped_column(String(64))
    title_normalized: Mapped[str | None] = mapped_column(String(400))
    embedding: Mapped[list[Any] | None] = mapped_column(JSONType)
    asset_manifest_hash: Mapped[str | None] = mapped_column(String(64))
