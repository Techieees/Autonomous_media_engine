import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from ame.db.types import GUID, JSONType, UTCDateTime
from sqlalchemy.orm import Mapped, mapped_column

from ame.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PublishingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publishing_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_publishing_jobs_idempotency"),
        Index("ix_publishing_jobs_status", "status"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    simulation: Mapped[bool] = mapped_column(default=True)


class Publication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("content_id", "platform", name="uq_publication_content_platform"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(300))
    privacy_status: Mapped[str | None] = mapped_column(String(40))
    simulation: Mapped[bool] = mapped_column(default=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class PublishingCalendarSlot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publishing_calendar_slots"
    __table_args__ = (
        UniqueConstraint("content_id", "platform", name="uq_calendar_content_platform"),
        Index("ix_calendar_status_planned", "status", "planned_at"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    planned_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text)
    experiment: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
