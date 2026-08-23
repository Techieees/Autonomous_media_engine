from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ame.analytics.runner import dispatch_agent
from ame.contracts.enums import AgentName
from ame.db.models import Job
from ame.learning.agent import LearningAgent


async def handle_learning_update(session: AsyncSession, job: Job) -> None:
    await dispatch_agent(session, job, LearningAgent(session), AgentName.LEARNING)
