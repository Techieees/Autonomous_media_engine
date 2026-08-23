"""Job queue idempotency and dead-letter behaviour."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ame.contracts.enums import JobStatus
from ame.jobs.queue import JobQueue
from tests.fakes import FakeJobStore, job_key, publish_key


def test_idempotency_key_formats_are_stable(content_id) -> None:
    assert publish_key(content_id, "youtube") == f"publish:{content_id}:youtube"
    assert publish_key(content_id, "dry_run") == f"publish:{content_id}:dry_run"
    named = job_key("research.run", content_id)
    assert named == f"research.run:{content_id}"
    generated = job_key("director.tick")
    assert generated.startswith("director.tick:")
    assert generated != job_key("director.tick")


def test_fake_store_same_key_yields_one_job(content_id) -> None:
    store = FakeJobStore()
    key = publish_key(content_id, "youtube")
    first = store.enqueue("publish.run", {"content_id": str(content_id)}, idempotency_key=key)
    second = store.enqueue(
        "publish.run",
        {"content_id": str(content_id), "retry": True},
        idempotency_key=key,
    )
    assert first is second
    assert len(store.by_key) == 1
    assert first.payload["content_id"] == str(content_id)
    other = store.enqueue("publish.run", {}, idempotency_key=publish_key(content_id, "tiktok"))
    assert other is not first
    assert len(store.by_key) == 2


@pytest.mark.asyncio
async def test_enqueue_conflict_path_returns_existing_row() -> None:
    existing = SimpleNamespace(
        id=uuid4(),
        name="publish.run",
        idempotency_key="publish:same:youtube",
        status=JobStatus.QUEUED.value,
    )
    conflict = MagicMock()
    conflict.scalar_one_or_none.return_value = None
    loaded = MagicMock()
    loaded.scalar_one.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[conflict, loaded])

    queue = JobQueue(session, worker_id="test-worker")
    job = await queue.enqueue("publish.run", {"n": 1}, idempotency_key="publish:same:youtube")

    assert job is existing
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_enqueue_insert_path_loads_new_row() -> None:
    new_id = uuid4()
    created = SimpleNamespace(id=new_id, idempotency_key="k", name="trend.ingest")
    inserted = MagicMock()
    inserted.scalar_one_or_none.return_value = new_id
    loaded = MagicMock()
    loaded.scalar_one.return_value = created
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[inserted, loaded])

    queue = JobQueue(session)
    job = await queue.enqueue("trend.ingest", {}, idempotency_key="k")
    assert job is created
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_mark_failed_dead_letters_after_max_attempts() -> None:
    session = AsyncMock()
    job = SimpleNamespace(
        attempts=5,
        max_attempts=5,
        last_error=None,
        status=JobStatus.RUNNING.value,
        dead_letter=False,
        run_after=None,
    )
    queue = JobQueue(session)
    await queue.mark_failed(job, "boom " * 2000)

    assert job.status == JobStatus.DEAD.value
    assert job.dead_letter is True
    assert job.last_error is not None
    assert len(job.last_error) == 4000
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_mark_failed_retries_before_max_attempts() -> None:
    session = AsyncMock()
    job = SimpleNamespace(
        attempts=2,
        max_attempts=5,
        last_error=None,
        status=JobStatus.RUNNING.value,
        dead_letter=False,
        run_after=None,
    )
    queue = JobQueue(session)
    await queue.mark_failed(job, "transient ffmpeg")

    assert job.status == JobStatus.RETRY_WAIT.value
    assert job.dead_letter is False
    assert job.run_after is not None
    assert job.last_error == "transient ffmpeg"
