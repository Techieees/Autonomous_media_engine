"""Publishing the same content+platform a second time is a no-op."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ame.contracts.enums import Platform, PublishStatus
from tests.fakes import FakePublicationStore, publish_key

publish_service = pytest.importorskip("ame.publishers.service")
handle_publish = publish_service.handle_publish
publish_idempotency_key = publish_service.publish_idempotency_key


def test_publish_idempotency_key_matches_contract(content_id) -> None:
    assert publish_idempotency_key(content_id, Platform.YOUTUBE) == f"publish:{content_id}:youtube"
    assert publish_idempotency_key(content_id, "dry_run") == f"publish:{content_id}:dry_run"
    assert publish_idempotency_key(content_id, Platform.DRY_RUN) == publish_key(
        content_id, Platform.DRY_RUN
    )


def test_fake_store_second_publish_is_reuse(content_id) -> None:
    store = FakePublicationStore()
    first = store.publish(content_id, Platform.YOUTUBE)
    second = store.publish(content_id, Platform.YOUTUBE)
    assert first is second
    assert second.reused is True
    assert store.publish_calls == 1
    other = store.publish(content_id, Platform.TIKTOK)
    assert other is not first
    assert store.publish_calls == 2


@pytest.mark.asyncio
async def test_handle_publish_reuses_terminal_publication(monkeypatch, content_id) -> None:
    from ame.publishers import service as pub

    existing = SimpleNamespace(
        id=uuid4(),
        status=PublishStatus.PUBLISHED.value,
        simulation=True,
    )
    publishing_job = SimpleNamespace(status=PublishStatus.QUEUED.value, error="stale")
    content = SimpleNamespace(
        id=content_id,
        status="approved",
        simulation=True,
        topic="Humanoid robotics",
        workflow_id=uuid4(),
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=content)
    emit = AsyncMock()
    adapter_publish = AsyncMock()

    monkeypatch.setattr(pub, "_get_or_create_publishing_job", AsyncMock(return_value=publishing_job))
    monkeypatch.setattr(pub, "_get_publication", AsyncMock(return_value=existing))
    monkeypatch.setattr(pub, "_emit", emit)
    monkeypatch.setattr(pub, "get_adapter", lambda *args, **kwargs: SimpleNamespace(publish=adapter_publish))

    job = SimpleNamespace(
        id=uuid4(),
        payload={"content_id": str(content_id), "platform": Platform.DRY_RUN.value},
        content_id=content_id,
        correlation_id="corr-1",
        workflow_id=content.workflow_id,
    )

    await handle_publish(session, job)

    assert publishing_job.status == PublishStatus.PUBLISHED.value
    assert publishing_job.error is None
    emit.assert_awaited()
    adapter_publish.assert_not_awaited()
