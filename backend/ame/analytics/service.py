from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.agent import AnalyticsAgent
from ame.analytics.queries import distributions, overview_metrics
from ame.analytics.runner import dispatch_agent
from ame.contracts.enums import AgentName
from ame.db.models import Job


async def handle_analytics_snapshot(session: AsyncSession, job: Job) -> None:
    await dispatch_agent(session, job, AnalyticsAgent(session), AgentName.ANALYTICS)


async def analytics_overview(session: AsyncSession) -> dict[str, Any]:
    return await overview_metrics(session)


__all__ = [
    "distributions",
    "handle_analytics_snapshot",
    "overview_metrics",
    "analytics_overview",
]
