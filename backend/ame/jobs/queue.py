from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import JobStatus
from ame.db.dialect import is_sqlite, upsert_insert
from ame.db.models import Job


class JobQueue:
    def __init__(self, session: AsyncSession, worker_id: str | None = None) -> None:
        self.session = session
        self.worker_id = worker_id or socket.gethostname()
        self.settings = get_settings()

    async def enqueue(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        content_id: UUID | None = None,
        workflow_id: UUID | None = None,
        correlation_id: str | None = None,
        run_after: datetime | None = None,
        max_attempts: int | None = None,
    ) -> Job:
        key = idempotency_key or f"{name}:{uuid4()}"
        now = datetime.now(UTC)
        stmt = (
            upsert_insert(Job, self.session)
            .values(
                id=uuid4(),
                name=name,
                status=JobStatus.QUEUED.value,
                payload=payload or {},
                idempotency_key=key,
                attempts=0,
                max_attempts=max_attempts or self.settings.job_max_attempts,
                run_after=run_after or now,
                correlation_id=correlation_id,
                workflow_id=workflow_id,
                content_id=content_id,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Job.id)
        )
        result = await self.session.execute(stmt)
        job_id = result.scalar_one_or_none()
        if job_id is None:
            existing = await self.session.execute(select(Job).where(Job.idempotency_key == key))
            return existing.scalar_one()
        loaded = await self.session.execute(select(Job).where(Job.id == job_id))
        return loaded.scalar_one()

    async def lease_next(self) -> Job | None:
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=self.settings.job_lease_seconds)
        candidate_stmt: Select[tuple[UUID]] = (
            select(Job.id)
            .where(
                or_(
                    and_(Job.status == JobStatus.QUEUED.value, Job.run_after <= now),
                    and_(Job.status == JobStatus.RETRY_WAIT.value, Job.run_after <= now),
                    and_(Job.status == JobStatus.LEASED.value, Job.leased_until < now),
                    and_(Job.status == JobStatus.RUNNING.value, Job.leased_until < now),
                )
            )
            .order_by(Job.run_after.asc())
            .limit(1)
        )
        if not is_sqlite(self.session):
            candidate_stmt = candidate_stmt.with_for_update(skip_locked=True)
        result = await self.session.execute(candidate_stmt)
        job_id = result.scalar_one_or_none()
        if not job_id:
            return None
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.LEASED.value,
                leased_until=lease_until,
                leased_by=self.worker_id,
            )
        )
        loaded = await self.session.execute(select(Job).where(Job.id == job_id))
        return loaded.scalar_one()

    async def mark_running(self, job: Job) -> None:
        job.status = JobStatus.RUNNING.value
        job.attempts += 1
        await self.session.flush()

    async def mark_succeeded(self, job: Job) -> None:
        job.status = JobStatus.SUCCEEDED.value
        job.last_error = None
        await self.session.flush()

    async def mark_failed(self, job: Job, error: str) -> None:
        job.last_error = error[:4000]
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.DEAD.value
            job.dead_letter = True
        else:
            delay = min(300, 2 ** job.attempts)
            job.status = JobStatus.RETRY_WAIT.value
            job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
        await self.session.flush()

    async def recover_stuck(self) -> int:
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(Job)
            .where(
                Job.status.in_([JobStatus.LEASED.value, JobStatus.RUNNING.value]),
                Job.leased_until < now,
            )
            .values(status=JobStatus.RETRY_WAIT.value, run_after=now)
        )
        return result.rowcount or 0


async def enqueue(session: AsyncSession, *args: Any, **kwargs: Any) -> Job:
    return await JobQueue(session).enqueue(*args, **kwargs)
