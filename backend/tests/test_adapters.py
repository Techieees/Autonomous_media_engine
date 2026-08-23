"""Publisher adapters: dry-run labels simulation; production refuses it."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("httpx")

from ame.contracts.enums import Platform, PublishStatus
from ame.publishers.base import PreparedPublish
from ame.publishers.dry_run import DryRunPublisher
from ame.publishers.instagram import InstagramPublisher
from ame.publishers.tiktok import TikTokPublisher
from ame.publishers.youtube import YouTubePublisher


def _content(*, simulation: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        topic="Humanoid robotics",
        simulation=simulation,
        privacy_status="unlisted",
    )


def _asset() -> SimpleNamespace:
    return SimpleNamespace(storage_key="content/demo/video.mp4")


def _prepared(platform: Platform, *, simulation: bool) -> PreparedPublish:
    return PreparedPublish(
        content_id=uuid4(),
        platform=platform,
        title="Humanoid robotics",
        description="Dry-run fixture",
        media_key="content/demo/video.mp4",
        simulation=simulation,
    )


@pytest.mark.asyncio
async def test_dry_run_publisher_sets_simulation_true() -> None:
    publisher = DryRunPublisher()
    assert publisher.platform is Platform.DRY_RUN
    content = _content(simulation=True)
    prepared = await publisher.prepare(content, _asset())
    assert prepared.simulation is True
    assert prepared.platform is Platform.DRY_RUN

    result = await publisher.publish(
        prepared, idempotency_key=f"publish:{content.id}:dry_run"
    )
    assert result.simulation is True
    assert result.status is PublishStatus.PUBLISHED
    assert result.external_id and result.external_id.startswith("sim-")
    assert result.url and result.url.startswith("ame://simulation/")
    assert result.raw.get("real_platform_post") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publisher_cls, platform",
    (
        (YouTubePublisher, Platform.YOUTUBE),
        (InstagramPublisher, Platform.INSTAGRAM),
        (TikTokPublisher, Platform.TIKTOK),
    ),
)
async def test_production_publisher_refuses_simulation(publisher_cls, platform) -> None:
    publisher = publisher_cls()
    assert publisher.platform is platform
    content = _content(simulation=True)
    validation = await publisher.validate(content, None)
    assert validation.ok is False
    assert validation.status is PublishStatus.REJECTED_SIMULATION
    assert "simulation" in " ".join(validation.reasons)

    published = await publisher.publish(
        _prepared(platform, simulation=True),
        idempotency_key=f"publish:{content.id}:{platform.value}",
    )
    assert published.status is PublishStatus.REJECTED_SIMULATION
    assert published.simulation is True
    assert published.error == "production_publisher_refuses_simulation"
