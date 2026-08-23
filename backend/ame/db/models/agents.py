import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, String, Text
from ame.db.types import GUID, JSONType
from sqlalchemy.orm import Mapped, mapped_column

from ame.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_agent_tasks_agent_status", "agent", "status"),)

    agent: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    correlation_id: Mapped[str | None] = mapped_column(String(80))


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"

    agent: Mapped[str] = mapped_column(String(80), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None]
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    correlation_id: Mapped[str | None] = mapped_column(String(80))


class AgentMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_messages"

    from_agent: Mapped[str] = mapped_column(String(80), nullable=False)
    to_agent: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    run_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    related_entity_type: Mapped[str | None] = mapped_column(String(80))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    confidence: Mapped[float | None] = mapped_column(Float)


class AgentDecisionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_decisions"
    __table_args__ = (Index("ix_agent_decisions_agent_created", "agent", "created_at"),)

    agent: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    expected_effect: Mapped[str | None] = mapped_column(Text)
    related_entity_type: Mapped[str | None] = mapped_column(String(80))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    run_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    content_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
