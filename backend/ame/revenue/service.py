from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.runner import dispatch_agent
from ame.contracts.enums import AgentName
from ame.db.models import Job
from ame.revenue.agent import RevenueAgent
from ame.revenue.forecast import labeled_forecast
from ame.revenue.queries import revenue_overview
from ame.revenue.sync import record_business_entry, sync_revenue


async def handle_revenue_sync(session: AsyncSession, job: Job) -> None:
    await dispatch_agent(session, job, RevenueAgent(session), AgentName.REVENUE)


async def revenue_metrics(session: AsyncSession) -> dict[str, Any]:
    return await revenue_overview(session)


__all__ = [
    "handle_revenue_sync",
    "labeled_forecast",
    "record_business_entry",
    "revenue_metrics",
    "revenue_overview",
    "sync_revenue",
]
