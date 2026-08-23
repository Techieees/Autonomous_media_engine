from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ame.config import get_settings
from ame.contracts.enums import JobName
from ame.db.session import async_session_factory, init_database
from ame.jobs.queue import JobQueue
from ame.observability import configure_logging, get_logger
from ame.ops.clock import autonomous_enabled, owner_local_date, scheduler_fast

configure_logging()
logger = get_logger("ame.scheduler")


def schedule_intervals() -> dict[str, timedelta]:
    if scheduler_fast():
        pulse = timedelta(seconds=1)
        return {
            JobName.DIRECTOR_TICK.value: pulse,
            JobName.TREND_INGEST.value: pulse,
            JobName.OPPORTUNITY_SCORE.value: pulse,
            JobName.NICHE_EVALUATE.value: timedelta(seconds=5),
            JobName.STUCK_JOB_RECOVERY.value: pulse,
            JobName.BOOTSTRAP_TICK.value: pulse,
            JobName.CALENDAR_TICK.value: pulse,
            JobName.ANALYTICS_SWEEP.value: pulse,
            JobName.DAILY_PLAN.value: timedelta(seconds=2),
            JobName.DAILY_REPORT.value: timedelta(seconds=3),
            JobName.REVENUE_SYNC.value: timedelta(seconds=8),
            JobName.BRAND_PROPOSE.value: timedelta(seconds=8),
        }
    intervals = {
        JobName.DIRECTOR_TICK.value: timedelta(minutes=5),
        JobName.TREND_INGEST.value: timedelta(minutes=15),
        JobName.NICHE_EVALUATE.value: timedelta(hours=6),
        JobName.STUCK_JOB_RECOVERY.value: timedelta(minutes=2),
        JobName.REVENUE_SYNC.value: timedelta(hours=12),
        JobName.BRAND_PROPOSE.value: timedelta(days=1),
    }
    if autonomous_enabled():
        intervals.update(
            {
                JobName.BOOTSTRAP_TICK.value: timedelta(minutes=10),
                JobName.CALENDAR_TICK.value: timedelta(minutes=5),
                JobName.ANALYTICS_SWEEP.value: timedelta(minutes=20),
                JobName.DAILY_PLAN.value: timedelta(hours=1),
                JobName.DAILY_REPORT.value: timedelta(hours=1),
            }
        )
    return intervals


def _idempotency_key(name: str, now: datetime, interval: timedelta) -> str:
    if name in {JobName.DAILY_PLAN.value, JobName.DAILY_REPORT.value}:
        return f"sched:{name}:{owner_local_date(now)}"
    if scheduler_fast():
        bucket = now.strftime("%Y%m%d%H%M%S")
        return f"sched:{name}:{bucket}:{int(interval.total_seconds())}"
    return f"sched:{name}:{now.strftime('%Y%m%d%H%M')[:12]}:{int(interval.total_seconds())}"


async def tick() -> list[str]:
    now = datetime.now(UTC)
    enqueued: list[str] = []
    async with async_session_factory() as session:
        queue = JobQueue(session)
        for name, interval in schedule_intervals().items():
            job = await queue.enqueue(
                name,
                {"trigger": "scheduler", "autonomous": autonomous_enabled()},
                idempotency_key=_idempotency_key(name, now, interval),
                correlation_id=str(uuid4()),
                run_after=now,
            )
            enqueued.append(job.name)
        await session.commit()
    logger.info("scheduler_enqueued", count=len(enqueued), autonomous=autonomous_enabled())
    return enqueued


async def main() -> None:
    init_database()
    settings = get_settings()
    logger.info(
        "scheduler_started",
        env=settings.app_env,
        autonomous=settings.autonomous_mode,
        fast=settings.scheduler_fast,
        timezone=settings.owner_timezone,
    )
    pause = 0.25 if scheduler_fast() else 60
    while True:
        try:
            await tick()
        except Exception:  # noqa: BLE001
            logger.exception("scheduler_tick_failed")
        await asyncio.sleep(pause)


if __name__ == "__main__":
    asyncio.run(main())
