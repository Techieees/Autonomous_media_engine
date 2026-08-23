"""Maximum real bootstrap: machine steps first, exact human checkpoints only."""

from __future__ import annotations

from ame.contracts.enums import AccountBootstrapState, HumanActionClass, Platform
from ame.db.runtime import reset_database_runtime
from ame.config import get_settings
from sqlalchemy import select
import pytest


def _env(monkeypatch, tmp_path, **extra: str) -> None:
    db = tmp_path / "ame.bootstrap.db"
    posix = db.as_posix()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AUTONOMOUS_MODE", "true")
    monkeypatch.setenv("AME_FORCE_SQLITE", "1")
    monkeypatch.setenv("AME_BOOTSTRAP_SIMULATION", "true")
    monkeypatch.setenv("AME_BOOTSTRAP_OPEN_BROWSER", "false")
    monkeypatch.setenv("OWNER_TIMEZONE", "Europe/Dublin")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_database_runtime()
    monkeypatch.setattr(
        "ame.db.runtime.sqlite_paths",
        lambda: (f"sqlite+aiosqlite:///{posix}", f"sqlite:///{posix}"),
    )


@pytest.fixture
def bootstrap_env(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    from ame.db.session import init_database

    init_database()
    return tmp_path


@pytest.mark.asyncio
async def test_automatic_brand_and_handle_fallback(bootstrap_env) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.db.models import BrandConfig
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        rows = await advance_all(session)
        await session.commit()
        brand = (await session.execute(select(BrandConfig))).scalar_one()
    assert brand.name == "Signal Brief"
    youtube = next(row for row in rows if row.platform == Platform.YOUTUBE.value)
    assert youtube.selected_handle == "signalbriefhq"
    package = youtube.payload["package"]
    assert package["avatar_path"]
    assert package["banner_path"]
    assert package["oauth_redirect_uri"]
    assert package["privacy_policy_url"]
    assert package["developer_app"]["name"]


@pytest.mark.asyncio
async def test_does_not_stop_at_signup_prepared(bootstrap_env) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        rows = await advance_all(session)
        await session.commit()
    assert all(row.state != AccountBootstrapState.SIGNUP_PREPARED.value for row in rows)
    assert all(row.state == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value for row in rows)
    assert all((row.payload or {}).get("handoff", {}).get("url") for row in rows)


@pytest.mark.asyncio
async def test_no_unnecessary_human_actions(bootstrap_env) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.db.models import HumanAction
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        await advance_all(session)
        await advance_all(session)
        await session.commit()
        actions = list((await session.execute(select(HumanAction))).scalars())
    assert len(actions) == 3
    assert {item.platform for item in actions} == {"youtube", "instagram", "tiktok"}
    assert all(item.classification == HumanActionClass.GENUINELY_HUMAN_REQUIRED.value for item in actions)
    assert all(item.checkpoint_kind in {"channel_creation", "email_verification"} for item in actions)


@pytest.mark.asyncio
async def test_button_does_not_fabricate_account_created(bootstrap_env) -> None:
    from ame.bootstrap.orchestrator import advance_all, resume_after_human_action
    from ame.db.models import AccountBootstrap, HumanAction
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        await advance_all(session)
        action = (
            await session.execute(select(HumanAction).where(HumanAction.platform == "youtube"))
        ).scalar_one()
        action.status = "completed"
        await resume_after_human_action(session, action)
        await session.commit()
        youtube = (
            await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == "youtube"))
        ).scalar_one()
    assert youtube.state == AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION.value
    assert youtube.state != AccountBootstrapState.ACCOUNT_CREATED.value


@pytest.mark.asyncio
async def test_resume_after_external_confirmation(bootstrap_env) -> None:
    from ame.bootstrap.boundary import (
        confirm_simulated_account,
        confirm_simulated_developer_app,
        confirm_simulated_oauth,
    )
    from ame.bootstrap.orchestrator import advance_all, resume_after_human_action
    from ame.db.models import AccountBootstrap, HumanAction
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        await advance_all(session)
        action = (
            await session.execute(select(HumanAction).where(HumanAction.platform == "youtube"))
        ).scalar_one()
        youtube = (
            await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == "youtube"))
        ).scalar_one()
        confirm_simulated_account(youtube)
        confirm_simulated_developer_app(youtube)
        confirm_simulated_oauth(youtube)
        action.status = "completed"
        await resume_after_human_action(session, action)
        await session.commit()
        youtube = (
            await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == "youtube"))
        ).scalar_one()
    assert youtube.state == AccountBootstrapState.READY.value


@pytest.mark.asyncio
async def test_one_platform_blocked_does_not_stop_others(bootstrap_env) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        rows = await advance_all(session)
        youtube = next(row for row in rows if row.platform == "youtube")
        youtube.state = AccountBootstrapState.PLATFORM_REVIEW_REQUIRED.value
        youtube.blocked_reason = "app review"
        await session.flush()
        again = await advance_all(session)
        await session.commit()
    states = {row.platform: row.state for row in again}
    assert states["youtube"] == AccountBootstrapState.PLATFORM_REVIEW_REQUIRED.value
    assert states["instagram"] == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value
    assert states["tiktok"] == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value


@pytest.mark.asyncio
async def test_ready_schedules_first_publication(bootstrap_env) -> None:
    from ame.bootstrap.boundary import (
        confirm_simulated_account,
        confirm_simulated_developer_app,
        confirm_simulated_oauth,
    )
    from ame.bootstrap.orchestrator import advance_all, resume_after_human_action
    from ame.contracts.enums import ContentStatus
    from ame.db.models import AccountBootstrap, ContentItem, HumanAction, PublishingCalendarSlot
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        content = ContentItem(
            topic="Ready queue",
            niche="ai",
            status=ContentStatus.APPROVED.value,
            simulation=True,
        )
        session.add(content)
        await session.flush()
        await advance_all(session)
        action = (
            await session.execute(select(HumanAction).where(HumanAction.platform == "tiktok"))
        ).scalar_one()
        tiktok = (
            await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == "tiktok"))
        ).scalar_one()
        confirm_simulated_account(tiktok)
        confirm_simulated_developer_app(tiktok)
        confirm_simulated_oauth(tiktok)
        action.status = "completed"
        await resume_after_human_action(session, action)
        await session.commit()
        slots = list((await session.execute(select(PublishingCalendarSlot))).scalars())
    assert slots
    assert slots[0].platform == "dry_run"


@pytest.mark.asyncio
async def test_restart_resume_during_bootstrap(bootstrap_env) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.db.models import AccountBootstrap
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        await advance_all(session)
        await session.commit()
    async with async_session_factory() as session:
        again = await advance_all(session)
        await session.commit()
        rows = list((await session.execute(select(AccountBootstrap))).scalars())
    assert len(again) == 3
    assert {row.state for row in rows} == {AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value}
