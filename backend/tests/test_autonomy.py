"""Silent autonomous operation: scheduler, plans, handoffs, bootstrap, reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from ame.config import get_settings
from ame.contracts.enums import (
    AccountBootstrapState,
    AgentMessageType,
    AgentName,
    CalendarSlotStatus,
    ContentStatus,
    HumanActionClass,
    JobName,
    JobStatus,
    Platform,
)
from ame.db.runtime import reset_database_runtime
from ame.jobs.scheduler import schedule_intervals, tick
from ame.ops.human_actions import classify_action, open_human_action


def _autonomy_env(monkeypatch, tmp_path, **extra: str) -> None:
    db = tmp_path / "ame.test.db"
    posix = db.as_posix()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AUTONOMOUS_MODE", "true")
    monkeypatch.setenv("OWNER_TIMEZONE", "Europe/Dublin")
    monkeypatch.setenv("AME_FORCE_SQLITE", "1")
    monkeypatch.setenv("AME_BOOTSTRAP_SIMULATION", "true")
    monkeypatch.setenv("AME_SCHEDULER_FAST", "true")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_database_runtime()
    monkeypatch.setattr(
        "ame.db.runtime.sqlite_paths",
        lambda: (f"sqlite+aiosqlite:///{posix}", f"sqlite:///{posix}"),
    )


@pytest.fixture
def autonomy(monkeypatch, tmp_path):
    _autonomy_env(monkeypatch, tmp_path)
    from ame.db.session import init_database

    init_database()
    return tmp_path


@pytest.mark.asyncio
async def test_scheduler_queues_work_without_run_cycle(autonomy) -> None:
    names = await tick()
    assert JobName.TREND_INGEST.value in names
    assert JobName.DIRECTOR_TICK.value in names
    assert JobName.BOOTSTRAP_TICK.value in names
    assert JobName.DAILY_PLAN.value in names
    from ame.db.session import async_session_factory
    from ame.db.models import Job

    async with async_session_factory() as session:
        rows = list((await session.execute(select(Job))).scalars())
    assert any(row.name == JobName.TREND_INGEST.value for row in rows)
    assert all("run_cycle" not in (row.payload or {}).get("trigger", "") for row in rows)


def test_fast_scheduler_does_not_use_hour_buckets(autonomy) -> None:
    intervals = schedule_intervals()
    assert intervals[JobName.DIRECTOR_TICK.value] <= timedelta(seconds=2)


@pytest.mark.asyncio
async def test_daily_plan_is_not_recreated(autonomy) -> None:
    from ame.db.session import async_session_factory
    from ame.ops.daily_plan import ensure_daily_plan

    async with async_session_factory() as session:
        first = await ensure_daily_plan(session)
        second = await ensure_daily_plan(session)
        await session.commit()
        assert first.id == second.id


@pytest.mark.asyncio
async def test_agent_handoff_message(autonomy) -> None:
    from ame.agents.messaging import post_message
    from ame.db.models import AgentMessage
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        msg = await post_message(
            session,
            sender=AgentName.TREND_SCOUT,
            recipient=AgentName.DIRECTOR,
            message_type=AgentMessageType.PROPOSAL,
            task="opportunity",
            payload={"score": 0.8},
            confidence=0.8,
        )
        await session.commit()
        loaded = await session.get(AgentMessage, msg.id)
    assert loaded is not None
    assert loaded.kind == AgentMessageType.PROPOSAL.value
    assert loaded.body["payload"]["score"] == 0.8


@pytest.mark.asyncio
async def test_human_action_classification_suppresses_technical(autonomy) -> None:
    from ame.db.models import HumanAction
    from ame.db.session import async_session_factory

    assert (
        classify_action(category="ops", title="FFmpeg timeout", instructions="retry render")
        == HumanActionClass.TECHNICAL_FAILURE
    )
    async with async_session_factory() as session:
        created = await open_human_action(
            session,
            title="Render failed",
            instructions="ffmpeg timeout, retry automatically",
            category="ops",
        )
        genuine = await open_human_action(
            session,
            title="YouTube OAuth consent",
            instructions="Approve the official consent screen.",
            category="oauth",
            platform="youtube",
            classification=HumanActionClass.GENUINELY_HUMAN_REQUIRED,
        )
        await session.commit()
        count = len(list((await session.execute(select(HumanAction))).scalars()))
    assert created is None
    assert genuine is not None
    assert count == 1


@pytest.mark.asyncio
async def test_automatic_brand_and_handle_fallback(autonomy) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.db.models import AccountBootstrap, BrandConfig
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        rows = await advance_all(session)
        await session.commit()
        brand = (await session.execute(select(BrandConfig))).scalar_one()
    assert brand.name == "Signal Brief"
    youtube = next(row for row in rows if row.platform == Platform.YOUTUBE.value)
    assert youtube.selected_handle != "signalbrief"
    assert youtube.selected_handle in {"signalbriefhq", "thesignalbrief", "signalbrief_lab"}
    assert youtube.state == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value
    assert youtube.payload.get("package", {}).get("handoff_url")
    assert youtube.state != AccountBootstrapState.SIGNUP_PREPARED.value


@pytest.mark.asyncio
async def test_bootstrap_pauses_at_human_checkpoint_and_resumes(autonomy) -> None:
    from ame.bootstrap.boundary import confirm_simulated_account, confirm_simulated_developer_app, confirm_simulated_oauth
    from ame.bootstrap.orchestrator import advance_all, resume_after_human_action
    from ame.db.models import AccountBootstrap, HumanAction
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        await advance_all(session)
        await session.commit()
        action = (
            await session.execute(
                select(HumanAction).where(HumanAction.platform == Platform.INSTAGRAM.value)
            )
        ).scalar_one()
        instagram = (
            await session.execute(
                select(AccountBootstrap).where(AccountBootstrap.platform == Platform.INSTAGRAM.value)
            )
        ).scalar_one()
        assert instagram.state == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value
        action.status = "completed"
        await resume_after_human_action(session, action)
        await session.commit()
        instagram = (
            await session.execute(
                select(AccountBootstrap).where(AccountBootstrap.platform == Platform.INSTAGRAM.value)
            )
        ).scalar_one()
        assert instagram.state == AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION.value
        confirm_simulated_account(instagram)
        confirm_simulated_developer_app(instagram)
        confirm_simulated_oauth(instagram)
        await resume_after_human_action(session, action)
        await session.commit()
        instagram = (
            await session.execute(
                select(AccountBootstrap).where(AccountBootstrap.platform == Platform.INSTAGRAM.value)
            )
        ).scalar_one()
    assert instagram.state == AccountBootstrapState.READY.value


@pytest.mark.asyncio
async def test_one_platform_blocked_does_not_stop_others(autonomy) -> None:
    from ame.bootstrap.orchestrator import advance_all
    from ame.contracts.enums import AccountBootstrapState
    from ame.db.models import AccountBootstrap
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        rows = await advance_all(session)
        youtube = next(row for row in rows if row.platform == "youtube")
        youtube.state = AccountBootstrapState.PLATFORM_REVIEW_REQUIRED.value
        youtube.blocked_reason = "platform review"
        await session.flush()
        again = await advance_all(session)
        await session.commit()
    states = {row.platform: row.state for row in again}
    assert states["youtube"] == AccountBootstrapState.PLATFORM_REVIEW_REQUIRED.value
    assert states["instagram"] == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value
    assert states["tiktok"] == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value


@pytest.mark.asyncio
async def test_calendar_and_report(autonomy) -> None:
    from ame.db.models import ContentItem, PublishingCalendarSlot
    from ame.db.session import async_session_factory
    from ame.ops.calendar import schedule_publication
    from ame.ops.reports import generate_daily_report

    async with async_session_factory() as session:
        content = ContentItem(topic="Autonomy test", niche="ai", status=ContentStatus.APPROVED.value, simulation=True)
        session.add(content)
        await session.flush()
        slot = await schedule_publication(session, content, "dry_run", reason="ready")
        report = await generate_daily_report(session, finalize=True)
        await session.commit()
        assert slot.status == CalendarSlotStatus.PLANNED.value
        assert report.finalized is True
        assert report.body["today"]["videos_produced"] >= 0


@pytest.mark.asyncio
async def test_watchdog_recovers_expired_lease(autonomy) -> None:
    from ame.db.models import Job
    from ame.db.session import async_session_factory
    from ame.ops.watchdog import handle_watchdog

    async with async_session_factory() as session:
        job = Job(
            name=JobName.RESEARCH.value,
            status=JobStatus.LEASED.value,
            payload={},
            idempotency_key=f"lease-test:{uuid4()}",
            run_after=datetime.now(UTC) - timedelta(minutes=10),
            leased_until=datetime.now(UTC) - timedelta(minutes=5),
            leased_by="dead-worker",
            max_attempts=5,
        )
        session.add(job)
        await session.flush()
        await handle_watchdog(session, job)
        await session.commit()
        fresh = await session.get(Job, job.id)
    assert fresh is not None
    assert fresh.status == JobStatus.RETRY_WAIT.value


@pytest.mark.asyncio
async def test_no_owner_confirmation_for_normal_director_flow(autonomy) -> None:
    from ame.agents.director import Director
    from ame.contracts.enums import AgentRunStatus
    from ame.contracts.schemas import AgentContext, AgentInput
    from ame.db.models import AgentTask, HumanAction
    from ame.db.session import async_session_factory

    async with async_session_factory() as session:
        task = AgentTask(agent=AgentName.DIRECTOR.value, status="running", payload={})
        session.add(task)
        await session.flush()
        result = await Director(session).run(
            AgentInput(task_id=task.id, agent=AgentName.DIRECTOR, payload={}),
            AgentContext(dry_run=True, simulation=True),
        )
        await session.commit()
        actions = list((await session.execute(select(HumanAction))).scalars())
    assert result.status in {AgentRunStatus.SUCCEEDED, AgentRunStatus.BUDGET_BLOCKED}
    assert all(
        getattr(action, "classification", "genuinely_human_required")
        != HumanActionClass.TECHNICAL_FAILURE.value
        for action in actions
    )


@pytest.mark.asyncio
async def test_duplicate_publish_key_stable() -> None:
    from ame.pipeline.advance import idempotency_key

    content_id = uuid4()
    assert idempotency_key("publish", content_id, "youtube") == f"publish:{content_id}:youtube"
