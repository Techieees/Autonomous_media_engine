import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from ame.db.types import GUID, JSONType
from sqlalchemy.orm import Mapped, mapped_column

from ame.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MetricSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "publication_id", "checkpoint", name="uq_metric_publication_checkpoint"
        ),
        Index("ix_metric_snapshots_content", "content_id"),
    )

    publication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("publications.id"), nullable=False)
    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    checkpoint: Mapped[str] = mapped_column(String(8), nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    watch_time_seconds: Mapped[float] = mapped_column(Float, default=0)
    completion_rate: Mapped[float | None] = mapped_column(Float)
    followers_gained: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    simulation: Mapped[bool] = mapped_column(default=False)


class RevenueEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "revenue_events"

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(40))
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    period: Mapped[str | None] = mapped_column(String(32))
    simulation: Mapped[bool] = mapped_column(default=False)


class Experiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "experiments"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    locked: Mapped[bool] = mapped_column(default=False)


class ExperimentAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "experiment_assignments"
    __table_args__ = (UniqueConstraint("content_id", name="uq_experiment_content"),)

    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    variant: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)


class StrategyAllocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_allocations"

    niche: Mapped[str] = mapped_column(String(80), nullable=False)
    allocation: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(default=True)
    decided_by: Mapped[str] = mapped_column(String(80), default="director")


class LearningRecommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "learning_recommendations"

    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    method: Mapped[str] = mapped_column(String(40), default="ucb")
    confidence: Mapped[float] = mapped_column(Float, default=0.4)
    consumed: Mapped[bool] = mapped_column(default=False)
