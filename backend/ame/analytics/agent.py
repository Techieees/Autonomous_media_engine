from __future__ import annotations

from ame.agents.base import Agent
from ame.analytics.classify import classify_performance
from ame.analytics.snapshot import take_snapshot
from ame.contracts.enums import AgentName, AgentRunStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import Job


class AnalyticsAgent(Agent):
    name = AgentName.ANALYTICS

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        job = (context.extra or {}).get("job")
        if not isinstance(job, Job):
            return AgentResult(status=AgentRunStatus.FAILED, error="analytics_missing_job")
        outcome = await take_snapshot(self.session, job)
        snapshot = outcome.snapshot
        if snapshot is None or outcome.publication is None:
            return AgentResult(
                status=AgentRunStatus.FAILED,
                error=outcome.skipped_reason or "snapshot_missing",
            )
        performance = classify_performance(
            views=snapshot.views,
            completion_rate=snapshot.completion_rate,
            shares=snapshot.shares,
            followers_gained=snapshot.followers_gained,
        )
        output = {
            "publication_id": str(outcome.publication.id),
            "content_id": str(outcome.publication.content_id),
            "checkpoint": snapshot.checkpoint,
            "created": outcome.created,
            "simulation": snapshot.simulation,
            "source": outcome.source,
            "views": snapshot.views,
            "likes": snapshot.likes,
            "comments": snapshot.comments,
            "shares": snapshot.shares,
            "watch_time_seconds": snapshot.watch_time_seconds,
            "completion_rate": snapshot.completion_rate,
            "followers_gained": snapshot.followers_gained,
            "performance_class": performance.value,
            "next_checkpoint": outcome.next_checkpoint,
            "learning_enqueued": outcome.learning_enqueued,
            "not_actual": bool(snapshot.simulation),
        }
        reason = (
            "Stored labeled synthetic metrics for a simulated publication."
            if snapshot.simulation
            else "Stored normalized official-platform metrics (or an unavailable placeholder)."
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output=output,
            decision=AgentDecision(
                decision=f"snapshot:{snapshot.checkpoint}",
                reason=reason,
                evidence=output,
                confidence=0.9 if outcome.source in {"synthetic", "platform"} else 0.4,
                expected_effect="Director receives later learning from checkpoint jobs.",
                related_entity_type="publication",
                related_entity_id=outcome.publication.id,
            ),
            events=["metrics.received"],
        )
