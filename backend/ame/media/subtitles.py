from __future__ import annotations

import json
import re
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
from ame.media.runtime import (
    KIND_SUBTITLES,
    enqueue_followup,
    load_content,
    load_manifest,
    persist_manifest,
    require_success,
    run_media_agent,
    storage_key,
    upsert_asset,
)
from ame.media.template import manifest_duration, split_sentences
from ame.observability import get_logger
from ame.storage import get_store

logger = get_logger("ame.media.subtitles")

_WORD = re.compile(r"\S+")


def srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, milli = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milli:03d}"


def _is_emphasized(word: str, index: int, count: int, role: str) -> bool:
    cleaned = word.strip(".,!?;:\"'()[]")
    if not cleaned:
        return False
    if cleaned.isupper() and len(cleaned) > 1:
        return True
    if role in {"hook", "reveal"} and len(cleaned) >= 7:
        return True
    return role == "hook" and index == count - 1


def _distribute(items: list[str], start_s: float, end_s: float) -> list[tuple[str, float, float]]:
    if not items:
        return []
    weights = [max(1, len(item.split())) for item in items]
    total = sum(weights)
    span = max(0.05, end_s - start_s)
    cursor = start_s
    out: list[tuple[str, float, float]] = []
    for index, item in enumerate(items):
        dur = span * (weights[index] / total)
        stop = end_s if index == len(items) - 1 else cursor + dur
        out.append((item, cursor, stop))
        cursor = stop
    return out


def build_caption_track(manifest: ProductionManifest) -> tuple[str, dict[str, Any]]:
    duration = manifest_duration(manifest)
    cues: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for scene in manifest.scenes:
        role = str(scene.get("role") or "body")
        start_s = float(scene.get("start_s") or 0)
        end_s = float(scene.get("end_s") or start_s)
        spoken = str(scene.get("voiceover_text") or scene.get("on_screen_text") or "").strip()
        sentences = split_sentences(spoken) or ([spoken] if spoken else [])
        for text, cue_start, cue_end in _distribute(sentences, start_s, end_s):
            cues.append(
                {
                    "text": text,
                    "start_s": round(cue_start, 3),
                    "end_s": round(cue_end, 3),
                    "role": role,
                }
            )
            tokens = _WORD.findall(text)
            for word_index, (token, word_start, word_end) in enumerate(
                _distribute(tokens, cue_start, cue_end)
            ):
                words.append(
                    {
                        "word": token,
                        "start_ms": int(round(word_start * 1000)),
                        "end_ms": int(round(word_end * 1000)),
                        "emphasis": _is_emphasized(token, word_index, len(tokens), role),
                        "scene": role,
                    }
                )
    lines = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{srt_timestamp(cue['start_s'])} --> {srt_timestamp(cue['end_s'])}")
        lines.append(cue["text"])
        lines.append("")
    emphasis = {
        "duration_ms": int(round(duration * 1000)),
        "cues": cues,
        "words": words,
    }
    return "\n".join(lines).strip() + "\n", emphasis


class SubtitleAgent(Agent):
    name = AgentName.SUBTITLE

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        if agent_input.content_id is None:
            return AgentResult(
                status=AgentRunStatus.FAILED, error="subtitle.build missing content_id"
            )
        content = await self.session.get(ContentItem, agent_input.content_id)
        if content is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error=f"content not found: {agent_input.content_id}",
            )
        manifest = await load_manifest(self.session, content.id)
        srt, emphasis = build_caption_track(manifest)
        store = get_store()
        srt_key = storage_key(content.id, "subtitles.srt")
        emphasis_key = storage_key(content.id, "subtitles_emphasis.json")
        srt_bytes = srt.encode("utf-8")
        emphasis_bytes = json.dumps(emphasis, ensure_ascii=False).encode("utf-8")
        store.put(srt_key, srt_bytes, content_type="application/x-subrip")
        store.put(emphasis_key, emphasis_bytes, content_type="application/json")
        metadata = {
            "format": "srt",
            "cue_count": len(emphasis["cues"]),
            "word_count": len(emphasis["words"]),
            "emphasis_key": emphasis_key,
            "provenance": {"source": "generated"},
        }
        asset = await upsert_asset(
            self.session,
            content_id=content.id,
            kind=KIND_SUBTITLES,
            storage_key=srt_key,
            mime_type="application/x-subrip",
            sha256=store.sha256(srt_bytes),
            metadata=metadata,
            source="generated",
        )
        updated = ProductionManifest.model_validate(
            {**manifest.model_dump(), "subtitle_path": srt_key}
        )
        record = await persist_manifest(self.session, content, updated)
        logger.info(
            "subtitle_generated",
            content_id=str(content.id),
            cues=len(emphasis["cues"]),
            dry_run=context.dry_run,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "asset_id": str(asset.id),
                "storage_key": srt_key,
                "emphasis_key": emphasis_key,
                "cue_count": len(emphasis["cues"]),
                "manifest_id": str(record.id),
            },
            decision=AgentDecision(
                decision="subtitles_generated",
                reason=(
                    "Sentence-level SRT and word-emphasis timings "
                    "derived from the saved manifest."
                ),
                evidence={
                    "cue_count": len(emphasis["cues"]),
                    "word_count": len(emphasis["words"]),
                },
                confidence=0.88,
                expected_effect="video.render queued",
                related_entity_type="media_asset",
                related_entity_id=asset.id,
            ),
            events=["subtitle.generated"],
        )


async def handle_subtitle_build(session: AsyncSession, job: Job) -> None:
    result = await run_media_agent(session, job, SubtitleAgent(session))
    require_success(result, "subtitle.build")
    content = await load_content(session, job)
    await enqueue_followup(session, job, content, JobName.VIDEO_RENDER.value)
