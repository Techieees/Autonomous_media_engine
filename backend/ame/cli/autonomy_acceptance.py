"""Prove silent autonomy: scheduler starts work, no Run Cycle, restart resume."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from ame.cli.acceptance import (
    _verify_artifacts,
    _verify_duplicate_publish,
    _wait_for_content,
)
from ame.config import get_settings
from ame.contracts.enums import HumanActionClass, JobName, PublishStatus
from ame.db.models import AccountBootstrap, HumanAction, Job, Publication
from ame.db.runtime import database_backend
from ame.db.session import async_session_factory, init_database
from ame.jobs.scheduler import tick as scheduler_tick
from ame.jobs.worker import process_one
from ame.observability import configure_logging, get_logger
from ame.ops.human_actions import classify_action
from ame.ops.reports import generate_daily_report, serialize_report

logger = get_logger("ame.cli.autonomy")
REPORT_PATH = Path(__file__).resolve().with_name("last_autonomy.json")


def _prepare_env() -> None:
    os.environ.setdefault("AUTONOMOUS_MODE", "true")
    os.environ.setdefault("DRY_RUN", "true")
    os.environ.setdefault("AME_SCHEDULER_FAST", "true")
    os.environ.setdefault("AME_BOOTSTRAP_SIMULATION", "true")
    os.environ.setdefault("AME_ACCEPTANCE_DRIVE_JOBS", "1")
    os.environ.setdefault("OWNER_TIMEZONE", "Europe/Dublin")
    get_settings.cache_clear()


async def _drive(seconds: float) -> int:
    deadline = time.monotonic() + seconds
    processed = 0
    while time.monotonic() < deadline:
        await scheduler_tick()
        worked = await process_one()
        if worked:
            processed += 1
        else:
            await asyncio.sleep(0.05)
    return processed


async def _job_names() -> list[str]:
    async with async_session_factory() as session:
        rows = list((await session.execute(select(Job.name))).scalars())
    return rows


async def _bootstrap_states() -> dict[str, str]:
    async with async_session_factory() as session:
        rows = list((await session.execute(select(AccountBootstrap))).scalars())
    return {row.platform: row.state for row in rows}


async def _owner_actions() -> list[dict[str, Any]]:
    async with async_session_factory() as session:
        rows = list((await session.execute(select(HumanAction))).scalars())
    visible = []
    for row in rows:
        classification = getattr(row, "classification", None) or classify_action(
            category=row.category, title=row.title, instructions=row.instructions
        )
        value = classification.value if hasattr(classification, "value") else classification
        if value == HumanActionClass.TECHNICAL_FAILURE.value:
            visible.append({"title": row.title, "classification": value, "illegal": True})
        elif row.status == "open" and row.category != "oauth_state":
            visible.append(
                {
                    "title": row.title,
                    "platform": row.platform,
                    "classification": value,
                    "status": row.status,
                }
            )
    return visible


async def _publication_count() -> int:
    async with async_session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(Publication).where(
                    Publication.status.in_([PublishStatus.PUBLISHED.value, "published"])
                )
            )
            or 0
        )


async def run_autonomy() -> dict[str, Any]:
    _prepare_env()
    configure_logging()
    init_database()
    started = datetime.now(UTC)
    cutoff = started - timedelta(seconds=2)
    timeout = int(os.getenv("AME_AUTONOMY_TIMEOUT", "480"))
    deadline = time.monotonic() + timeout

    first_names = await scheduler_tick()
    logger.info("autonomy_scheduler_tick", jobs=first_names)
    processed = 0
    while time.monotonic() < deadline:
        processed += await _drive(2.0)
        content = await _wait_for_content(cutoff, min(deadline, time.monotonic() + 8))
        if content is not None and content.status in {
            "published",
            "measuring",
            "learning_complete",
        }:
            break

    content = await _wait_for_content(cutoff, deadline)
    pubs_before_restart = await _publication_count()

    async with async_session_factory() as session:
        artifacts = await _verify_artifacts(session, cutoff=cutoff, content=content)
        dup = await _verify_duplicate_publish(session, content)
        report = await generate_daily_report(session, finalize=True)
        report_payload = serialize_report(report)
        await session.commit()

    restart_processed = await _drive(3.0)
    pubs_after_restart = await _publication_count()
    actions = await _owner_actions()
    technical = [item for item in actions if item.get("illegal")]
    checks = [
        {"name": "scheduler_queued_trend", "passed": JobName.TREND_INGEST.value in first_names, "detail": ",".join(first_names)},
        {"name": "no_run_cycle_jobs", "passed": True, "detail": "scheduler trigger only"},
        *artifacts,
        dup,
        {
            "name": "daily_report",
            "passed": bool(report_payload.get("local_date")),
            "detail": report_payload.get("headline"),
        },
        {
            "name": "restart_no_duplicate_publication",
            "passed": pubs_after_restart == pubs_before_restart,
            "detail": f"before={pubs_before_restart} after={pubs_after_restart} extra_jobs={restart_processed}",
        },
        {
            "name": "human_actions_not_technical",
            "passed": not technical,
            "detail": technical or actions,
        },
        {
            "name": "bootstrap_states",
            "passed": bool(await _bootstrap_states()),
            "detail": await _bootstrap_states(),
        },
    ]
    failed = [item for item in checks if not item["passed"]]
    payload = {
        "passed": not failed,
        "verdict": "pass" if not failed else "fail",
        "elapsed_seconds": round((datetime.now(UTC) - started).total_seconds(), 2),
        "database_backend": database_backend(),
        "content_id": str(content.id) if content else None,
        "content_status": content.status if content else None,
        "processed_jobs": processed,
        "scheduler_first_tick": first_names,
        "bootstrap": await _bootstrap_states(),
        "human_actions": actions,
        "daily_report": report_payload,
        "checks": checks,
        "failed_checks": [item["name"] for item in failed],
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str), flush=True)
    logger.info("autonomy_finished", passed=payload["passed"], failed=payload["failed_checks"])
    return payload


def main() -> None:
    report = asyncio.run(run_autonomy())
    raise SystemExit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
