"""Prove maximum real bootstrap against simulated external boundaries."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ame.bootstrap.boundary import (
    confirm_simulated_account,
    confirm_simulated_developer_app,
    confirm_simulated_oauth,
)
from ame.bootstrap.orchestrator import advance_all, resume_after_human_action
from ame.config import get_settings
from ame.contracts.enums import AccountBootstrapState, ContentStatus, HumanActionClass
from ame.db.models import AccountBootstrap, BrandConfig, ContentItem, HumanAction, PublishingCalendarSlot
from ame.db.session import async_session_factory, init_database
from ame.observability import configure_logging, get_logger

logger = get_logger("ame.cli.bootstrap_acceptance")
REPORT_PATH = Path(__file__).resolve().with_name("last_bootstrap.json")


def _prepare_env() -> None:
    os.environ.setdefault("AUTONOMOUS_MODE", "true")
    os.environ.setdefault("DRY_RUN", "true")
    os.environ.setdefault("AME_BOOTSTRAP_SIMULATION", "true")
    os.environ.setdefault("AME_BOOTSTRAP_OPEN_BROWSER", "false")
    os.environ.setdefault("AME_ACCEPTANCE_DRIVE_JOBS", "1")
    os.environ.setdefault("OWNER_TIMEZONE", "Europe/Dublin")
    get_settings.cache_clear()


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


async def run_bootstrap_acceptance() -> dict[str, Any]:
    _prepare_env()
    configure_logging()
    init_database()
    started = datetime.now(UTC)
    checks: list[dict[str, Any]] = []

    async with async_session_factory() as session:
        content = ContentItem(
            topic="Bootstrap first publish",
            niche="ai",
            status=ContentStatus.APPROVED.value,
            simulation=True,
        )
        session.add(content)
        await session.flush()
        first = await advance_all(session)
        await session.commit()
        brand = (await session.execute(select(BrandConfig).where(BrandConfig.active.is_(True)))).scalar_one()
        actions = list((await session.execute(select(HumanAction))).scalars())
        states = {row.platform: row.state for row in first}
        youtube = next(row for row in first if row.platform == "youtube")
        package = (youtube.payload or {}).get("package") or {}
        handoff = (youtube.payload or {}).get("handoff") or {}

        checks.append(_check("brand_selected", brand.name == "Signal Brief", brand.name))
        checks.append(
            _check(
                "handle_fallback",
                youtube.selected_handle == "signalbriefhq",
                youtube.selected_handle,
            )
        )
        checks.append(
            _check(
                "profile_assets",
                bool(package.get("avatar_path") and package.get("bio") and package.get("banner_path")),
                {k: package.get(k) for k in ("avatar_path", "banner_path", "bio", "category")},
            )
        )
        checks.append(
            _check(
                "oauth_app_metadata",
                bool(package.get("oauth_redirect_uri") and package.get("developer_app") and package.get("privacy_policy_url")),
                package.get("developer_app"),
            )
        )
        checks.append(
            _check(
                "handoff_launched",
                bool(handoff.get("url")) and handoff.get("attempted") is False,
                handoff,
            )
        )
        checks.append(
            _check(
                "not_signup_prepared",
                all(row.state != AccountBootstrapState.SIGNUP_PREPARED.value for row in first),
                states,
            )
        )
        checks.append(
            _check(
                "stops_at_verification",
                all(row.state == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value for row in first),
                states,
            )
        )
        checks.append(
            _check(
                "one_action_per_platform",
                len(actions) == 3 and {item.classification for item in actions} == {HumanActionClass.GENUINELY_HUMAN_REQUIRED.value},
                [(item.platform, item.checkpoint_kind) for item in actions],
            )
        )

        instagram_action = next(item for item in actions if item.platform == "instagram")
        instagram_action.status = "completed"
        await resume_after_human_action(session, instagram_action)
        await session.commit()
        instagram = (
            await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == "instagram"))
        ).scalar_one()
        checks.append(
            _check(
                "no_fabricated_account",
                instagram.state == AccountBootstrapState.AWAITING_EXTERNAL_CONFIRMATION.value,
                instagram.state,
            )
        )

        confirm_simulated_account(instagram)
        confirm_simulated_developer_app(instagram)
        confirm_simulated_oauth(instagram)
        await resume_after_human_action(session, instagram_action)
        await session.commit()
        instagram = (
            await session.execute(select(AccountBootstrap).where(AccountBootstrap.platform == "instagram"))
        ).scalar_one()
        checks.append(_check("automatic_resume", instagram.state == AccountBootstrapState.READY.value, instagram.state))

        youtube.state = AccountBootstrapState.PLATFORM_REVIEW_REQUIRED.value
        youtube.blocked_reason = "simulated app review"
        await session.flush()
        after_block = await advance_all(session)
        await session.commit()
        blocked = {row.platform: row.state for row in after_block}
        checks.append(
            _check(
                "platform_isolation",
                blocked["youtube"] == AccountBootstrapState.PLATFORM_REVIEW_REQUIRED.value
                and blocked["instagram"] == AccountBootstrapState.READY.value
                and blocked["tiktok"] == AccountBootstrapState.HUMAN_VERIFICATION_REQUIRED.value,
                blocked,
            )
        )

        slots = list((await session.execute(select(PublishingCalendarSlot))).scalars())
        checks.append(_check("first_publication_scheduled", bool(slots), len(slots)))

        before_restart = {row.platform: row.state for row in after_block}

    async with async_session_factory() as session:
        resumed = await advance_all(session)
        await session.commit()
        after_restart = {row.platform: row.state for row in resumed}
        actions_again = list((await session.execute(select(HumanAction))).scalars())
        checks.append(_check("restart_resume", after_restart == before_restart, after_restart))
        checks.append(
            _check(
                "no_duplicate_actions",
                len([item for item in actions_again if item.status == "open"]) <= 2,
                [(item.platform, item.status, item.checkpoint_kind) for item in actions_again],
            )
        )

    failed = [item for item in checks if not item["passed"]]
    payload = {
        "passed": not failed,
        "verdict": "pass" if not failed else "fail",
        "elapsed_seconds": round((datetime.now(UTC) - started).total_seconds(), 2),
        "brand": "Signal Brief",
        "states": after_restart,
        "checks": checks,
        "failed_checks": [item["name"] for item in failed],
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str), flush=True)
    logger.info("bootstrap_acceptance_finished", passed=payload["passed"], failed=payload["failed_checks"])
    return payload


def main() -> None:
    report = asyncio.run(run_bootstrap_acceptance())
    raise SystemExit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
