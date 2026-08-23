from __future__ import annotations

import time
from abc import ABC, abstractmethod
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import AgentName, AgentRunStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import AgentDecisionRecord, AgentRun, SystemEvent
from ame.observability import bind_job_context, get_logger

logger = get_logger("ame.agent")


class Agent(ABC):
    name: AgentName

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        started = time.perf_counter()
        run = AgentRun(
            id=uuid4(),
            agent=self.name.value,
            task_id=agent_input.task_id,
            status=AgentRunStatus.STARTED.value,
            input=agent_input.model_dump(mode="json"),
            content_id=agent_input.content_id,
            workflow_id=agent_input.workflow_id,
            correlation_id=agent_input.correlation_id,
        )
        self.session.add(run)
        await self.session.flush()
        bind_job_context(
            correlation_id=agent_input.correlation_id,
            workflow_id=str(agent_input.workflow_id) if agent_input.workflow_id else None,
            agent_run_id=str(run.id),
            content_id=str(agent_input.content_id) if agent_input.content_id else None,
        )
        try:
            result = await self.execute(agent_input, context)
        except Exception as exc:  # noqa: BLE001
            result = AgentResult(status=AgentRunStatus.FAILED, error=str(exc))
            logger.exception("agent_failed", agent=self.name.value)
        run.status = result.status.value
        run.output = result.output
        run.error = result.error
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        if result.decision:
            await self.persist_decision(result.decision, run.id, agent_input.content_id)
        for event_name in result.events:
            self.session.add(
                SystemEvent(
                    name=event_name,
                    payload=result.output,
                    correlation_id=agent_input.correlation_id,
                    workflow_id=agent_input.workflow_id,
                    content_id=agent_input.content_id,
                    agent_run_id=run.id,
                    simulation=context.simulation,
                )
            )
        await self.session.flush()
        return result

    async def persist_decision(
        self, decision: AgentDecision, run_id: UUID, content_id: UUID | None
    ) -> None:
        self.session.add(
            AgentDecisionRecord(
                agent=self.name.value,
                decision=decision.decision,
                reason=decision.reason,
                evidence=decision.evidence,
                confidence=decision.confidence,
                expected_effect=decision.expected_effect,
                related_entity_type=decision.related_entity_type,
                related_entity_id=decision.related_entity_id,
                run_id=run_id,
                content_id=content_id,
            )
        )

    @abstractmethod
    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        raise NotImplementedError
