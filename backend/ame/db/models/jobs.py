import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint
from ame.db.types import GUID, JSONType
from sqlalchemy.orm import Mapped, mapped_column

from ame.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_name_status", "name", "status"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_by: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(80))
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    dead_letter: Mapped[bool] = mapped_column(default=False)


class SystemEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "system_events"
    __table_args__ = (Index("ix_system_events_name_created", "name", "created_at"),)

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(80))
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    simulation: Mapped[bool] = mapped_column(default=False)


class CostEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cost_events"
    __table_args__ = (Index("ix_cost_events_created", "created_at"),)

    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    job: Mapped[str | None] = mapped_column(String(80))
    agent: Mapped[str | None] = mapped_column(String(80))
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    kind: Mapped[str] = mapped_column(String(32), default="ai")
