from __future__ import annotations

from ame.agents.base import Agent
from ame.contracts.enums import AgentName, AgentRunStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import Job
from ame.learning.recommend import decision_from_recommendation
from ame.learning.update import outcome_payload, run_learning_update


class LearningAgent(Agent):
    name = AgentName.LEARNING

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        job = (context.extra or {}).get("job")
        if not isinstance(job, Job):
            return AgentResult(status=AgentRunStatus.FAILED, error="learning_missing_job")
        outcome = await run_learning_update(self.session, job)
        output = outcome_payload(outcome)
        decision_text, reason, evidence, confidence = decision_from_recommendation(
            outcome.recommendation, outcome.record.content_id
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output=output,
            decision=AgentDecision(
                decision=decision_text[:200],
                reason=reason,
                evidence=evidence,
                confidence=confidence,
                expected_effect="Director may adjust niche allocation below owner caps.",
                related_entity_type="content_item",
                related_entity_id=outcome.record.content_id,
            ),
            events=["learning.updated"],
        )
