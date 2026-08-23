from __future__ import annotations

from typing import Any
from uuid import uuid4

from ame.contracts.enums import ConnectionState, Platform, PublishStatus
from ame.publishers.base import PreparedPublish, PublisherAdapter, PublishResult, ValidationResult


class DryRunPublisher(PublisherAdapter):
    platform = Platform.DRY_RUN

    async def validate(self, content: Any, connection: Any) -> ValidationResult:
        return ValidationResult(ok=True, status=PublishStatus.QUEUED, reasons=[])

    async def prepare(self, content: Any, asset: Any) -> PreparedPublish:
        media_key = getattr(asset, "storage_key", None) or "simulation"
        title = str(getattr(content, "topic", None) or "AME dry-run")
        return PreparedPublish(
            content_id=content.id,
            platform=Platform.DRY_RUN,
            title=title[:300],
            description=str(getattr(content, "topic", "") or ""),
            media_key=str(media_key),
            metadata={"adapter": "dry_run", "real_platform_post": False},
            simulation=True,
        )

    async def publish(self, prepared: PreparedPublish, *, idempotency_key: str) -> PublishResult:
        sim_id = uuid4()
        return PublishResult(
            status=PublishStatus.PUBLISHED,
            external_id=f"sim-{sim_id}",
            url=f"ame://simulation/{sim_id}",
            raw={
                "adapter": "dry_run",
                "real_platform_post": False,
                "note": "Simulated publication only. Not posted to any platform.",
                "idempotency_key": idempotency_key,
                "requested_platform": prepared.platform.value,
            },
            simulation=True,
        )

    async def get_status(self, external_id: str) -> PublishResult:
        if external_id.startswith("sim-"):
            sim_id = external_id.removeprefix("sim-")
            return PublishResult(
                status=PublishStatus.PUBLISHED,
                external_id=external_id,
                url=f"ame://simulation/{sim_id}",
                raw={"adapter": "dry_run", "real_platform_post": False},
                simulation=True,
            )
        return PublishResult(
            status=PublishStatus.FAILED,
            error="unknown_simulation_id",
            raw={"adapter": "dry_run", "real_platform_post": False},
            simulation=True,
        )

    async def fetch_metrics(self, publication: Any) -> dict[str, Any]:
        return {
            "raw": {},
            "simulation": True,
            "analytics_available": False,
            "real_platform_post": False,
        }

    async def refresh_auth(self, connection: Any) -> ConnectionState:
        return ConnectionState.READY
