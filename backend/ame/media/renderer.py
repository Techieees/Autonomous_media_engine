from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.contracts.enums import AgentName, AgentRunStatus, JobName
from ame.contracts.schemas import (
    AgentContext,
    AgentDecision,
    AgentInput,
    AgentResult,
    ProductionManifest,
)
from ame.db.models import ContentItem, Job
from ame.media.errors import RetryableMediaError
from ame.media.ffmpeg import (
    build_vertical_filtergraph,
    compose_vertical_mp4,
    extract_thumbnail,
    require_ffmpeg,
    resolve_font,
)
from ame.media.runtime import (
    KIND_SUBTITLES,
    KIND_THUMBNAIL,
    KIND_VIDEO,
    KIND_VOICEOVER,
    enqueue_followup,
    fail_render_job,
    load_asset,
    load_content,
    load_manifest,
    persist_manifest,
    run_media_agent,
    storage_key,
    upsert_asset,
)
from ame.media.subtitles import build_caption_track
from ame.media.template import default_template, manifest_duration
from ame.observability import get_logger
from ame.storage import get_store

logger = get_logger("ame.media.renderer")


def _template_from_manifest(manifest: ProductionManifest) -> dict[str, Any]:
    base = default_template()
    for item in manifest.assets:
        if isinstance(item, dict) and item.get("kind") == "template":
            colors = dict(base["colors"])
            if isinstance(item.get("colors"), dict):
                colors.update(item["colors"])
            margins = dict(base["safe_margins"])
            if isinstance(item.get("safe_margins"), dict):
                margins.update({key: int(value) for key, value in item["safe_margins"].items()})
            base = {
                **base,
                **{key: value for key, value in item.items() if key != "kind"},
                "colors": colors,
                "safe_margins": margins,
            }
    return base


def _resolve_media_path(store, manifest_path: str | None, kind: str, session_asset):
    key = manifest_path or (session_asset.storage_key if session_asset is not None else None)
    if not key:
        raise RetryableMediaError(f"missing {kind} asset for render (retryable)")
    path = store.local_path(key)
    if not path.is_file():
        raise RetryableMediaError(f"{kind} file missing at storage key {key} (retryable)")
    return path


def _load_cues(store, manifest: ProductionManifest, subtitle_asset) -> list[dict[str, Any]]:
    emphasis_key = None
    if subtitle_asset is not None:
        emphasis_key = (subtitle_asset.metadata_json or {}).get("emphasis_key")
    if emphasis_key and store.exists(str(emphasis_key)):
        payload = json.loads(store.get(str(emphasis_key)).decode("utf-8"))
        cues = payload.get("cues") if isinstance(payload, dict) else None
        if isinstance(cues, list):
            return [cue for cue in cues if isinstance(cue, dict)]
    _, emphasis = build_caption_track(manifest)
    return list(emphasis.get("cues") or [])


class VideoFactoryAgent(Agent):
    name = AgentName.VIDEO_FACTORY

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        if agent_input.content_id is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error="video.render missing content_id",
            )
        content = await self.session.get(ContentItem, agent_input.content_id)
        if content is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error=f"content not found: {agent_input.content_id}",
                events=["render.failed"],
            )
        try:
            require_ffmpeg()
            manifest = await load_manifest(self.session, content.id)
            store = get_store()
            voice_asset = await load_asset(self.session, content.id, KIND_VOICEOVER)
            subtitle_asset = await load_asset(self.session, content.id, KIND_SUBTITLES)
            voice_path = _resolve_media_path(
                store, manifest.voiceover_path, KIND_VOICEOVER, voice_asset
            )
            if subtitle_asset is None and not manifest.subtitle_path:
                raise RetryableMediaError("subtitles not ready for render (retryable)")
            if manifest.subtitle_path or subtitle_asset is not None:
                _resolve_media_path(
                    store, manifest.subtitle_path, KIND_SUBTITLES, subtitle_asset
                )
            template = _template_from_manifest(manifest)
            duration = manifest_duration(manifest)
            cues = _load_cues(store, manifest, subtitle_asset)
            work_dir = store.local_path(storage_key(content.id, "work/.keep")).parent
            plan = build_vertical_filtergraph(
                scenes=list(manifest.scenes),
                cues=cues,
                font=resolve_font(),
                work_dir=work_dir,
                duration_s=duration,
                width=manifest.width,
                height=manifest.height,
                fps=manifest.fps,
                background=str(template["colors"]["background"]),
                subtitle_color=str(template["colors"]["subtitle"]),
                subtitle_size=int(template.get("subtitle_size") or 42),
                accent=str(template["colors"]["accent"]),
                margins=template["safe_margins"],
            )
            video_key = storage_key(content.id, "video.mp4")
            thumb_key = storage_key(content.id, "thumbnail.jpg")
            video_path = store.local_path(video_key)
            thumb_path = store.local_path(thumb_key)
            video_path.parent.mkdir(parents=True, exist_ok=True)
            filter_script = work_dir / "filtergraph.txt"
            timeout = max(120, int(duration * 8) + 30)
            compose_vertical_mp4(
                plan,
                voiceover_path=voice_path,
                output_path=video_path,
                filter_script=filter_script,
                timeout=timeout,
            )
            if not video_path.is_file() or video_path.stat().st_size < 1000:
                raise RetryableMediaError("ffmpeg produced an empty video (retryable)")
            thumb_at = min(max(duration * 0.2, 0.4), max(duration - 0.2, 0.0))
            extract_thumbnail(video_path=video_path, output_path=thumb_path, at_s=thumb_at)
            video_bytes = video_path.read_bytes()
            thumb_bytes = thumb_path.read_bytes()
            store.put(video_key, video_bytes, content_type="video/mp4")
            store.put(thumb_key, thumb_bytes, content_type="image/jpeg")
            video_asset = await upsert_asset(
                self.session,
                content_id=content.id,
                kind=KIND_VIDEO,
                storage_key=video_key,
                mime_type="video/mp4",
                sha256=store.sha256(video_bytes),
                metadata={
                    "width": manifest.width,
                    "height": manifest.height,
                    "fps": manifest.fps,
                    "duration_s": duration,
                    "template_id": manifest.template_id,
                    "ffmpeg": True,
                    "provenance": {"source": "generated"},
                },
                source="generated",
            )
            thumb_asset = await upsert_asset(
                self.session,
                content_id=content.id,
                kind=KIND_THUMBNAIL,
                storage_key=thumb_key,
                mime_type="image/jpeg",
                sha256=store.sha256(thumb_bytes),
                metadata={"extracted_at_s": thumb_at, "provenance": {"source": "generated"}},
                source="generated",
            )
            assets = [item for item in manifest.assets if isinstance(item, dict)]
            assets = [
                item
                for item in assets
                if item.get("kind") not in {KIND_VIDEO, KIND_THUMBNAIL}
            ]
            assets.extend(
                [
                    {
                        "kind": KIND_VIDEO,
                        "storage_key": video_key,
                        "asset_id": str(video_asset.id),
                    },
                    {
                        "kind": KIND_THUMBNAIL,
                        "storage_key": thumb_key,
                        "asset_id": str(thumb_asset.id),
                    },
                ]
            )
            voice_key = manifest.voiceover_path or (
                voice_asset.storage_key if voice_asset is not None else None
            )
            updated = ProductionManifest.model_validate(
                {
                    **manifest.model_dump(),
                    "voiceover_path": voice_key,
                    "subtitle_path": manifest.subtitle_path,
                    "thumbnail_path": thumb_key,
                    "assets": assets,
                }
            )
            record = await persist_manifest(self.session, content, updated)
            content.failure_reason = None
            logger.info(
                "render_completed",
                content_id=str(content.id),
                duration_s=duration,
                video_bytes=len(video_bytes),
                dry_run=context.dry_run,
            )
            return AgentResult(
                status=AgentRunStatus.SUCCEEDED,
                output={
                    "video_asset_id": str(video_asset.id),
                    "thumbnail_asset_id": str(thumb_asset.id),
                    "video_key": video_key,
                    "thumbnail_key": thumb_key,
                    "duration_s": duration,
                    "width": manifest.width,
                    "height": manifest.height,
                    "fps": manifest.fps,
                    "manifest_id": str(record.id),
                },
                decision=AgentDecision(
                    decision="vertical_video_rendered",
                    reason="FFmpeg composed a 1080x1920 MP4 from the saved production manifest.",
                    evidence={
                        "template_id": manifest.template_id,
                        "duration_s": duration,
                        "sha256": video_asset.sha256,
                    },
                    confidence=0.92,
                    expected_effect="qa.check queued",
                    related_entity_type="media_asset",
                    related_entity_id=video_asset.id,
                ),
                events=["render.completed"],
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            content.failure_reason = error[:2000]
            logger.info("render_failed", content_id=str(content.id), error_type=type(exc).__name__)
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error=error,
                output={"content_id": str(content.id), "error_type": type(exc).__name__},
                decision=AgentDecision(
                    decision="render_failed",
                    reason=error[:500],
                    evidence={"error_type": type(exc).__name__},
                    confidence=1.0,
                    expected_effect="job retry",
                    related_entity_type="content_item",
                    related_entity_id=content.id,
                ),
                events=["render.failed"],
            )


async def handle_video_render(session: AsyncSession, job: Job) -> None:
    result = await run_media_agent(session, job, VideoFactoryAgent(session))
    if result.status != AgentRunStatus.SUCCEEDED:
        await fail_render_job(session, result)
    content = await load_content(session, job)
    await enqueue_followup(session, job, content, JobName.QA_CHECK.value)


def local_render_hint() -> str:
    return (
        "Install ffmpeg on PATH (Docker image already includes it). "
        "Optional: espeak-ng for spoken dev voiceover, else a timed tone WAV is used. "
        "Run `make dev` and enqueue media.plan / video.render for a content id, "
        "or invoke handle_video_render after voice and subtitle jobs."
    )
