from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus, JobName, JobStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import (
    AgentMessage,
    AgentTask,
    ContentItem,
    Job,
    Opportunity,
    ResearchPack,
    Script,
    SystemEvent,
)
from ame.jobs.queue import enqueue
from ame.observability import get_logger
from ame.pipeline.advance import idempotency_key

logger = get_logger("ame.agent.pattern_analyst")

_ACTIVE_JOB = {
    JobStatus.QUEUED.value,
    JobStatus.LEASED.value,
    JobStatus.RUNNING.value,
    JobStatus.SUCCEEDED.value,
    JobStatus.RETRY_WAIT.value,
}
PATTERN_EVENT = "pattern.extracted"


class PatternAnalystAgent(Agent):
    name = AgentName.PATTERN_ANALYST

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        content = await _require_content(self.session, agent_input.content_id)
        existing = await _latest_pattern_event(self.session, content.id)
        if existing is not None:
            scripts_queued = await _scripts_already_queued(self.session, content.id)
            enqueued = await _maybe_enqueue_scripts(
                self.session, content, agent_input.correlation_id, scripts_queued
            )
            return AgentResult(
                status=AgentRunStatus.SKIPPED,
                output={
                    "reused": True,
                    "patterns": existing.payload.get("patterns") if existing.payload else {},
                    "enqueued_script_generate": enqueued,
                    "scripts_already_queued": scripts_queued,
                },
                decision=AgentDecision(
                    decision="reuse_patterns",
                    reason=(
                        "Abstract patterns already stored; "
                        "no copyrighted expression was copied."
                    ),
                    evidence={"event_id": str(existing.id)},
                    confidence=0.8,
                    related_entity_type="content_item",
                    related_entity_id=content.id,
                ),
            )

        pack = await _latest_pack(self.session, content.id)
        features = await _opportunity_features(self.session, content)
        patterns = _abstract_patterns(content.topic, pack, features)
        self.session.add(
            AgentMessage(
                from_agent=self.name.value,
                to_agent=AgentName.SCRIPT_WRITER.value,
                kind="abstract_patterns",
                body={"patterns": patterns, "expression": "abstract_only"},
                content_id=content.id,
            )
        )

        scripts_queued = await _scripts_already_queued(self.session, content.id)
        enqueued = await _maybe_enqueue_scripts(
            self.session, content, agent_input.correlation_id, scripts_queued, patterns
        )

        await self.session.flush()
        logger.info(
            "patterns_extracted",
            content_id=str(content.id),
            hook_type=patterns["hook_type"],
            scripts_already_queued=scripts_queued,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "patterns": patterns,
                "enqueued_script_generate": enqueued,
                "scripts_already_queued": scripts_queued,
            },
            decision=AgentDecision(
                decision="store_abstract_patterns",
                reason=(
                    "Extracted hook type, pacing, duration, and curiosity-gap labels only. "
                    "No titles, dialogue, or other copyrighted expression were stored."
                ),
                evidence={"patterns": patterns, "scripts_already_queued": scripts_queued},
                confidence=0.7,
                expected_effect="inform_script_structure_without_copying",
                related_entity_type="content_item",
                related_entity_id=content.id,
            ),
            events=[PATTERN_EVENT],
        )


async def handle_pattern_analyze(session: AsyncSession, job: Job) -> None:
    agent_input, context, task = await _start(session, job, AgentName.PATTERN_ANALYST)
    result = await PatternAnalystAgent(session).run(agent_input, context)
    task.status = _task_status(result)
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "pattern_analyze_failed")


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
        raise ValueError("pattern analyst requires content_id")
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


async def _latest_pattern_event(session: AsyncSession, content_id: UUID) -> SystemEvent | None:
    result = await session.execute(
        select(SystemEvent)
        .where(SystemEvent.content_id == content_id, SystemEvent.name == PATTERN_EVENT)
        .order_by(SystemEvent.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _opportunity_features(session: AsyncSession, content: ContentItem) -> dict[str, Any]:
    if not content.opportunity_id:
        return {}
    opportunity = await session.get(Opportunity, content.opportunity_id)
    if opportunity is None:
        return {}
    return dict(opportunity.features or {})


async def _maybe_enqueue_scripts(
    session: AsyncSession,
    content: ContentItem,
    correlation_id: str | None,
    scripts_queued: bool,
    patterns: dict[str, Any] | None = None,
) -> bool:
    if scripts_queued or content.status in {
        ContentStatus.REJECTED.value,
        ContentStatus.FAILED.value,
        ContentStatus.PAUSED_BY_BUDGET.value,
    }:
        return False
    await enqueue(
        session,
        JobName.SCRIPT_GENERATE.value,
        payload={"content_id": str(content.id), "patterns": patterns or {}},
        idempotency_key=idempotency_key("scripts", content.id),
        content_id=content.id,
        workflow_id=content.workflow_id,
        correlation_id=correlation_id,
    )
    return True


async def _scripts_already_queued(session: AsyncSession, content_id: UUID) -> bool:
    job_row = await session.execute(
        select(Job.id)
        .where(
            Job.name == JobName.SCRIPT_GENERATE.value,
            Job.content_id == content_id,
            Job.status.in_(_ACTIVE_JOB),
        )
        .limit(1)
    )
    if job_row.scalar_one_or_none() is not None:
        return True
    count = await session.execute(select(Script.id).where(Script.content_id == content_id).limit(1))
    return count.scalar_one_or_none() is not None


def _abstract_patterns(
    topic: str, pack: ResearchPack | None, features: dict[str, Any]
) -> dict[str, Any]:
    blob = f"{topic} {(pack.summary if pack else '')}".lower()
    hook_type = "curiosity_gap"
    if "?" in topic or "why" in blob or "how" in blob:
        hook_type = "question"
    elif any(token in blob for token in (" vs ", "versus", "constraint", "not the")):
        hook_type = "contrast"
    elif any(token in blob for token in ("three", "3 ", "signals")):
        hook_type = "list"
    shelf = features.get("shelf_life_score")
    duration = 35
    if isinstance(shelf, (int, float)):
        duration = 28 if float(shelf) < 0.4 else 42
    pacing = "front_loaded" if hook_type in {"curiosity_gap", "question"} else "measured"
    return {
        "hook_type": hook_type,
        "pacing": pacing,
        "duration_seconds": duration,
        "duration_bucket": "30_45" if 30 <= duration <= 45 else "other",
        "curiosity_gap": hook_type in {"curiosity_gap", "question"},
        "structure": ["hook", "context", "reveal", "cta"],
        "expression": "abstract_only",
    }
