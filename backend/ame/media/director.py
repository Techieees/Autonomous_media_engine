from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus, JobName
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import ContentItem, Job
from ame.media.runtime import (
    enqueue_followup,
    load_active_brand,
    load_content,
    load_selected_script,
    persist_manifest,
    require_success,
    run_media_agent,
)
from ame.media.template import TEMPLATE_ID, build_production_manifest, manifest_duration
from ame.observability import get_logger

logger = get_logger("ame.media.director")


class MediaDirectorAgent(Agent):
    name = AgentName.MEDIA_DIRECTOR

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        if agent_input.content_id is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error="media.plan missing content_id",
            )
        content = await self.session.get(ContentItem, agent_input.content_id)
        if content is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error=f"content not found: {agent_input.content_id}",
            )
        script = await load_selected_script(self.session, content)
        brand = await load_active_brand(self.session)
        visual = brand.visual_identity if brand is not None else None
        from ame.ops.daily_plan import plan_preferences

        prefs = await plan_preferences(self.session)
        target = prefs.get("target_duration_s")
        extra_identity = dict(visual or {})
        if target:
            extra_identity["target_duration_s"] = target
        manifest = build_production_manifest(script, visual_identity=extra_identity)
        record = await persist_manifest(self.session, content, manifest)
        content.status = ContentStatus.PRODUCTION.value
        content.failure_reason = None
        duration = manifest_duration(manifest)
        logger.info(
            "media_planned",
            content_id=str(content.id),
            template_id=TEMPLATE_ID,
            scenes=len(manifest.scenes),
            duration_s=duration,
            dry_run=context.dry_run,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "manifest_id": str(record.id),
                "template_id": manifest.template_id,
                "width": manifest.width,
                "height": manifest.height,
                "fps": manifest.fps,
                "scene_count": len(manifest.scenes),
                "duration_s": duration,
                "script_id": str(script.id),
            },
            decision=AgentDecision(
                decision="production_manifest_created",
                reason=(
                    "Selected script mapped onto vertical_clean_v1 with timed scenes, "
                    "typography, and voiceover windows."
                ),
                evidence={
                    "template_id": manifest.template_id,
                    "scene_count": len(manifest.scenes),
                    "duration_s": duration,
                    "script_id": str(script.id),
                },
                confidence=0.9,
                expected_effect="voice.synth queued",
                related_entity_type="production_manifest",
                related_entity_id=record.id,
            ),
            events=["media.planned"],
        )


async def handle_media_plan(session: AsyncSession, job: Job) -> None:
    result = await run_media_agent(session, job, MediaDirectorAgent(session))
    require_success(result, "media.plan")
    content = await load_content(session, job)
    await enqueue_followup(session, job, content, JobName.VOICE_SYNTH.value)
