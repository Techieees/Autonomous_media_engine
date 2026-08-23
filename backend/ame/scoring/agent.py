from __future__ import annotations

from uuid import UUID

from ame.agents.base import Agent
from ame.contracts.enums import AgentName, AgentRunStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.scoring.pipeline import score_recent_signals


class OpportunityScoringAgent(Agent):
    name = AgentName.OPPORTUNITY_SCORING

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        output = await score_recent_signals(
            self.session,
            payload=agent_input.payload,
            correlation_id=agent_input.correlation_id,
            workflow_id=agent_input.workflow_id,
            simulation=context.simulation,
        )
        created = output.get("opportunities") or []
        top = created[0] if created else None
        raw_top_id = top.get("opportunity_id") if top else None
        top_id = UUID(str(raw_top_id)) if raw_top_id else None
        decision = AgentDecision(
            decision=f"scored_{len(created)}_opportunities",
            reason=output.get("summary") or "No new TrendSignals required scoring.",
            evidence=output,
            confidence=0.86 if created else 0.55,
            expected_effect="Director can approve top scored opportunities within caps.",
            related_entity_type="opportunity" if top_id else None,
            related_entity_id=top_id,
        )
        if created:
            from ame.agents.messaging import post_message
            from ame.contracts.enums import AgentMessageType

            await post_message(
                self.session,
                sender=self.name,
                recipient=AgentName.DIRECTOR,
                message_type=AgentMessageType.PROPOSAL,
                task="opportunity.score",
                related_entity_type="opportunity",
                related_entity_id=top_id,
                payload={"count": len(created), "top": top},
                confidence=0.86,
            )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output=output,
            decision=decision,
            events=["opportunity.scored"] if created else [],
        )
