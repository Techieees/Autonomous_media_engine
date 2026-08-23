"""Structured agent-to-agent messages persisted on the blackboard."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import AgentMessageType, AgentName
from ame.db.models import AgentMessage


async def post_message(
    session: AsyncSession,
    *,
    sender: AgentName | str,
    recipient: AgentName | str,
    message_type: AgentMessageType | str,
    payload: dict[str, Any] | None = None,
    task: str | None = None,
    related_entity_type: str | None = None,
    related_entity_id: UUID | None = None,
    content_id: UUID | None = None,
    run_id: UUID | None = None,
    task_id: UUID | None = None,
    confidence: float | None = None,
) -> AgentMessage:
    kind = message_type.value if isinstance(message_type, AgentMessageType) else str(message_type)
    sender_name = sender.value if isinstance(sender, AgentName) else str(sender)
    recipient_name = recipient.value if isinstance(recipient, AgentName) else str(recipient)
    body = {
        "message_type": kind,
        "task": task,
        "related_entity_type": related_entity_type,
        "related_entity_id": str(related_entity_id) if related_entity_id else None,
        "payload": payload or {},
        "confidence": confidence,
    }
    row = AgentMessage(
        from_agent=sender_name,
        to_agent=recipient_name,
        kind=kind,
        body=body,
        content_id=content_id,
        run_id=run_id,
        task_id=task_id,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        confidence=confidence,
    )
    session.add(row)
    return row
