from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, JobName
from ame.contracts.schemas import (
    AgentContext,
    AgentDecision,
    AgentInput,
    AgentResult,
    ProductionManifest,
)
from ame.costs.tracker import assert_budget, record_cost
from ame.db.models import ContentItem, Job
from ame.media.runtime import (
    KIND_VOICEOVER,
    enqueue_followup,
    load_content,
    load_manifest,
    load_selected_script,
    persist_manifest,
    require_success,
    run_media_agent,
    storage_key,
    upsert_asset,
)
from ame.media.template import manifest_duration
from ame.media.wavutil import fit_wav_duration, is_valid_wav, write_tone_wav
from ame.observability import get_logger
from ame.storage import get_store

logger = get_logger("ame.media.voice")


@dataclass(frozen=True)
class TTSResult:
    provider: str
    voice: str
    settings: dict[str, Any]
    duration_s: float


class TTSProvider(ABC):
    name: str

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        dest: Path,
        *,
        duration_s: float,
        voice: str,
        settings: dict[str, Any],
    ) -> TTSResult:
        raise NotImplementedError


def find_espeak() -> str | None:
    for name in ("espeak-ng", "espeak", "espeak-ng.exe", "espeak.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _voice_script(script: Any) -> str:
    parts = [
        getattr(script, "hook", "") or "",
        getattr(script, "body", "") or "",
        getattr(script, "reveal", "") or "",
        getattr(script, "cta", "") or "",
    ]
    return " ".join(part.strip() for part in parts if part and str(part).strip())


def _espeak_wpm(text: str, duration_s: float) -> int:
    words = max(1, len(text.split()))
    raw = words / max(duration_s, 0.5) * 60.0
    return max(80, min(220, int(raw)))


class DevTTSProvider(TTSProvider):
    name = "dev"

    async def synthesize(
        self,
        text: str,
        dest: Path,
        *,
        duration_s: float,
        voice: str,
        settings: dict[str, Any],
    ) -> TTSResult:
        dest.parent.mkdir(parents=True, exist_ok=True)
        binary = find_espeak()
        used = "tone"
        resolved_voice = voice if voice and voice != "default" else "en"
        wpm = _espeak_wpm(text, duration_s)
        if binary:
            try:
                completed = subprocess.run(
                    [binary, "-w", str(dest), "-s", str(wpm), "-v", resolved_voice],
                    input=text,
                    text=True,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                if completed.returncode == 0 and dest.is_file() and is_valid_wav(dest):
                    used = Path(binary).stem
                else:
                    logger.info("espeak_unavailable_falling_back_to_tone")
                    write_tone_wav(dest, duration_s)
            except (OSError, subprocess.TimeoutExpired):
                write_tone_wav(dest, duration_s)
        else:
            write_tone_wav(dest, duration_s)
        actual = fit_wav_duration(dest, duration_s)
        return TTSResult(
            provider=used,
            voice=resolved_voice,
            settings={**settings, "wpm": wpm, "backend": used, "espeak_binary": bool(binary)},
            duration_s=actual,
        )


class OpenAITTSProvider(TTSProvider):
    name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.tts_api_key:
            raise RuntimeError("TTS_API_KEY required for OpenAI TTS")
        self.api_key = settings.tts_api_key
        self.model = "tts-1"

    async def synthesize(
        self,
        text: str,
        dest: Path,
        *,
        duration_s: float,
        voice: str,
        settings: dict[str, Any],
    ) -> TTSResult:
        dest.parent.mkdir(parents=True, exist_ok=True)
        resolved_voice = voice if voice and voice != "default" else "alloy"
        payload = {
            "model": self.model,
            "input": text[:4096],
            "voice": resolved_voice,
            "response_format": "wav",
        }
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            dest.write_bytes(response.content)
        if not is_valid_wav(dest):
            raise RuntimeError("openai tts returned a non-wav payload")
        actual = fit_wav_duration(dest, duration_s)
        return TTSResult(
            provider=self.name,
            voice=resolved_voice,
            settings={**settings, "model": self.model, "response_format": "wav"},
            duration_s=actual,
        )


def get_tts_provider() -> TTSProvider:
    settings = get_settings()
    if settings.tts_api_key and settings.tts_provider in {"openai", "compatible"}:
        return OpenAITTSProvider()
    return DevTTSProvider()


async def _synthesize_with_fallback(
    session: AsyncSession,
    text: str,
    dest: Path,
    *,
    duration_s: float,
    voice: str,
    settings: dict[str, Any],
    content_id: UUID,
) -> TTSResult:
    provider = get_tts_provider()
    if isinstance(provider, OpenAITTSProvider):
        estimated = len(text) * 15.0 / 1_000_000.0
        try:
            await assert_budget(session, kind="media", extra=estimated)
            result = await provider.synthesize(
                text, dest, duration_s=duration_s, voice=voice, settings=settings
            )
            await record_cost(
                session,
                provider.name,
                "tts-1",
                0,
                0,
                estimated,
                job=JobName.VOICE_SYNTH.value,
                agent=AgentName.VOICE.value,
                content_id=content_id,
                kind="media",
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.info("openai_tts_fallback_dev", error_type=type(exc).__name__)
    return await DevTTSProvider().synthesize(
        text, dest, duration_s=duration_s, voice=voice, settings=settings
    )


class VoiceAgent(Agent):
    name = AgentName.VOICE

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        if agent_input.content_id is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error="voice.synth missing content_id",
            )
        content = await self.session.get(ContentItem, agent_input.content_id)
        if content is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error=f"content not found: {agent_input.content_id}",
            )
        script = await load_selected_script(self.session, content)
        manifest = await load_manifest(self.session, content.id)
        duration = manifest_duration(manifest)
        text = _voice_script(script)
        if not text:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error="selected script has no spoken text",
            )
        settings = get_settings()
        key = storage_key(content.id, "voiceover.wav")
        store = get_store()
        dest = store.local_path(key)
        tts = await _synthesize_with_fallback(
            self.session,
            text,
            dest,
            duration_s=duration,
            voice=settings.tts_voice,
            settings={
                "voice_style": script.voice_style,
                "estimated_duration": duration,
                "sample_format": "pcm_s16le",
            },
            content_id=content.id,
        )
        data = dest.read_bytes()
        store.put(key, data, content_type="audio/wav")
        metadata = {
            "provider": tts.provider,
            "voice": tts.voice,
            "settings": tts.settings,
            "duration_s": tts.duration_s,
            "provenance": {"source": "generated"},
        }
        asset = await upsert_asset(
            self.session,
            content_id=content.id,
            kind=KIND_VOICEOVER,
            storage_key=key,
            mime_type="audio/wav",
            sha256=store.sha256(data),
            metadata=metadata,
            source="generated",
        )
        updated = ProductionManifest.model_validate(
            {**manifest.model_dump(), "voiceover_path": key}
        )
        record = await persist_manifest(self.session, content, updated)
        logger.info(
            "voice_generated",
            content_id=str(content.id),
            provider=tts.provider,
            duration_s=tts.duration_s,
            dry_run=context.dry_run,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "asset_id": str(asset.id),
                "storage_key": key,
                "provider": tts.provider,
                "voice": tts.voice,
                "duration_s": tts.duration_s,
                "manifest_id": str(record.id),
            },
            decision=AgentDecision(
                decision="voiceover_generated",
                reason=(
                    f"Voiceover synthesized with {tts.provider} "
                    "and stored as a generated asset."
                ),
                evidence={
                    "provider": tts.provider,
                    "voice": tts.voice,
                    "duration_s": tts.duration_s,
                },
                confidence=0.85,
                expected_effect="subtitle.build queued",
                related_entity_type="media_asset",
                related_entity_id=asset.id,
            ),
            events=["voice.generated"],
        )


async def handle_voice_synth(session: AsyncSession, job: Job) -> None:
    result = await run_media_agent(session, job, VoiceAgent(session))
    require_success(result, "voice.synth")
    content = await load_content(session, job)
    await enqueue_followup(session, job, content, JobName.SUBTITLE_BUILD.value)
