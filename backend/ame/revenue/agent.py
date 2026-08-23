from __future__ import annotations

from ame.agents.base import Agent
from ame.contracts.enums import AgentName, AgentRunStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.revenue.sync import sync_revenue


class RevenueAgent(Agent):
    name = AgentName.REVENUE

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        outcome = await sync_revenue(self.session, agent_input.payload)
        output = {
            "connected": outcome.connected,
            "actual_written": outcome.actual_written,
            "forecast_written": outcome.forecast_written,
            "business_written": outcome.business_written,
            "noop": outcome.noop,
            "reason": outcome.reason,
            "note": (
                "No-op actual sync: nothing connected and no business entry. "
                "Actual revenue stays null, not a fabricated CPM."
                if outcome.noop
                else "Actual rows are stored only from connected platform data or explicit entry. "
                "Forecast rows are kind=forecast and excluded from actual totals."
            ),
        }
        decision = "revenue_sync_noop" if outcome.noop else "revenue_sync"
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output=output,
            decision=AgentDecision(
                decision=decision,
                reason=output["note"],
                evidence=output,
                confidence=0.95 if outcome.noop or outcome.actual_written else 0.6,
                expected_effect="Dashboard actual totals stay honest.",
                related_entity_type="revenue",
            ),
            events=outcome.events,
        )
