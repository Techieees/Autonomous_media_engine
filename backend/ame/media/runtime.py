from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentRunStatus, ContentStatus
from ame.contracts.schemas import AgentContext, AgentInput, AgentResult, ProductionManifest
from ame.db.models import (
    AgentTask,
    BrandConfig,
    ContentItem,
    Job,
    MediaAsset,
    ProductionManifestRecord,
    Script,
)
from ame.contracts.enums import JobName
from ame.jobs.queue import enqueue
from ame.media.errors import RetryableMediaError
from ame.pipeline.advance import idempotency_key

_FOLLOWUP_STAGE = {
    JobName.VOICE_SYNTH.value: "voice",
    JobName.SUBTITLE_BUILD.value: "subs",
    JobName.VIDEO_RENDER.value: "render",
    JobName.QA_CHECK.value: "qa",
}

KIND_VOICEOVER = "voiceover"
KIND_SUBTITLES = "subtitles"
KIND_VIDEO = "video"
KIND_THUMBNAIL = "thumbnail"


def resolve_content_id(job: Job) -> UUID:
    if job.content_id:
        return job.content_id
    raw = (job.payload or {}).get("content_id")
    if not raw:
        raise ValueError("job missing content_id")
    return UUID(str(raw))


async def load_content(session: AsyncSession, job: Job) -> ContentItem:
    content_id = resolve_content_id(job)
    content = await session.get(ContentItem, content_id)
    if content is None:
        raise ValueError(f"content not found: {content_id}")
    return content


async def load_selected_script(session: AsyncSession, content: ContentItem) -> Script:
    script: Script | None = None
    if content.selected_script_id:
        script = await session.get(Script, content.selected_script_id)
    if script is None:
        result = await session.execute(
            select(Script)
            .where(Script.content_id == content.id, Script.selected.is_(True))
            .order_by(Script.created_at.desc())
        )
        script = result.scalar_one_or_none()
    if script is None:
        raise ValueError(f"selected script not found for content {content.id}")
    return script


async def load_active_brand(session: AsyncSession) -> BrandConfig | None:
    result = await session.execute(
        select(BrandConfig)
        .where(BrandConfig.active.is_(True))
        .order_by(BrandConfig.version.desc())
    )
    return result.scalars().first()


async def load_manifest_record(
    session: AsyncSession, content_id: UUID
) -> ProductionManifestRecord | None:
    result = await session.execute(
        select(ProductionManifestRecord)
        .where(ProductionManifestRecord.content_id == content_id)
        .order_by(ProductionManifestRecord.created_at.desc())
    )
    return result.scalars().first()


async def load_manifest(session: AsyncSession, content_id: UUID) -> ProductionManifest:
    record = await load_manifest_record(session, content_id)
    if record is None:
        raise ValueError(f"production manifest not found for content {content_id}")
    return ProductionManifest.model_validate(record.spec)


async def persist_manifest(
    session: AsyncSession,
    content: ContentItem,
    manifest: ProductionManifest,
) -> ProductionManifestRecord:
    validated = ProductionManifest.model_validate(manifest.model_dump())
    spec = validated.model_dump(mode="json")
    record = await load_manifest_record(session, content.id)
    if record is None:
        record = ProductionManifestRecord(
            content_id=content.id,
            template_id=validated.template_id,
            spec=spec,
        )
        session.add(record)
    else:
        record.template_id = validated.template_id
        record.spec = spec
    await session.flush()
    return record


async def load_asset(session: AsyncSession, content_id: UUID, kind: str) -> MediaAsset | None:
    result = await session.execute(
        select(MediaAsset)
        .where(MediaAsset.content_id == content_id, MediaAsset.kind == kind)
        .order_by(MediaAsset.created_at.desc())
    )
    return result.scalars().first()


async def upsert_asset(
    session: AsyncSession,
    *,
    content_id: UUID,
    kind: str,
    storage_key: str,
    mime_type: str,
    sha256: str,
    metadata: dict[str, Any],
    source: str = "generated",
) -> MediaAsset:
    asset = await load_asset(session, content_id, kind)
    if asset is None:
        asset = MediaAsset(
            content_id=content_id,
            kind=kind,
            storage_key=storage_key,
            mime_type=mime_type,
            sha256=sha256,
            source=source,
            source_url=None,
            license="original-ame",
            usage_type="original",
            metadata_json=metadata,
        )
        session.add(asset)
    else:
        asset.storage_key = storage_key
        asset.mime_type = mime_type
        asset.sha256 = sha256
        asset.source = source
        asset.license = "original-ame"
        asset.usage_type = "original"
        asset.metadata_json = metadata
    await session.flush()
    return asset


async def enqueue_followup(
    session: AsyncSession,
    job: Job,
    content: ContentItem,
    name: str,
) -> None:
    await enqueue(
        session,
        name,
        payload={"content_id": str(content.id)},
        idempotency_key=idempotency_key(_FOLLOWUP_STAGE.get(name, name.split(".", 1)[0]), content.id),
        content_id=content.id,
        workflow_id=job.workflow_id or content.workflow_id,
        correlation_id=job.correlation_id,
    )


async def run_media_agent(session: AsyncSession, job: Job, agent: Agent) -> AgentResult:
    content = await load_content(session, job)
    settings = get_settings()
    try:
        status = ContentStatus(content.status)
    except ValueError:
        status = None
    task = AgentTask(
        agent=agent.name.value,
        status="running",
        payload=dict(job.payload or {}),
        content_id=content.id,
        workflow_id=job.workflow_id or content.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    result = await agent.run(
        AgentInput(
            task_id=task.id,
            agent=agent.name,
            payload=job.payload or {},
            content_id=content.id,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id or content.workflow_id,
        ),
        AgentContext(
            content_id=content.id,
            status=status,
            dry_run=settings.dry_run,
            simulation=content.simulation,
        ),
    )
    task.status = "succeeded" if result.status == AgentRunStatus.SUCCEEDED else "failed"
    await session.flush()
    return result


def require_success(result: AgentResult, label: str) -> AgentResult:
    if result.status != AgentRunStatus.SUCCEEDED:
        raise RuntimeError(result.error or f"{label} failed")
    return result


async def fail_render_job(session: AsyncSession, result: AgentResult) -> None:
    await session.commit()
    raise RetryableMediaError(result.error or "video render failed (retryable)")


def storage_key(content_id: UUID, filename: str) -> str:
    return f"content/{content_id}/{filename}"
