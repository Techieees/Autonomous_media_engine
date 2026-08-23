from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from ame.config import get_settings
from ame.db.session import async_session_factory, init_database
from ame.jobs.queue import JobQueue
from ame.jobs.registry import resolve
from ame.observability import bind_job_context, configure_logging, get_logger

configure_logging()
logger = get_logger("ame.worker")


async def process_one() -> bool:
    async with async_session_factory() as session:
        queue = JobQueue(session, worker_id=os.getenv("HOSTNAME", "worker"))
        job = await queue.lease_next()
        if not job:
            await session.commit()
            return False
        bind_job_context(
            correlation_id=job.correlation_id or str(uuid4()),
            workflow_id=str(job.workflow_id) if job.workflow_id else None,
            content_id=str(job.content_id) if job.content_id else None,
        )
        await queue.mark_running(job)
        await session.commit()
        try:
            handler = resolve(job.name)
            async with async_session_factory() as work_session:
                await handler(work_session, job)
                await work_session.commit()
            async with async_session_factory() as done_session:
                done_queue = JobQueue(done_session)
                from sqlalchemy import select
                from ame.db.models import Job

                fresh = (await done_session.execute(select(Job).where(Job.id == job.id))).scalar_one()
                await done_queue.mark_succeeded(fresh)
                await done_session.commit()
            logger.info("job_succeeded", job_id=str(job.id), name=job.name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job_failed", job_id=str(job.id), name=job.name)
            async with async_session_factory() as fail_session:
                from sqlalchemy import select
                from ame.db.models import Job

                fresh = (await fail_session.execute(select(Job).where(Job.id == job.id))).scalar_one()
                await JobQueue(fail_session).mark_failed(fresh, str(exc))
                await fail_session.commit()
        return True


async def main() -> None:
    init_database()
    settings = get_settings()
    logger.info("worker_started", env=settings.app_env, dry_run=settings.dry_run)
    idle = 0.5
    while True:
        worked = await process_one()
        await asyncio.sleep(0.05 if worked else idle)


if __name__ == "__main__":
    asyncio.run(main())
