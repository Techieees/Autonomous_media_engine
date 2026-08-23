from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus, JobName
from ame.contracts.schemas import (
    AgentContext,
    AgentDecision,
    AgentInput,
    AgentResult,
    ScriptCritique,
)
from ame.db.models import AgentTask, BrandConfig, ContentItem, Job, ResearchPack, Script
from ame.jobs.queue import enqueue
from ame.observability import get_logger
from ame.pipeline.advance import idempotency_key

logger = get_logger("ame.agent.script_critic")

SELECT_FLOOR = 0.45
_POLICY_MARKERS = (
    "watermark",
    "drm",
    "fake engagement",
    "guaranteed views",
    "buy followers",
    "copyrighted clip",
)


class ScriptCriticAgent(Agent):
    """Independent reviewer. Never generates scripts and never imports the writer."""

    name = AgentName.SCRIPT_CRITIC

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        content = await _require_content(self.session, agent_input.content_id)
        scripts = await _load_scripts(self.session, content.id)
        if not scripts:
            raise ValueError("script critic has no persisted candidates to review")

        if content.selected_script_id and any(row.selected for row in scripts):
            return AgentResult(
                status=AgentRunStatus.SKIPPED,
                output={
                    "selected_script_id": str(content.selected_script_id),
                    "reused": True,
                    "independent": True,
                },
                decision=AgentDecision(
                    decision="reuse_selection",
                    reason="A prior independent critique already selected a winner.",
                    evidence={"selected_script_id": str(content.selected_script_id)},
                    confidence=0.9,
                    related_entity_type="script",
                    related_entity_id=content.selected_script_id,
                ),
            )

        pack = await _latest_pack(self.session, content.id)
        brand = await _active_brand(self.session)
        critiques = [
            _score_script(script, scripts, pack, brand) for script in scripts
        ]
        by_id = {item.script_id: item for item in critiques}
        for script in scripts:
            critique = by_id[script.id]
            script.critique = critique.model_dump(mode="json")
            script.selected = False

        eligible = [item for item in critiques if item.total >= SELECT_FLOOR]
        if not eligible:
            content.status = ContentStatus.REJECTED.value
            content.selected_script_id = None
            content.failure_reason = "all_scripts_below_critique_floor"
            await self.session.flush()
            return AgentResult(
                status=AgentRunStatus.SUCCEEDED,
                output={
                    "selected": False,
                    "critiques": [item.model_dump(mode="json") for item in critiques],
                    "floor": SELECT_FLOOR,
                    "independent": True,
                    "writer_scores_ignored": True,
                },
                decision=AgentDecision(
                    decision="reject_all_scripts",
                    reason="Independent rubric scored every candidate below 0.45.",
                    evidence={
                        "totals": {str(item.script_id): item.total for item in critiques},
                        "reviewer": self.name.value,
                    },
                    confidence=0.85,
                    expected_effect="halt_before_media_plan",
                    related_entity_type="content_item",
                    related_entity_id=content.id,
                ),
                events=["script.rejected"],
            )

        winner = max(eligible, key=lambda item: (item.total, item.factual_confidence, item.hook))
        winner.selected = True
        for script in scripts:
            script.selected = script.id == winner.script_id
            script.critique = by_id[script.id].model_dump(mode="json")
        content.selected_script_id = winner.script_id
        content.status = ContentStatus.SCRIPT_SELECTED.value
        content.failure_reason = None
        await enqueue(
            self.session,
            JobName.MEDIA_PLAN.value,
            payload={
                "content_id": str(content.id),
                "script_id": str(winner.script_id),
            },
            idempotency_key=idempotency_key("media", content.id),
            content_id=content.id,
            workflow_id=content.workflow_id,
            correlation_id=agent_input.correlation_id,
        )
        await self.session.flush()
        logger.info(
            "script_selected",
            content_id=str(content.id),
            script_id=str(winner.script_id),
            total=winner.total,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "selected": True,
                "selected_script_id": str(winner.script_id),
                "total": winner.total,
                "critiques": [item.model_dump(mode="json") for item in critiques],
                "independent": True,
                "writer_scores_ignored": True,
            },
            decision=AgentDecision(
                decision="select_script",
                reason="Independent critic selected the highest scoring candidate above the floor.",
                evidence={
                    "selected_script_id": str(winner.script_id),
                    "total": winner.total,
                    "reviewer": self.name.value,
                    "writer_cannot_self_review": True,
                },
                confidence=winner.total,
                expected_effect="start_media_plan",
                related_entity_type="script",
                related_entity_id=winner.script_id,
            ),
            events=["script.selected"],
        )


async def handle_script_critique(session: AsyncSession, job: Job) -> None:
    agent_input, context, task = await _start(session, job, AgentName.SCRIPT_CRITIC)
    result = await ScriptCriticAgent(session).run(agent_input, context)
    task.status = _task_status(result)
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "script_critique_failed")


async def _start(
    session: AsyncSession, job: Job, agent: AgentName
) -> tuple[AgentInput, AgentContext, AgentTask]:
    payload = dict(job.payload or {})
    content_id = job.content_id
    raw = payload.get("content_id")
    if content_id is None and raw:
        content_id = UUID(str(raw))
    task = AgentTask(
        agent=agent.value,
        status="running",
        payload=payload,
        content_id=content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    content = await session.get(ContentItem, content_id) if content_id else None
    status: ContentStatus | None = None
    if content is not None:
        try:
            status = ContentStatus(content.status)
        except ValueError:
            status = None
    settings = get_settings()
    return (
        AgentInput(
            task_id=task.id,
            agent=agent,
            payload=payload,
            content_id=content_id,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id or (content.workflow_id if content else None),
        ),
        AgentContext(
            content_id=content_id,
            status=status,
            dry_run=settings.dry_run,
            simulation=bool(content.simulation) if content is not None else True,
            extra=payload,
        ),
        task,
    )


def _task_status(result: AgentResult) -> str:
    if result.status in {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.SKIPPED,
        AgentRunStatus.BUDGET_BLOCKED,
    }:
        return "succeeded"
    return "failed"


async def _require_content(session: AsyncSession, content_id: UUID | None) -> ContentItem:
    if content_id is None:
        raise ValueError("script critic requires content_id")
    content = await session.get(ContentItem, content_id)
    if content is None:
        raise ValueError(f"content_item not found: {content_id}")
    return content


async def _load_scripts(session: AsyncSession, content_id: UUID) -> list[Script]:
    result = await session.execute(
        select(Script)
        .where(Script.content_id == content_id)
        .order_by(Script.candidate_label.asc())
    )
    return list(result.scalars().all())


async def _latest_pack(session: AsyncSession, content_id: UUID) -> ResearchPack | None:
    result = await session.execute(
        select(ResearchPack)
        .where(ResearchPack.content_id == content_id)
        .order_by(ResearchPack.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _active_brand(session: AsyncSession) -> BrandConfig | None:
    result = await session.execute(
        select(BrandConfig)
        .where(BrandConfig.active.is_(True))
        .order_by(BrandConfig.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _claim_text(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("claim") or "").strip().lower()
    return str(getattr(raw, "claim", "")).strip().lower()


def _score_script(
    script: Script,
    cohort: list[Script],
    pack: ResearchPack | None,
    brand: BrandConfig | None,
) -> ScriptCritique:
    hook = script.hook or ""
    body = script.body or ""
    others = [row for row in cohort if row.id != script.id]

    hook_score = 0.15
    if 12 <= len(hook) <= 140:
        hook_score += 0.4
    if hook.endswith("?") or any(
        token in hook.lower() for token in ("why", "how", "overlooked", "constraint", "signals")
    ):
        hook_score += 0.25
    if hook[:1].isupper():
        hook_score += 0.15

    clarity = 0.25
    if 40 <= len(body) <= 900:
        clarity += 0.4
    if script.reveal:
        clarity += 0.15
    if script.on_screen_text:
        clarity += 0.15

    same_hash = sum(
        1 for row in others if row.normalized_hash and row.normalized_hash == script.normalized_hash
    )
    originality = 0.2 if same_hash else 0.86

    duration = script.estimated_duration or 0
    if 20 <= duration <= 55:
        retention = 0.78
    elif 15 <= duration <= 70:
        retention = 0.52
    else:
        retention = 0.28
    if script.reveal:
        retention += 0.12

    claims = list(script.claims or [])
    pack_claims = {_claim_text(item) for item in (pack.claims if pack else [])}
    if not claims:
        factual = 0.32
    else:
        sourced = 0
        verified_ok = True
        invented = 0
        for raw in claims:
            sources = raw.get("sources") if isinstance(raw, dict) else []
            kind = raw.get("kind") if isinstance(raw, dict) else None
            if sources:
                sourced += 1
            if kind == "verified_fact" and not sources:
                verified_ok = False
            if pack_claims and _claim_text(raw) not in pack_claims:
                invented += 1
        factual = sourced / max(len(claims), 1)
        if not verified_ok:
            factual *= 0.4
        if invented:
            factual *= 0.45

    platform = 0.35
    if 15 <= duration <= 90:
        platform += 0.3
    if script.caption:
        platform += 0.15
    if script.hashtags:
        platform += 0.15

    feasibility = 0.3
    if script.scene_plan:
        feasibility += 0.4
    if duration <= 90:
        feasibility += 0.25

    brand_fit = 0.58
    if brand is not None:
        brand_fit += 0.15
        tone = (brand.tone or "").lower()
        voice = (script.voice_style or "").lower()
        if tone and (tone in voice or tone in body.lower()):
            brand_fit += 0.15

    blob = f"{hook} {body} {script.cta} {script.caption}".lower()
    policy_risk = 0.05
    for marker in _POLICY_MARKERS:
        if marker in blob:
            policy_risk += 0.3

    repetition = 0.06
    hook_norm = hook.lower().strip()
    for row in others:
        if script.normalized_hash and script.normalized_hash == row.normalized_hash:
            repetition = max(repetition, 0.85)
        elif hook_norm and hook_norm == (row.hook or "").lower().strip():
            repetition = max(repetition, 0.7)

    hook_score = _clip(hook_score)
    clarity = _clip(clarity)
    originality = _clip(originality)
    retention = _clip(retention)
    factual = _clip(factual)
    platform = _clip(platform)
    feasibility = _clip(feasibility)
    brand_fit = _clip(brand_fit)
    policy_risk = _clip(policy_risk)
    repetition = _clip(repetition)
    total = _clip(
        0.14 * hook_score
        + 0.12 * clarity
        + 0.12 * originality
        + 0.14 * retention
        + 0.14 * factual
        + 0.08 * platform
        + 0.08 * feasibility
        + 0.08 * brand_fit
        + 0.06 * (1.0 - policy_risk)
        + 0.04 * (1.0 - repetition)
    )
    notes = (
        f"Independent rubric on candidate {script.candidate_label}: "
        f"factual={factual:.2f} originality={originality:.2f} retention={retention:.2f}."
    )
    return ScriptCritique(
        script_id=script.id,
        hook=hook_score,
        clarity=clarity,
        originality=originality,
        retention_potential=retention,
        factual_confidence=factual,
        platform_suitability=platform,
        production_feasibility=feasibility,
        brand_fit=brand_fit,
        policy_risk=policy_risk,
        repetition=repetition,
        total=total,
        selected=False,
        notes=notes,
    )
