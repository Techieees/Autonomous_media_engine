"""Advance each platform through every permitted machine step.

Stops only at an exact platform-enforced checkpoint. Never asks the owner
which brand, handle, niche, or whether to continue. Never fabricates
ACCOUNT_CREATED or READY from a dashboard click.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.messaging import post_message
from ame.bootstrap.boundary import read_boundary, write_boundary
from ame.bootstrap.handoff import launch_official_page
from ame.bootstrap.identity import (
    choose_brand,
    first_human_checkpoint,
    handle_candidates,
    next_available_handle,
    official_flows,
    official_oauth_start_path,
)
from ame.bootstrap.mailbox import mailbox_provider, surface_messages
from ame.bootstrap.packages import build_activation_package
from ame.bootstrap.status import PRODUCTION_PLATFORMS, credentials_configured
from ame.bootstrap.verify import verify_account, verify_developer_app, verify_oauth
from ame.config import get_settings
from ame.contracts.enums import (
    AccountBootstrapState,
    AgentMessageType,
    AgentName,
    ConnectionState,
    HumanActionClass,
    NotificationKind,
    Platform,
)
from ame.db.models import (
    AccountBootstrap,
    BrandConfig,
    ContentItem,
    HumanAction,
    Job,
    PlatformConnection,
    SystemEvent,
)
from ame.ops.calendar import schedule_publication
from ame.ops.human_actions import open_human_action
from ame.ops.notifications import notify

HUMAN_STOPS = {
    AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED,
    AccountBootstrapState.OAUTH_REQUIRED,
    AccountBootstrapState.PLATFORM_REVIEW_REQUIRED,
    AccountBootstrapState.DEVELOPER_APP_REQUIRED,
    AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION,
}

CHECKPOINT_KIND = {
    AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED: "verification",
    AccountBootstrapState.OAUTH_REQUIRED: "oauth_consent",
    AccountBootstrapState.PLATFORM_REVIEW_REQUIRED: "app_review",
    AccountBootstrapState.DEVELOPER_APP_REQUIRED: "app_review",
}

CHECKPOINT_COPY = {
    "channel_creation": "{platform} requires account/channel confirmation. The official page is already open.",
    "email_verification": "{platform} requires email verification. The official page is already open.",
    "phone_verification": "{platform} requires phone verification. The official page is already open.",
    "captcha": "{platform} requires a CAPTCHA. Complete only that checkpoint.",
    "legal_consent": "{platform} requires Terms acceptance. The official page is already open.",
    "oauth_consent": "{platform} OAuth consent is required. Approve the official consent screen.",
    "app_review": "{platform} developer portal requires the remaining interactive step.",
    "verification": "{platform} verification required. Complete the checkpoint currently shown.",
}


async def handle_bootstrap_tick(session: AsyncSession, job: Job) -> None:
    await advance_all(session)
    _ = job


async def advance_all(session: AsyncSession) -> list[AccountBootstrap]:
    brand = await _ensure_brand(session)
    rows: list[AccountBootstrap] = []
    for platform in PRODUCTION_PLATFORMS:
        rows.append(await advance_platform(session, platform.value, brand))
    await session.flush()
    return rows


async def advance_platform(
    session: AsyncSession, platform: str, brand: BrandConfig | None = None
) -> AccountBootstrap:
    brand = brand or await _ensure_brand(session)
    row = await _load_or_create(session, platform, brand)
    if row.state in {
        AccountBootstrapState.READY.value,
        AccountBootstrapState.RESTRICTED.value,
    }:
        return row
    guard = 0
    while guard < 20:
        guard += 1
        previous = row.state
        await _step(session, row, brand)
        if row.state == previous or AccountBootstrapState(row.state) in HUMAN_STOPS:
            break
    if (row.payload or {}).get("last_emitted_state") != row.state:
        session.add(
            SystemEvent(
                name="account.bootstrap.advanced",
                payload={"platform": platform, "state": row.state, "checkpoint": row.checkpoint_kind},
                simulation=row.simulation,
            )
        )
        payload = dict(row.payload or {})
        payload["last_emitted_state"] = row.state
        row.payload = payload
        await post_message(
            session,
            sender=AgentName.ACCOUNT_BOOTSTRAP,
            recipient=AgentName.DIRECTOR,
            message_type=AgentMessageType.HANDOFF,
            task="account_bootstrap",
            related_entity_type="account_bootstrap",
            related_entity_id=row.id,
            payload={"platform": platform, "state": row.state, "blocked_reason": row.blocked_reason},
            confidence=0.9,
        )
    return row


async def resume_after_human_action(session: AsyncSession, action: HumanAction) -> None:
    if not action.platform:
        return
    row = await _load_or_create(session, action.platform, await _ensure_brand(session))
    payload = dict(row.payload or {})
    payload["owner_completed_checkpoint"] = action.checkpoint_kind or row.checkpoint_kind
    payload["owner_completed_at"] = datetime.now(UTC).isoformat()
    row.payload = payload
    row.state = AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION.value
    row.blocked_reason = (
        f"{row.platform.title()} checkpoint completed by owner. "
        "Waiting for the platform to confirm the external state."
    )
    session.add(
        SystemEvent(
            name="account.bootstrap.awaiting_confirmation",
            payload={"platform": row.platform, "checkpoint": row.checkpoint_kind},
            simulation=row.simulation,
        )
    )
    await session.flush()
    await _apply_external_proof(session, row)
    await advance_platform(session, action.platform)


async def on_oauth_tokens_persisted(session: AsyncSession, platform: str) -> None:
    row = (
        await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == platform))
    ).scalar_one_or_none()
    if row is None:
        return
    write_boundary(row, {"oauth_authenticated": True})
    if row.state in {
        AccountBootstrapState.OAUTH_REQUIRED.value,
        AccountBootstrapState.OAUTH_IN_PROGRESS.value,
        AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION.value,
    }:
        row.checkpoint_kind = "oauth_consent"
        await _apply_external_proof(session, row)
        await advance_platform(session, platform)


async def bootstrap_snapshot(session: AsyncSession) -> dict[str, Any]:
    rows = await advance_all(session)
    return {
        "brand": await _brand_payload(session),
        "platforms": {row.platform: _row_payload(row) for row in rows},
    }


def _row_payload(row: AccountBootstrap) -> dict[str, Any]:
    package = (row.payload or {}).get("package") or {}
    handoff = (row.payload or {}).get("handoff") or {}
    return {
        "platform": row.platform,
        "state": row.state,
        "selected_handle": row.selected_handle,
        "handle_candidates": row.handle_candidates,
        "profile": row.profile,
        "package": package,
        "handoff": handoff,
        "checkpoint_kind": row.checkpoint_kind,
        "blocked_reason": row.blocked_reason,
        "simulation": row.simulation,
        "ready": row.state == AccountBootstrapState.READY.value,
    }


async def _ensure_brand(session: AsyncSession) -> BrandConfig:
    existing = await session.execute(select(BrandConfig).where(BrandConfig.active.is_(True)).limit(1))
    brand = existing.scalar_one_or_none()
    spec = choose_brand()
    if brand is None:
        brand = BrandConfig(
            name=spec["name"],
            version=1,
            handles={"primary": handle_candidates(Platform.YOUTUBE.value)[0], "binding": True},
            short_description=spec["short_description"],
            tone=spec["tone"],
            visual_identity=spec["visual_identity"],
            content_pillars=spec["content_pillars"],
            audience=spec["audience"],
            voice_personality=spec["voice_personality"],
            title_conventions=spec["title_conventions"],
            caption_conventions=spec["caption_conventions"],
            active=True,
        )
        session.add(brand)
        await session.flush()
        await post_message(
            session,
            sender=AgentName.BRAND,
            recipient=AgentName.ACCOUNT_BOOTSTRAP,
            message_type=AgentMessageType.RESULT,
            task="brand_selected",
            related_entity_type="brand_config",
            related_entity_id=brand.id,
            payload={"name": brand.name, "automatic": True},
            confidence=0.95,
        )
    return brand


async def _load_or_create(
    session: AsyncSession, platform: str, brand: BrandConfig
) -> AccountBootstrap:
    result = await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == platform))
    row = result.scalar_one_or_none()
    settings = get_settings()
    if row is None:
        row = AccountBootstrap(
            platform=platform,
            state=AccountBootstrapState.PLANNING.value,
            brand_id=brand.id,
            handle_candidates=handle_candidates(platform),
            simulation=settings.bootstrap_simulation,
            payload={},
        )
        session.add(row)
        await session.flush()
    await _ensure_connection(session, platform)
    return row


async def _ensure_connection(session: AsyncSession, platform: str) -> PlatformConnection:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    row = result.scalar_one_or_none()
    if row is None:
        row = PlatformConnection(
            platform=platform,
            state=ConnectionState.NOT_CONFIGURED.value,
            scopes=[],
            metadata_json={"source": "bootstrap_orchestrator"},
        )
        session.add(row)
        await session.flush()
    return row


async def _step(session: AsyncSession, row: AccountBootstrap, brand: BrandConfig) -> None:
    state = AccountBootstrapState(row.state)
    settings = get_settings()
    spec = choose_brand()
    if state == AccountBootstrapState.PLANNING:
        row.state = AccountBootstrapState.BRAND_READY.value
        return
    if state == AccountBootstrapState.BRAND_READY:
        await _prepare_package(session, row, brand, spec, settings)
        return
    if state == AccountBootstrapState.SIGNUP_PREPARED:
        await _enter_signup(session, row)
        return
    if state == AccountBootstrapState.SIGNUP_IN_PROGRESS:
        await _progress_signup(session, row)
        return
    if state == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED:
        return
    if state == AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION:
        await _apply_external_proof(session, row)
        return
    if state == AccountBootstrapState.ACCOUNT_CREATED:
        row.state = AccountBootstrapState.PROFILE_CONFIGURING.value
        return
    if state == AccountBootstrapState.PROFILE_CONFIGURING:
        _apply_profile_assets(row)
        row.state = AccountBootstrapState.PROFILE_READY.value
        return
    if state == AccountBootstrapState.PROFILE_READY:
        await _progress_developer_app(session, row)
        return
    if state == AccountBootstrapState.DEVELOPER_APP_REQUIRED:
        return
    if state == AccountBootstrapState.DEVELOPER_APP_READY:
        await _enter_oauth(session, row)
        return
    if state == AccountBootstrapState.OAUTH_REQUIRED:
        return
    if state == AccountBootstrapState.OAUTH_IN_PROGRESS:
        await _apply_external_proof(session, row)
        return
    if state == AccountBootstrapState.CONNECTED:
        await _enter_ready(session, row)
        return
    if state == AccountBootstrapState.PLATFORM_REVIEW_REQUIRED:
        return
    if state == AccountBootstrapState.FAILED_RETRYABLE:
        return


async def _prepare_package(
    session: AsyncSession,
    row: AccountBootstrap,
    brand: BrandConfig,
    spec: dict[str, Any],
    settings,
) -> None:
    handle = next_available_handle(row.platform, row.payload.get("rejected_handles") or [])
    if handle is None:
        row.state = AccountBootstrapState.FAILED_RETRYABLE.value
        row.blocked_reason = "All candidate handles were unavailable."
        return
    attempted = list(row.payload.get("rejected_handles") or [])
    if handle == handle_candidates(row.platform)[0] and settings.bootstrap_simulation:
        attempted.append(handle)
        handle = next_available_handle(row.platform, attempted) or handle
    row.selected_handle = handle
    row.handle_candidates = handle_candidates(row.platform)
    package = build_activation_package(row.platform, brand=spec, handle=handle, settings=settings)
    row.profile = {
        "account_name": package["brand_name"],
        "handle": handle,
        "bio": package["bio"],
        "description": package["description"],
        "category": package["category"],
        "content_pillars": package["content_pillars"],
        "publishing_strategy": package["publishing_strategy"],
        "avatar_path": package["avatar_path"],
        "banner_path": package["banner_path"],
    }
    payload = dict(row.payload or {})
    payload["rejected_handles"] = attempted
    payload["package"] = package
    payload["prefill"] = {
        "name": brand.name,
        "handle": handle,
        "bio": package["bio"],
        "description": package["description"],
    }
    row.payload = payload
    row.state = AccountBootstrapState.SIGNUP_PREPARED.value


async def _enter_signup(session: AsyncSession, row: AccountBootstrap) -> None:
    package = (row.payload or {}).get("package") or {}
    url = package.get("handoff_url") or official_flows(row.platform)["signup"]
    record = launch_official_page(url, purpose="signup")
    payload = dict(row.payload or {})
    payload["handoff"] = record
    payload["signup_url"] = url
    row.payload = payload
    write_boundary(row, {"signup_launched": True, "handoff_url": url})
    session.add(
        SystemEvent(
            name="account.bootstrap.handoff",
            payload={"platform": row.platform, "url": url, "opened": record.get("opened")},
            simulation=row.simulation,
        )
    )
    row.state = AccountBootstrapState.SIGNUP_IN_PROGRESS.value


async def _progress_signup(session: AsyncSession, row: AccountBootstrap) -> None:
    proof = await verify_account(session, row)
    if proof.verified:
        _mark_account_created(row, proof.evidence)
        return
    messages = await mailbox_provider(simulated=bool(get_settings().bootstrap_simulation)).list_verification_messages(
        row.platform
    )
    if messages:
        payload = dict(row.payload or {})
        payload["verification_mailbox"] = surface_messages(messages)
        row.payload = payload
    kind = first_human_checkpoint(row.platform)
    row.state = AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value
    row.checkpoint_kind = kind
    row.blocked_reason = _checkpoint_text(row.platform, kind)
    await _checkpoint_action(session, row)


async def _progress_developer_app(session: AsyncSession, row: AccountBootstrap) -> None:
    proof = await verify_developer_app(session, row)
    if proof.verified:
        row.state = AccountBootstrapState.DEVELOPER_APP_READY.value
        row.checkpoint_kind = None
        row.blocked_reason = None
        return
    url = official_flows(row.platform)["developer"]
    record = launch_official_page(url, purpose="developer_app")
    payload = dict(row.payload or {})
    payload["developer_handoff"] = record
    row.payload = payload
    row.state = AccountBootstrapState.DEVELOPER_APP_REQUIRED.value
    row.checkpoint_kind = "app_review"
    row.blocked_reason = _checkpoint_text(row.platform, "app_review")
    await _checkpoint_action(session, row)


async def _enter_oauth(session: AsyncSession, row: AccountBootstrap) -> None:
    proof = await verify_oauth(session, row)
    if proof.verified:
        row.state = AccountBootstrapState.CONNECTED.value
        row.checkpoint_kind = None
        row.blocked_reason = None
        await _mark_connection(session, row.platform, ConnectionState.CONNECTED)
        return
    url = official_oauth_start_path(row.platform)
    settings = get_settings()
    absolute = url if url.startswith("http") else f"http://127.0.0.1:{settings.api_port}{url}"
    record = launch_official_page(absolute, purpose="oauth")
    payload = dict(row.payload or {})
    payload["oauth_handoff"] = record
    payload["oauth_start"] = url
    row.payload = payload
    row.state = AccountBootstrapState.OAUTH_REQUIRED.value
    row.checkpoint_kind = "oauth_consent"
    row.blocked_reason = _checkpoint_text(row.platform, "oauth_consent")
    await _checkpoint_action(session, row)


async def _apply_external_proof(session: AsyncSession, row: AccountBootstrap) -> None:
    kind = row.checkpoint_kind or (row.payload or {}).get("owner_completed_checkpoint")
    if kind in {None, "verification", "channel_creation", "email_verification", "phone_verification", "captcha", "legal_consent"}:
        proof = await verify_account(session, row)
        if proof.verified:
            _mark_account_created(row, proof.evidence)
            return
        _hold_unverified(row, proof)
        return
    if kind == "app_review":
        proof = await verify_developer_app(session, row)
        if proof.verified:
            row.state = AccountBootstrapState.DEVELOPER_APP_READY.value
            row.checkpoint_kind = None
            row.blocked_reason = None
            return
        _hold_unverified(row, proof)
        return
    if kind == "oauth_consent":
        proof = await verify_oauth(session, row)
        if proof.verified:
            row.state = AccountBootstrapState.CONNECTED.value
            row.checkpoint_kind = None
            row.blocked_reason = None
            await _mark_connection(session, row.platform, ConnectionState.CONNECTED)
            return
        _hold_unverified(row, proof)
        return
    _hold_unverified(row, None)


def _hold_unverified(row: AccountBootstrap, proof) -> None:
    row.state = AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION.value
    if proof is None:
        row.blocked_reason = (
            f"{row.platform.title()} cannot be marked created until the platform confirms it."
        )
        return
    if proof.can_verify:
        row.blocked_reason = proof.reason
        return
    row.blocked_reason = (
        f"{row.platform.title()} is awaiting external confirmation. "
        "AME will resume automatically when the official API can verify it."
    )


def _mark_account_created(row: AccountBootstrap, evidence: dict[str, Any]) -> None:
    payload = dict(row.payload or {})
    payload["external_evidence"] = evidence
    row.payload = payload
    row.state = AccountBootstrapState.ACCOUNT_CREATED.value
    row.checkpoint_kind = None
    row.blocked_reason = None


def _apply_profile_assets(row: AccountBootstrap) -> None:
    package = (row.payload or {}).get("package") or {}
    profile = dict(row.profile or {})
    profile["avatar_path"] = package.get("avatar_path") or profile.get("avatar_path")
    profile["banner_path"] = package.get("banner_path")
    profile["developer_app"] = package.get("developer_app")
    profile["requested_scopes"] = package.get("requested_scopes")
    profile["privacy_policy_url"] = package.get("privacy_policy_url")
    profile["terms_url"] = package.get("terms_url")
    row.profile = profile


async def _enter_ready(session: AsyncSession, row: AccountBootstrap) -> None:
    row.state = AccountBootstrapState.READY.value
    row.checkpoint_kind = None
    row.blocked_reason = None
    await _mark_connection(session, row.platform, ConnectionState.READY)
    session.add(
        SystemEvent(
            name="account.ready",
            payload={"platform": row.platform, "handle": row.selected_handle},
            simulation=row.simulation,
        )
    )
    await _schedule_ready_queue(session, row.platform)


async def _checkpoint_action(session: AsyncSession, row: AccountBootstrap) -> None:
    kind = row.checkpoint_kind or CHECKPOINT_KIND.get(AccountBootstrapState(row.state), "verification")
    title = f"{row.platform.title()} {kind.replace('_', ' ')}"
    action = await open_human_action(
        session,
        title=title[:200],
        instructions=row.blocked_reason or "Complete the platform checkpoint.",
        category="checkpoint",
        platform=row.platform,
        blocking=False,
        checkpoint_kind=kind,
        details={
            "state": row.state,
            "handle": row.selected_handle,
            "handoff": (row.payload or {}).get("handoff"),
            "package": (row.payload or {}).get("package"),
        },
        classification=HumanActionClass.GENUINELY_HUMAN_REQUIRED,
    )
    if action is not None:
        await notify(
            session,
            NotificationKind.HUMAN_ACTION_REQUIRED,
            title[:200],
            row.blocked_reason or title,
            related_entity_type="account_bootstrap",
            related_entity_id=row.id,
        )


async def _mark_connection(session: AsyncSession, platform: str, state: ConnectionState) -> None:
    connection = await _ensure_connection(session, platform)
    connection.state = state.value
    meta = dict(connection.metadata_json or {})
    meta["bootstrap_state"] = state.value
    connection.metadata_json = meta


def _checkpoint_text(platform: str, kind: str) -> str:
    template = CHECKPOINT_COPY.get(kind, CHECKPOINT_COPY["verification"])
    return template.format(platform=platform.title())


async def _schedule_ready_queue(session: AsyncSession, platform: str) -> None:
    approved = list(
        (
            await session.execute(
                select(ContentItem)
                .where(
                    ContentItem.status.in_(
                        [
                            "approved",
                            "qa",
                            "script_selected",
                            "production",
                            "published",
                            "measuring",
                        ]
                    )
                )
                .order_by(ContentItem.created_at.asc())
                .limit(3)
            )
        ).scalars()
    )
    for content in approved:
        await schedule_publication(
            session,
            content,
            platform if not content.simulation and not get_settings().dry_run else Platform.DRY_RUN.value,
            reason="platform became READY; schedule first permitted publication",
        )


async def _brand_payload(session: AsyncSession) -> dict[str, Any]:
    brand = (await session.execute(select(BrandConfig).where(BrandConfig.active.is_(True)))).scalar_one_or_none()
    if brand is None:
        return choose_brand()
    return {
        "id": str(brand.id),
        "name": brand.name,
        "handles": brand.handles,
        "short_description": brand.short_description,
        "content_pillars": brand.content_pillars,
        "visual_identity": brand.visual_identity,
    }
