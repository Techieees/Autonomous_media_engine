from __future__ import annotations

from ame.agents.base import Agent
from ame.analytics.ids import as_uuid
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus
from ame.contracts.schemas import AgentContext, AgentInput, AgentResult
from ame.db.models import AgentTask, ContentItem, Job, Publication
from sqlalchemy.ext.asyncio import AsyncSession


async def dispatch_agent(
    session: AsyncSession,
    job: Job,
    agent: Agent,
    name: AgentName,
) -> AgentResult:
    payload = dict(job.payload or {})
    content_id = job.content_id or as_uuid(payload.get("content_id"))
    publication_id = as_uuid(payload.get("publication_id"))
    simulation = True
    status: ContentStatus | None = None
    if publication_id:
        publication = await session.get(Publication, publication_id)
        if publication is not None:
            simulation = publication.simulation
            content_id = content_id or publication.content_id
    if content_id:
        content = await session.get(ContentItem, content_id)
        if content is not None:
            simulation = simulation or content.simulation
            try:
                status = ContentStatus(content.status)
            except ValueError:
                status = None
    task = AgentTask(
        agent=name.value,
        status="running",
        payload=payload,
        content_id=content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    settings = get_settings()
    result = await agent.run(
        AgentInput(
            task_id=task.id,
            agent=name,
            payload=payload,
            content_id=content_id,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id,
        ),
        AgentContext(
            content_id=content_id,
            status=status,
            dry_run=settings.dry_run,
            simulation=bool(simulation or settings.dry_run),
            extra={"job": job, "job_id": str(job.id)},
        ),
    )
    task.status = result.status.value
    await session.flush()
    if result.status != AgentRunStatus.SUCCEEDED:
        raise RuntimeError(result.error or f"{name.value}_failed")
    return result
