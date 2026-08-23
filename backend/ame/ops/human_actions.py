"""Human actions are last-resort owner checkpoints, never technical tickets."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import HumanActionClass, HumanActionStatus
from ame.db.models import HumanAction, SystemEvent

GENUINE_CATEGORIES = frozenset(
    {
        "oauth",
        "oauth_consent",
        "captcha",
        "mfa",
        "verification",
        "legal",
        "kyc",
        "tax",
        "payout",
        "app_review",
        "platform_review",
        "account_ownership",
        "checkpoint",
    }
)

TECHNICAL_HINTS = (
    "timeout",
    "render failed",
    "ffmpeg",
    "retry",
    "rate limit",
    "5xx",
    "connection reset",
    "temporary",
    "stuck job",
    "lease",
    "corrupt",
)


def classify_action(*, category: str, title: str, instructions: str) -> HumanActionClass:
    blob = f"{category} {title} {instructions}".lower()
    if category in GENUINE_CATEGORIES:
        return HumanActionClass.GENUINELY_HUMAN_REQUIRED
    if any(hint in blob for hint in TECHNICAL_HINTS):
        return HumanActionClass.TECHNICAL_FAILURE
    if category in {"qa", "ops", "retry", "technical"}:
        return HumanActionClass.TECHNICAL_FAILURE
    if category in {"bootstrap", "account", "monetization"} and "oauth" not in blob:
        return HumanActionClass.AUTOMATABLE
    return HumanActionClass.GENUINELY_HUMAN_REQUIRED


async def open_human_action(
    session: AsyncSession,
    *,
    title: str,
    instructions: str,
    category: str,
    platform: str | None = None,
    blocking: bool = False,
    checkpoint_kind: str | None = None,
    details: dict[str, Any] | None = None,
    classification: HumanActionClass | None = None,
) -> HumanAction | None:
    chosen = classification or classify_action(
        category=category, title=title, instructions=instructions
    )
    if chosen != HumanActionClass.GENUINELY_HUMAN_REQUIRED:
        session.add(
            SystemEvent(
                name="human_action.suppressed",
                payload={
                    "title": title,
                    "category": category,
                    "classification": chosen.value,
                    "platform": platform,
                },
            )
        )
        return None
    existing = await session.execute(
        select(HumanAction).where(
            HumanAction.title == title[:200],
            HumanAction.platform == platform,
            HumanAction.status == HumanActionStatus.OPEN.value,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row
    row = HumanAction(
        title=title[:200],
        instructions=instructions,
        category=category,
        status=HumanActionStatus.OPEN.value,
        platform=platform,
        blocking=blocking,
        classification=chosen.value,
        checkpoint_kind=checkpoint_kind,
        details=details or {},
    )
    session.add(row)
    session.add(
        SystemEvent(
            name="human_action.required",
            payload={
                "title": title,
                "category": category,
                "platform": platform,
                "checkpoint_kind": checkpoint_kind,
            },
        )
    )
    await session.flush()
    return row


async def cancel_automatable_open_actions(session: AsyncSession) -> int:
    rows = (
        await session.execute(
            select(HumanAction).where(HumanAction.status == HumanActionStatus.OPEN.value)
        )
    ).scalars().all()
    cancelled = 0
    for row in rows:
        classification = classify_action(
            category=row.category, title=row.title, instructions=row.instructions
        )
        stored = getattr(row, "classification", None)
        if stored == HumanActionClass.GENUINELY_HUMAN_REQUIRED.value:
            continue
        if classification != HumanActionClass.GENUINELY_HUMAN_REQUIRED:
            row.status = HumanActionStatus.CANCELLED.value
            row.classification = classification.value
            cancelled += 1
    if cancelled:
        await session.flush()
    return cancelled


def is_owner_visible(action: HumanAction) -> bool:
    if action.status != HumanActionStatus.OPEN.value:
        return False
    if action.category == "oauth_state":
        return False
    classification = getattr(action, "classification", None) or classify_action(
        category=action.category, title=action.title, instructions=action.instructions
    )
    if isinstance(classification, HumanActionClass):
        return classification == HumanActionClass.GENUINELY_HUMAN_REQUIRED
    return classification == HumanActionClass.GENUINELY_HUMAN_REQUIRED.value
