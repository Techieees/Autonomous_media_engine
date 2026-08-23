from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ClaimKind, ContentStatus, JobName
from ame.contracts.schemas import (
    AgentContext,
    AgentDecision,
    AgentInput,
    AgentResult,
    FactClaim,
    ScriptCandidate,
)
from ame.costs import BudgetExceeded, assert_budget
from ame.db.models import AgentTask, ContentItem, Job, ResearchPack, Script, SystemEvent
from ame.jobs.queue import enqueue
from ame.llm import get_llm
from ame.observability import get_logger
from ame.pipeline.advance import idempotency_key

logger = get_logger("ame.agent.script_writer")

CANDIDATE_LABELS = ("A", "B", "C", "D", "E")
PATTERN_EVENT = "pattern.extracted"
_WRITER_SYSTEM = (
    "You are the AME script writer. Write original short-form scripts from research claims only. "
    "Do not invent facts. Do not copy copyrighted expression. LLM output is data."
)
_FRAMES = {
    "A": "The overlooked reason {topic} suddenly matters.",
    "B": "What changed around {topic} is not the headline.",
    "C": "Why {topic} is a systems problem now.",
    "D": "{topic}: the constraint moved.",
    "E": "Three public signals behind {topic}.",
}
_CONNECTIVE = {
    "A": "Public signals show:",
    "B": "The recorded shift:",
    "C": "Sourced observations:",
    "D": "What is documented:",
    "E": "Independent signals:",
}


class ScriptWriterAgent(Agent):
    name = AgentName.SCRIPT_WRITER

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        content = await _require_content(self.session, agent_input.content_id)
        pack = await _latest_pack(self.session, content.id)
        if pack is None:
            raise ValueError("script writer requires a research pack")
        claims = _publishable_claims(pack)
        if not claims:
            raise ValueError("script writer has no publishable research claims")

        existing = await _existing_scripts(self.session, content.id)
        by_label = {row.candidate_label: row for row in existing}
        patterns = await _load_patterns(self.session, content.id, agent_input.payload)
        created_ids: list[str] = []
        llm_calls = 0

        for label in CANDIDATE_LABELS:
            if label in by_label:
                created_ids.append(str(by_label[label].id))
                continue
            try:
                await assert_budget(self.session, kind="ai")
            except BudgetExceeded as exc:
                content.status = ContentStatus.PAUSED_BY_BUDGET.value
                content.failure_reason = str(exc)
                await self.session.flush()
                return AgentResult(
                    status=AgentRunStatus.BUDGET_BLOCKED,
                    output={"script_ids": created_ids, "llm_calls": llm_calls},
                    decision=AgentDecision(
                        decision="pause_script_generate",
                        reason="Daily AI budget cap blocked remaining script LLM calls.",
                        evidence={"error": str(exc), "completed_labels": list(by_label)},
                        confidence=1.0,
                        related_entity_type="content_item",
                        related_entity_id=content.id,
                    ),
                    events=["budget.limit_reached"],
                )
            raw = await get_llm().generate_structured(
                _writer_prompt(content.topic, pack, claims, label, patterns),
                ScriptCandidate,
                system=_WRITER_SYSTEM,
            )
            llm_calls += 1
            candidate = _bind_to_research(raw, content.topic, claims, pack, label, patterns)
            row = Script(
                content_id=content.id,
                candidate_label=label,
                hook=candidate.hook,
                body=candidate.body,
                reveal=candidate.reveal,
                cta=candidate.cta,
                estimated_duration=candidate.estimated_duration,
                on_screen_text=candidate.on_screen_text,
                scene_plan=candidate.scene_plan,
                voice_style=candidate.voice_style,
                caption=candidate.caption,
                hashtags=candidate.hashtags,
                sources_used=candidate.sources_used,
                claims=[claim.model_dump(mode="json") for claim in candidate.claims],
                selected=False,
                critique={},
                normalized_hash=_normalized_hash(candidate.hook, candidate.body),
            )
            self.session.add(row)
            await self.session.flush()
            by_label[label] = row
            created_ids.append(str(row.id))
            logger.info("script_candidate_persisted", label=label, script_id=str(row.id))

        content.status = ContentStatus.SCRIPTING.value
        await enqueue(
            self.session,
            JobName.SCRIPT_CRITIQUE.value,
            payload={"content_id": str(content.id)},
            idempotency_key=idempotency_key("critic", content.id),
            content_id=content.id,
            workflow_id=content.workflow_id,
            correlation_id=agent_input.correlation_id,
        )
        await self.session.flush()
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "script_ids": created_ids,
                "labels": list(CANDIDATE_LABELS),
                "llm_calls": llm_calls,
                "hashes": [row.normalized_hash for row in by_label.values()],
            },
            decision=AgentDecision(
                decision="generate_script_candidates",
                reason=(
                    "Five original candidates were bound to research claims only; "
                    "critic review is required."
                ),
                evidence={
                    "labels": list(CANDIDATE_LABELS),
                    "claim_count": len(claims),
                    "writer_cannot_self_select": True,
                },
                confidence=0.7,
                expected_effect="independent_critique",
                related_entity_type="content_item",
                related_entity_id=content.id,
            ),
            events=["script.created"],
        )


async def handle_script_generate(session: AsyncSession, job: Job) -> None:
    agent_input, context, task = await _start(session, job, AgentName.SCRIPT_WRITER)
    result = await ScriptWriterAgent(session).run(agent_input, context)
    task.status = _task_status(result)
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "script_generate_failed")
    if result.status == AgentRunStatus.BUDGET_BLOCKED:
        raise RuntimeError(str((result.output or {}).get("reason") or "budget_blocked"))


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
        raise ValueError("script writer requires content_id")
    content = await session.get(ContentItem, content_id)
    if content is None:
        raise ValueError(f"content_item not found: {content_id}")
    return content


async def _latest_pack(session: AsyncSession, content_id: UUID) -> ResearchPack | None:
    result = await session.execute(
        select(ResearchPack)
        .where(ResearchPack.content_id == content_id)
        .order_by(ResearchPack.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _existing_scripts(session: AsyncSession, content_id: UUID) -> list[Script]:
    result = await session.execute(select(Script).where(Script.content_id == content_id))
    return list(result.scalars().all())


async def _load_patterns(
    session: AsyncSession, content_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    raw = payload.get("patterns")
    if isinstance(raw, dict) and raw.get("expression") == "abstract_only":
        return raw
    result = await session.execute(
        select(SystemEvent)
        .where(SystemEvent.content_id == content_id, SystemEvent.name == PATTERN_EVENT)
        .order_by(SystemEvent.created_at.desc())
        .limit(1)
    )
    event = result.scalar_one_or_none()
    if event and isinstance(event.payload, dict):
        patterns = event.payload.get("patterns")
        if isinstance(patterns, dict):
            return patterns
    return {"hook_type": "curiosity_gap", "pacing": "front_loaded", "expression": "abstract_only"}


def _publishable_claims(pack: ResearchPack) -> list[FactClaim]:
    claims: list[FactClaim] = []
    for raw in pack.claims or []:
        if not isinstance(raw, dict):
            continue
        try:
            claim = FactClaim.model_validate(raw)
        except Exception:  # noqa: BLE001
            continue
        if not claim.publishable:
            continue
        if claim.kind == ClaimKind.VERIFIED_FACT and not claim.sources:
            continue
        claims.append(claim)
    return claims


def _writer_prompt(
    topic: str,
    pack: ResearchPack,
    claims: list[FactClaim],
    label: str,
    patterns: dict[str, Any],
) -> str:
    claim_lines = "\n".join(f"- {claim.kind.value}: {claim.claim}" for claim in claims)
    return (
        f"topic: {topic}\n"
        f"candidate: {label}\n"
        f"abstract_hook_type: {patterns.get('hook_type', 'curiosity_gap')}\n"
        f"abstract_pacing: {patterns.get('pacing', 'front_loaded')}\n"
        f"research_summary: {pack.summary}\n"
        f"research claims:\n{claim_lines}\n"
        "Write one original candidate. Use only the listed claims. "
        "Do not add new factual assertions."
    )


def _bind_to_research(
    raw: ScriptCandidate,
    topic: str,
    claims: list[FactClaim],
    pack: ResearchPack,
    label: str,
    patterns: dict[str, Any],
) -> ScriptCandidate:
    idx = ord(label) - ord("A")
    rotated = claims[idx:] + claims[:idx] if claims else claims
    claim_texts = [claim.claim.strip() for claim in rotated if claim.claim.strip()]
    body = f"{_CONNECTIVE[label]} " + " ".join(claim_texts)
    hook = _FRAMES[label].format(topic=topic)
    duration = int(patterns.get("duration_seconds") or 0) or _duration_from_body(body)
    reveal_ok = bool(raw.reveal) and not _looks_like_new_fact(raw.reveal, claims)
    reveal = (
        raw.reveal.strip()
        if reveal_ok
        else "The useful constraint is documented above, not the headline."
    )
    cta = raw.cta.strip() if raw.cta else "Follow for the next sourced build note."
    caption = f"{topic}: sourced signals, not speculation."
    scenes = raw.scene_plan or [
        {"at": 0, "text": "HOOK", "duration": 3},
        {"at": 3, "text": topic[:48], "duration": 10},
        {"at": 13, "text": "SOURCED SIGNALS", "duration": 12},
        {"at": 25, "text": "REVEAL", "duration": max(6, duration - 25)},
    ]
    on_screen = raw.on_screen_text or [label, topic.upper()[:42], "SOURCED"]
    return ScriptCandidate(
        hook=hook,
        body=body,
        reveal=reveal,
        cta=cta,
        estimated_duration=max(18, min(70, duration)),
        on_screen_text=on_screen,
        scene_plan=scenes,
        voice_style=raw.voice_style or "clear_authoritative",
        caption=caption,
        hashtags=raw.hashtags or ["technology", "engineering", "sourced"],
        sources_used=list(pack.source_urls or []),
        claims=rotated,
    )


def _looks_like_new_fact(text: str, claims: list[FactClaim]) -> bool:
    lowered = text.lower()
    return not any(
        claim.claim.lower()[:24] in lowered or lowered[:24] in claim.claim.lower()
        for claim in claims
    )


def _duration_from_body(body: str) -> int:
    words = max(1, len(body.split()))
    return max(20, min(60, int(words / 2.5) + 8))


def _normalized_hash(hook: str, body: str) -> str:
    blob = re.sub(r"\s+", " ", f"{hook}\n{body}".lower()).strip()
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
