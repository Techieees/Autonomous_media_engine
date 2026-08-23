from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.db.models import (
    AgentMessage,
    AgentTask,
    BrandConfig,
    ContentItem,
    Job,
    StrategyAllocation,
    TrendSignal,
)
from ame.observability import get_logger

logger = get_logger("ame.agent.brand")


class BrandAgent(Agent):
    name = AgentName.BRAND

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        active = await _active_brand(self.session)
        if active is not None:
            return AgentResult(
                status=AgentRunStatus.SKIPPED,
                output={
                    "brand_id": str(active.id),
                    "version": active.version,
                    "proposed": False,
                    "platform_accounts_created": False,
                    "legal_agreements_created": False,
                },
                decision=AgentDecision(
                    decision="keep_active_brand",
                    reason="An active brand config already exists; no new identity was proposed.",
                    evidence={"brand_id": str(active.id), "version": active.version},
                    confidence=0.95,
                    related_entity_type="brand_config",
                    related_entity_id=active.id,
                ),
            )

        niches = await _collect_niches(self.session)
        topics = await _top_topics(self.session)
        dominant = niches[0] if niches else "technology"
        next_version = await _next_version(self.session)
        brand = BrandConfig(
            version=next_version,
            name=_brand_name(dominant),
            handles={},
            short_description=(
                f"Sourced short-form notes on {dominant} and adjacent public technology shifts."
            ),
            tone="precise",
            visual_identity={
                "template": "vertical_clean_v1",
                "ratio": "9:16",
                "motion": "restrained",
            },
            content_pillars=niches[:4] or ["technology", "systems", "public_signals"],
            audience="Operators and builders tracking public technology shifts.",
            voice_personality="Clear, sourced, non-sensational.",
            title_conventions="Concrete noun plus mechanism; no guaranteed-outcome clickbait.",
            caption_conventions="One claim, one source cue, one next-step CTA.",
            active=True,
        )
        self.session.add(brand)
        self.session.add(
            AgentMessage(
                from_agent=self.name.value,
                to_agent=AgentName.DIRECTOR.value,
                kind="brand_proposal",
                body={
                    "version": next_version,
                    "name": brand.name,
                    "niches": niches,
                    "topics": topics,
                    "platform_accounts_created": False,
                    "legal_agreements_created": False,
                },
                content_id=agent_input.content_id,
            )
        )
        await self.session.flush()
        logger.info("brand_proposed", brand_id=str(brand.id), version=next_version)
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "brand_id": str(brand.id),
                "version": brand.version,
                "name": brand.name,
                "proposed": True,
                "platform_accounts_created": False,
                "legal_agreements_created": False,
            },
            decision=AgentDecision(
                decision="propose_brand",
                reason=(
                    "No active brand existed; a versioned working identity "
                    "was proposed from niches and trends."
                ),
                evidence={
                    "niches": niches,
                    "topics": topics,
                    "handles_empty": True,
                },
                confidence=0.65,
                expected_effect="voice_and_caption_conventions_available",
                related_entity_type="brand_config",
                related_entity_id=brand.id,
            ),
            events=["brand.proposed"],
        )


async def handle_brand_propose(session: AsyncSession, job: Job) -> None:
    agent_input, context, task = await _start(session, job, AgentName.BRAND)
    result = await BrandAgent(session).run(agent_input, context)
    task.status = _task_status(result)
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "brand_propose_failed")


async def _start(
    session: AsyncSession, job: Job, agent: AgentName
) -> tuple[AgentInput, AgentContext, AgentTask]:
    payload = dict(job.payload or {})
    content_id = job.content_id
    raw = payload.get("content_id")
    if content_id is None and raw:
        content_id = UUID(str(raw))
    task = AgentTask(
        agent=agent.value,
        status="running",
        payload=payload,
        content_id=content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    content = await session.get(ContentItem, content_id) if content_id else None
    status: ContentStatus | None = None
    if content is not None:
        try:
            status = ContentStatus(content.status)
        except ValueError:
            status = None
    settings = get_settings()
    return (
        AgentInput(
            task_id=task.id,
            agent=agent,
            payload=payload,
            content_id=content_id,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id or (content.workflow_id if content else None),
        ),
        AgentContext(
            content_id=content_id,
            status=status,
            dry_run=settings.dry_run,
            simulation=bool(content.simulation) if content is not None else True,
            extra=payload,
        ),
        task,
    )


def _task_status(result: AgentResult) -> str:
    if result.status in {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.SKIPPED,
        AgentRunStatus.BUDGET_BLOCKED,
    }:
        return "succeeded"
    return "failed"


async def _active_brand(session: AsyncSession) -> BrandConfig | None:
    result = await session.execute(
        select(BrandConfig)
        .where(BrandConfig.active.is_(True))
        .order_by(BrandConfig.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _next_version(session: AsyncSession) -> int:
    result = await session.execute(select(func.max(BrandConfig.version)))
    current = result.scalar_one()
    return int(current or 0) + 1


async def _collect_niches(session: AsyncSession) -> list[str]:
    ordered: list[str] = []
    allocs = await session.execute(
        select(StrategyAllocation.niche)
        .where(StrategyAllocation.active.is_(True))
        .order_by(StrategyAllocation.allocation.desc())
    )
    for niche in allocs.scalars().all():
        if niche and niche not in ordered:
            ordered.append(niche)
    items = await session.execute(
        select(ContentItem.niche).where(ContentItem.niche.is_not(None)).distinct()
    )
    for niche in items.scalars().all():
        if niche and niche not in ordered:
            ordered.append(niche)
    topics = await session.execute(
        select(TrendSignal.topic).order_by(TrendSignal.trend_score.desc()).limit(12)
    )
    for topic in topics.scalars().all():
        niche = _classify_niche(topic)
        if niche not in ordered:
            ordered.append(niche)
    return ordered or ["technology"]


async def _top_topics(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(TrendSignal.topic).order_by(TrendSignal.trend_score.desc()).limit(8)
    )
    return [topic for topic in result.scalars().all() if topic]


def _classify_niche(topic: str) -> str:
    text = topic.lower()
    if any(token in text for token in ("ai", "llm", "model", "gpt", "neural")):
        return "ai_systems"
    if any(token in text for token in ("chip", "semiconductor", "hardware", "robot")):
        return "engineering"
    if any(token in text for token in ("market", "startup", "funding", "ipo")):
        return "markets"
    if any(token in text for token in ("policy", "regulation", "law", "privacy")):
        return "policy_tech"
    if any(token in text for token in ("science", "biology", "physics", "climate")):
        return "science"
    return "technology"


def _brand_name(niche: str) -> str:
    label = niche.replace("_", " ").strip().title() or "Signal"
    return f"{label} Brief"
