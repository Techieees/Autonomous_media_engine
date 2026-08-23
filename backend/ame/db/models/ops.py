import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from ame.db.types import GUID, JSONType
from sqlalchemy.orm import Mapped, mapped_column

from ame.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_connections"

    platform: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="not_configured")
    account_label: Mapped[str | None] = mapped_column(String(160))
    scopes: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)


class HumanAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "human_actions"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open")
    platform: Mapped[str | None] = mapped_column(String(40))
    blocking: Mapped[bool] = mapped_column(default=False)
    classification: Mapped[str] = mapped_column(String(40), default="genuinely_human_required")
    checkpoint_kind: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class BrandConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brand_configs"

    version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    handles: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    short_description: Mapped[str] = mapped_column(Text, default="")
    tone: Mapped[str] = mapped_column(String(120), default="precise")
    visual_identity: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    content_pillars: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    audience: Mapped[str] = mapped_column(Text, default="")
    voice_personality: Mapped[str] = mapped_column(Text, default="")
    title_conventions: Mapped[str] = mapped_column(Text, default="")
    caption_conventions: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)


class AccountBootstrap(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "account_bootstraps"
    __table_args__ = (UniqueConstraint("platform", name="uq_account_bootstrap_platform"),)

    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="planning")
    brand_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    selected_handle: Mapped[str | None] = mapped_column(String(80))
    handle_candidates: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    checkpoint_kind: Mapped[str | None] = mapped_column(String(80))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    simulation: Mapped[bool] = mapped_column(default=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class DailyPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "daily_plans"
    __table_args__ = (UniqueConstraint("local_date", "timezone", name="uq_daily_plan_date_tz"),)

    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class ExecutiveReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "executive_reports"
    __table_args__ = (UniqueConstraint("local_date", "timezone", name="uq_executive_report_date_tz"),)

    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    headline: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False)


class OwnerNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "owner_notifications"

    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    related_entity_type: Mapped[str | None] = mapped_column(String(80))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
