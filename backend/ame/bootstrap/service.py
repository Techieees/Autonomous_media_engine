from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.bootstrap.instructions import (
    OWNER_ACTION_CATEGORIES,
    SPECS_BY_TITLE,
    generate_first_run_brief,
)
from ame.bootstrap.status import (
    PRODUCTION_PLATFORMS,
    ConnectionStatus,
    resolve_connection_status,
    sync_connection_states,
)
from ame.contracts.enums import ConnectionState, HumanActionStatus
from ame.db.models import HumanAction, PlatformConnection


class HumanChecklistItem(BaseModel):
    id: UUID
    key: str
    title: str
    instructions: str
    category: str
    status: str
    platform: str | None
    blocking: bool


class BootstrapSnapshot(BaseModel):
    message: str
    production_ready: bool
    connections: list[ConnectionStatus]
    checklist: list[HumanChecklistItem] = Field(default_factory=list)
    first_run: bool = True
    activation: dict = Field(default_factory=dict)


def _spec_key(action: HumanAction) -> str:
    spec = SPECS_BY_TITLE.get(action.title)
    if spec is not None:
        return spec.key
    return f"{action.category}.{action.platform or 'owner'}"


def _as_item(action: HumanAction) -> HumanChecklistItem:
    return HumanChecklistItem(
        id=action.id,
        key=_spec_key(action),
        title=action.title,
        instructions=action.instructions,
        category=action.category,
        status=action.status,
        platform=action.platform,
        blocking=action.blocking,
    )


async def _seed_platforms(session: AsyncSession) -> list[PlatformConnection]:
    result = await session.execute(
        select(PlatformConnection).where(
            PlatformConnection.platform.in_([item.value for item in PRODUCTION_PLATFORMS])
        )
    )
    existing = {row.platform: row for row in result.scalars()}
    created: list[PlatformConnection] = []
    for platform in PRODUCTION_PLATFORMS:
        connection = existing.get(platform.value)
        if connection is not None:
            continue
        resolved = resolve_connection_status(None, platform=platform.value)
        connection = PlatformConnection(
            platform=platform.value,
            state=resolved.state.value,
            scopes=[],
            metadata_json={"seeded": True, "source": "bootstrap"},
        )
        session.add(connection)
        created.append(connection)
        existing[platform.value] = connection
    if created:
        await session.flush()
    return list(existing.values())


async def seed_bootstrap(session: AsyncSession) -> list[HumanChecklistItem]:
    from ame.bootstrap.orchestrator import advance_all
    from ame.ops.human_actions import cancel_automatable_open_actions, is_owner_visible

    await _seed_platforms(session)
    await cancel_automatable_open_actions(session)
    await advance_all(session)
    await sync_connection_states(session)
    result = await session.execute(
        select(HumanAction).where(HumanAction.status == HumanActionStatus.OPEN.value)
    )
    return [_as_item(action) for action in result.scalars() if is_owner_visible(action)]


async def list_human_checklist(session: AsyncSession) -> list[HumanChecklistItem]:
    result = await session.execute(
        select(HumanAction)
        .where(HumanAction.category.in_(OWNER_ACTION_CATEGORIES))
        .order_by(HumanAction.created_at.asc())
    )
    return [_as_item(action) for action in result.scalars()]


async def list_open_human_actions(session: AsyncSession) -> list[HumanChecklistItem]:
    items = await list_human_checklist(session)
    return [item for item in items if item.status == HumanActionStatus.OPEN.value]


def _snapshot_message(connections: list[ConnectionStatus]) -> tuple[str, bool, bool]:
    ready = [item for item in connections if item.state == ConnectionState.READY]
    connected = [
        item
        for item in connections
        if item.state
        in {
            ConnectionState.READY,
            ConnectionState.CONNECTED,
            ConnectionState.NEEDS_PLATFORM_REVIEW,
            ConnectionState.REQUIRES_HUMAN_ACTION,
        }
        and item.token_present
    ]
    if not connected:
        return "No production social accounts connected.", False, True
    if len(ready) == len(connections):
        return "Production platforms connected.", True, False
    return "Partial production connections. Dry-run remains available.", False, False


async def get_bootstrap_snapshot(session: AsyncSession) -> BootstrapSnapshot:
    from ame.bootstrap.orchestrator import bootstrap_snapshot

    checklist = await seed_bootstrap(session)
    connections = await sync_connection_states(session)
    activation = await bootstrap_snapshot(session)
    message, production_ready, first_run = _snapshot_message(connections)
    if not any(item.state.value == "ready" for item in connections):
        first_run = True
        blocked = [
            f"{name}: {payload.get('blocked_reason') or payload.get('state')}"
            for name, payload in (activation.get("platforms") or {}).items()
        ]
        message = "Account activation in progress. " + "; ".join(blocked[:3])
    return BootstrapSnapshot(
        message=message,
        production_ready=production_ready,
        connections=connections,
        checklist=checklist,
        first_run=first_run,
        activation=activation,
    )


def owner_instructions_brief() -> str:
    return generate_first_run_brief()
