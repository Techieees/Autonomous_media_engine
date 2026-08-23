"""In-app owner notifications. No external provider required."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import NotificationKind
from ame.db.models import OwnerNotification, SystemEvent

OWNER_KINDS = frozenset(item.value for item in NotificationKind)


async def notify(
    session: AsyncSession,
    kind: NotificationKind | str,
    title: str,
    body: str,
    *,
    payload: dict[str, Any] | None = None,
    related_entity_type: str | None = None,
    related_entity_id: UUID | None = None,
) -> OwnerNotification:
    kind_value = kind.value if isinstance(kind, NotificationKind) else str(kind)
    if kind_value not in OWNER_KINDS:
        raise ValueError(f"unsupported notification kind: {kind_value}")
    existing = await session.execute(
        select(OwnerNotification).where(
            OwnerNotification.kind == kind_value,
            OwnerNotification.title == title[:200],
            OwnerNotification.read.is_(False),
        )
    )
    row = existing.scalars().first()
    if row is not None:
        return row
    row = OwnerNotification(
        kind=kind_value,
        title=title[:200],
        body=body,
        payload=payload or {},
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    session.add(row)
    session.add(
        SystemEvent(
            name="notification.created",
            payload={"kind": kind_value, "title": title},
        )
    )
    await session.flush()
    return row


async def list_notifications(
    session: AsyncSession, *, unread_only: bool = False, limit: int = 50
) -> list[OwnerNotification]:
    stmt = select(OwnerNotification).order_by(OwnerNotification.created_at.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(OwnerNotification.read.is_(False))
    return list((await session.execute(stmt)).scalars().all())


def serialize_notification(row: OwnerNotification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "payload": row.payload or {},
        "read": row.read,
        "related_entity_type": row.related_entity_type,
        "related_entity_id": str(row.related_entity_id) if row.related_entity_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
