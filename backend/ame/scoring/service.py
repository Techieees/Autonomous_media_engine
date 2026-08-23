from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, JobName
from ame.contracts.schemas import AgentContext, AgentInput
from ame.db.models import AgentTask, Job
from ame.jobs.queue import enqueue
from ame.scoring.agent import OpportunityScoringAgent
from ame.scoring.scorer import FEATURE_WEIGHTS, SCORE_FORMULA, score_signal

__all__ = [
    "FEATURE_WEIGHTS",
    "SCORE_FORMULA",
    "OpportunityScoringAgent",
    "handle_opportunity_score",
    "score_signal",
]


async def handle_opportunity_score(session: AsyncSession, job: Job) -> None:
    settings = get_settings()
    payload = dict(job.payload or {})
    task = AgentTask(
        agent=AgentName.OPPORTUNITY_SCORING.value,
        status="running",
        payload=payload,
        content_id=job.content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    agent_input = AgentInput(
        task_id=task.id,
        agent=AgentName.OPPORTUNITY_SCORING,
        payload=payload,
        content_id=job.content_id,
        correlation_id=job.correlation_id,
        workflow_id=job.workflow_id,
    )
    context = AgentContext(
        dry_run=settings.dry_run,
        simulation=settings.dry_run,
        extra={"job_id": str(job.id), "job_name": job.name},
    )
    result = await OpportunityScoringAgent(session).run(agent_input, context)
    task.status = (
        "succeeded" if result.status == AgentRunStatus.SUCCEEDED else "failed"
    )
    await session.flush()
    if result.status != AgentRunStatus.SUCCEEDED:
        raise RuntimeError(result.error or "opportunity scoring failed")
    created = (result.output or {}).get("opportunities") or []
    if created:
        await enqueue(
            session,
            JobName.DIRECTOR_TICK.value,
            {"trigger": "opportunity.scored"},
            idempotency_key=f"director:after-score:{job.correlation_id or job.id}",
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id,
        )
