from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import Settings, get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ContentStatus, JobName
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult
from ame.costs.tracker import BudgetExceeded, assert_budget, produced_today, spend_today
from ame.db.models import (
    AgentDecisionRecord,
    AgentRun,
    AgentTask,
    BrandConfig,
    ContentItem,
    Job,
    LearningRecommendation,
    Opportunity,
    StrategyAllocation,
    TrendSignal,
)
from ame.agents.messaging import post_message
from ame.contracts.enums import AgentMessageType
from ame.jobs.queue import enqueue
from ame.observability import get_logger
from ame.ops.daily_plan import ensure_daily_plan, update_preferences
from ame.pipeline.advance import handle_pipeline_advance

logger = get_logger("ame.director")

DEFAULT_ALLOCATIONS: dict[str, float] = {
    "ai": 20.0,
    "robotics": 20.0,
    "science": 15.0,
    "space": 10.0,
    "engineering": 15.0,
    "history": 10.0,
    "business": 10.0,
}

MAX_NICHE_ALLOCATION = 40.0
MIN_NICHE_ALLOCATION = 0.0
ALLOCATION_STEP = 5.0
LEARNING_MIN_CONFIDENCE = 0.3

FORBIDDEN_CAP_KEYS = frozenset(
    {
        "daily_ai_spend_limit",
        "daily_media_spend_limit",
        "max_content_per_day",
        "daily_cost_limit",
        "maximum_daily_content",
        "DAILY_AI_SPEND_LIMIT",
        "DAILY_MEDIA_SPEND_LIMIT",
        "MAX_CONTENT_PER_DAY",
    }
)

NICHE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "artificial intelligence", "machine learning", "llm", "gpt", "neural"),
    "robotics": ("robot", "humanoid", "automation", "drone"),
    "science": ("science", "physics", "biology", "chemistry", "quantum"),
    "space": ("space", "nasa", "mars", "orbit", "rocket", "satellite"),
    "engineering": ("engineering", "chip", "semiconductor", "hardware", "manufactur"),
    "history": ("history", "ancient", "civilization", "empire", "war "),
    "business": ("business", "startup", "market", "econom", "company", "finance"),
}

IN_FLIGHT_CONTENT = frozenset(
    {
        ContentStatus.APPROVED_FOR_RESEARCH.value,
        ContentStatus.RESEARCHED.value,
        ContentStatus.SCRIPTING.value,
        ContentStatus.SCRIPT_SELECTED.value,
        ContentStatus.PRODUCTION.value,
        ContentStatus.QA.value,
        ContentStatus.APPROVED.value,
        ContentStatus.PUBLISHING.value,
        ContentStatus.PUBLISHED.value,
        ContentStatus.MEASURING.value,
        ContentStatus.AWAITING_HUMAN.value,
        ContentStatus.AWAITING_PLATFORM_APPROVAL.value,
    }
)

PROPOSED_BRAND: dict[str, Any] = {
    "name": "Signal Brief",
    "handles": {"proposed": True, "binding": False},
    "short_description": (
        "Proposed, non-binding short-form explainer identity for AI, robotics, "
        "science, and engineering. No platform accounts are created by this record."
    ),
    "tone": "precise",
    "visual_identity": {
        "template": "vertical_clean_v1",
        "palette": "dark",
        "proposed": True,
        "binding": False,
    },
    "content_pillars": list(DEFAULT_ALLOCATIONS.keys()),
    "audience": "Curious generalists who want compact, sourced explainers.",
    "voice_personality": "clear_authoritative",
    "title_conventions": "Specific claim or question; no fabricated stakes.",
    "caption_conventions": "One-sentence takeaway; cite sources for factual claims.",
}


class Director(Agent):
    name = AgentName.DIRECTOR

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self._extra_decisions: list[AgentDecision] = []

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        settings = get_settings()
        events: list[str] = ["director.decision"]
        await ensure_daily_plan(self.session)
        seeded = await self._seed_allocations_if_needed(agent_input)
        if seeded:
            events.append("strategy.changed")
        brand = await self._ensure_proposed_brand(agent_input)
        if brand is not None:
            events.append("brand.proposed")
        adjusted = await self._consume_learning(agent_input)
        if adjusted:
            events.append("strategy.changed")

        produced = await produced_today(self.session)
        in_flight = await self._in_flight_count()
        budget_payload = await self._budget_snapshot(produced, settings)
        budget_payload["in_flight"] = in_flight
        allocations = await self._active_allocations()
        allocation_map = {row.niche: row.allocation for row in allocations}

        try:
            await assert_budget(self.session, kind="ai")
            await assert_budget(self.session, kind="media")
        except BudgetExceeded as exc:
            events.append("budget.limit_reached")
            await self._pause_unstarted_research(exc)
            decision = AgentDecision(
                decision="paused_by_budget",
                reason=f"Budget cap reached ({exc.kind}); paid work will not be enqueued.",
                evidence={
                    **budget_payload,
                    "ok": False,
                    "kind": exc.kind,
                    "spent": exc.spent,
                    "limit": exc.limit,
                    "allocations": allocation_map,
                },
                confidence=1.0,
                expected_effect="Halt new paid production until the daily window resets.",
            )
            return AgentResult(
                status=AgentRunStatus.BUDGET_BLOCKED,
                output={
                    "decision": decision.decision,
                    "budget": decision.evidence,
                    "allocations": allocation_map,
                    "brand_created": brand is not None,
                    "seeded_allocations": seeded,
                    "learning_adjusted": adjusted,
                    "dry_run": settings.dry_run,
                },
                decision=decision,
                events=events,
            )

        opportunity, trend = await self._top_unused_opportunity()
        can_produce = _below_daily_caps(produced + in_flight, settings)
        approved_content: ContentItem | None = None
        if opportunity is not None and trend is not None and can_produce:
            approved_content = await self._approve_opportunity(
                opportunity, trend, allocations, settings, agent_input
            )
            events.append("opportunity.approved")
            await post_message(
                self.session,
                sender=self.name,
                recipient=AgentName.RESEARCH,
                message_type=AgentMessageType.ASSIGNMENT,
                task="research.run",
                related_entity_type="content_item",
                related_entity_id=approved_content.id,
                content_id=approved_content.id,
                task_id=agent_input.task_id,
                payload={
                    "opportunity_id": str(opportunity.id),
                    "topic": approved_content.topic,
                    "niche": approved_content.niche,
                },
                confidence=_score_confidence(opportunity.score),
            )
            decision = AgentDecision(
                decision="approved_for_research",
                reason=(
                    "Top unused scored opportunity selected under daily content and spend caps."
                ),
                evidence={
                    **budget_payload,
                    "opportunity_id": str(opportunity.id),
                    "content_id": str(approved_content.id),
                    "score": opportunity.score,
                    "topic": approved_content.topic,
                    "niche": approved_content.niche,
                    "allocations": allocation_map,
                },
                confidence=_score_confidence(opportunity.score),
                expected_effect="Start original research for the highest-value unused opportunity.",
                related_entity_type="content_item",
                related_entity_id=approved_content.id,
            )
        elif not can_produce:
            decision = AgentDecision(
                decision="hold",
                reason="Daily content already at target or max cap; no additional approval.",
                evidence={**budget_payload, "allocations": allocation_map},
                confidence=0.85,
                expected_effect="Protect owner volume caps and keep production conservative.",
            )
        elif opportunity is None and seeded:
            decision = AgentDecision(
                decision="seed_allocations",
                reason="Conservative default niche mix persisted because none existed.",
                evidence={"allocations": allocation_map, **budget_payload},
                confidence=0.9,
                expected_effect="Give later ticks a bounded 100-point mix to rebalance.",
            )
        elif opportunity is None and adjusted:
            decision = AgentDecision(
                decision="adjusted_allocations",
                reason="Consumed learning recommendations and rebalanced within existing niches.",
                evidence={"allocations": allocation_map, **budget_payload},
                confidence=0.55,
                expected_effect="Shift future topic mix without raising owner hard caps.",
            )
        elif opportunity is None and brand is not None:
            decision = AgentDecision(
                decision="brand_proposed",
                reason="No BrandConfig existed; stored a non-binding proposed identity.",
                evidence={"brand_id": str(brand.id), "brand_name": brand.name},
                confidence=0.6,
                expected_effect="Give downstream agents a proposed tone without opening accounts.",
                related_entity_type="brand_config",
                related_entity_id=brand.id,
            )
        elif opportunity is None:
            decision = AgentDecision(
                decision="hold",
                reason="No unused scored opportunities are available in PostgreSQL.",
                evidence={**budget_payload, "allocations": allocation_map},
                confidence=0.8,
                expected_effect="Wait for scoring; Director will not invent topics from memory.",
            )
        else:
            decision = AgentDecision(
                decision="hold",
                reason="System inspected; no allocation, brand, or approval action required.",
                evidence={**budget_payload, "allocations": allocation_map},
                confidence=0.7,
                expected_effect="Maintain current strategy until new scored opportunities arrive.",
            )

        if can_produce:
            await self._enqueue_inflight_advances(agent_input)

        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "decision": decision.decision,
                "budget": budget_payload,
                "allocations": allocation_map,
                "approved_content_id": str(approved_content.id) if approved_content else None,
                "brand_created": brand is not None,
                "seeded_allocations": seeded,
                "learning_adjusted": adjusted,
                "dry_run": settings.dry_run,
                "simulation": context.simulation,
            },
            decision=decision,
            events=list(dict.fromkeys(events)),
        )

    async def _budget_snapshot(self, produced: int, settings: Settings) -> dict[str, Any]:
        return {
            "ok": True,
            "ai_spent": await spend_today(self.session, "ai"),
            "media_spent": await spend_today(self.session, "media"),
            "total_spent": await spend_today(self.session),
            "produced_today": produced,
            "target_daily_content": settings.target_daily_content,
            "maximum_daily_content": settings.maximum_daily_content,
            "max_content_per_day": settings.max_content_per_day,
            "daily_ai_spend_limit": settings.daily_ai_spend_limit,
            "daily_media_spend_limit": settings.daily_media_spend_limit,
            "daily_cost_limit": settings.daily_cost_limit,
        }

    async def _seed_allocations_if_needed(self, agent_input: AgentInput) -> bool:
        existing = await self.session.execute(select(StrategyAllocation.id).limit(1))
        if existing.scalar_one_or_none() is not None:
            return False
        rows: list[StrategyAllocation] = []
        for niche, weight in DEFAULT_ALLOCATIONS.items():
            row = StrategyAllocation(
                niche=niche,
                allocation=weight,
                reason="Conservative first-run defaults totaling 100.",
                active=True,
                decided_by=self.name.value,
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        await self._record_extra(
            AgentDecision(
                decision="seed_allocations",
                reason="No strategy allocations existed; seeded conservative defaults totaling 100.",
                evidence={"allocations": dict(DEFAULT_ALLOCATIONS), "sum": 100.0},
                confidence=0.95,
                expected_effect="Establish a bounded niche mix Director may later rebalance.",
                related_entity_type="strategy_allocation",
                related_entity_id=rows[0].id,
            ),
            agent_input,
        )
        logger.info("director_seeded_allocations", allocations=DEFAULT_ALLOCATIONS)
        return True

    async def _ensure_proposed_brand(self, agent_input: AgentInput) -> BrandConfig | None:
        existing = await self.session.execute(select(BrandConfig.id).limit(1))
        if existing.scalar_one_or_none() is not None:
            return None
        brand = BrandConfig(
            name=str(PROPOSED_BRAND["name"]),
            version=1,
            handles=dict(PROPOSED_BRAND["handles"]),
            short_description=str(PROPOSED_BRAND["short_description"]),
            tone=str(PROPOSED_BRAND["tone"]),
            visual_identity=dict(PROPOSED_BRAND["visual_identity"]),
            content_pillars=list(PROPOSED_BRAND["content_pillars"]),
            audience=str(PROPOSED_BRAND["audience"]),
            voice_personality=str(PROPOSED_BRAND["voice_personality"]),
            title_conventions=str(PROPOSED_BRAND["title_conventions"]),
            caption_conventions=str(PROPOSED_BRAND["caption_conventions"]),
            active=True,
        )
        self.session.add(brand)
        await self.session.flush()
        await self._record_extra(
            AgentDecision(
                decision="brand_proposed",
                reason="No BrandConfig existed; stored a non-binding proposed brand.",
                evidence={
                    "brand_id": str(brand.id),
                    "name": brand.name,
                    "binding": False,
                    "accounts_created": False,
                },
                confidence=0.6,
                expected_effect="Provide tone and pillars without creating platform accounts.",
                related_entity_type="brand_config",
                related_entity_id=brand.id,
            ),
            agent_input,
        )
        logger.info("director_brand_proposed", brand_id=str(brand.id), name=brand.name)
        return brand

    async def _consume_learning(self, agent_input: AgentInput) -> bool:
        result = await self.session.execute(
            select(LearningRecommendation).where(LearningRecommendation.consumed.is_(False))
        )
        recommendations = list(result.scalars().all())
        if not recommendations:
            return False
        allocations = await self._active_allocations()
        existing = {row.niche.lower(): row for row in allocations}
        applied = False
        for rec in recommendations:
            before = {row.niche: row.allocation for row in existing.values()}
            changed = _apply_learning(existing, rec)
            rec.consumed = True
            features = rec.features if isinstance(rec.features, dict) else {}
            prefs = {}
            if features.get("target_duration_s") or "35-45" in rec.recommendation.lower():
                prefs["target_duration_s"] = int(features.get("target_duration_s") or 40)
            if features.get("hook_style"):
                prefs["hook_style"] = str(features["hook_style"])
            if "question hook" in rec.recommendation.lower():
                prefs["hook_style"] = "question"
            if prefs:
                await update_preferences(self.session, prefs)
            if not changed and not prefs:
                continue
            applied = True
            after = {row.niche: row.allocation for row in existing.values()}
            await self._record_extra(
                AgentDecision(
                    decision="adjusted_allocations",
                    reason="Applied a consumed learning recommendation within existing niches.",
                    evidence={
                        "recommendation_id": str(rec.id),
                        "recommendation": rec.recommendation,
                        "method": rec.method,
                        "before": before,
                        "after": after,
                        "sum": round(sum(after.values()), 2),
                        "hard_caps_changed": False,
                    },
                    confidence=rec.confidence,
                    expected_effect=(
                        "Rebalance future topic mix; owner spend and volume caps unchanged."
                    ),
                    related_entity_type="learning_recommendation",
                    related_entity_id=rec.id,
                ),
                agent_input,
            )
        await self.session.flush()
        logger.info("director_consumed_learning", count=len(recommendations), applied=applied)
        return applied

    async def _approve_opportunity(
        self,
        opportunity: Opportunity,
        trend: TrendSignal,
        allocations: list[StrategyAllocation],
        settings: Settings,
        agent_input: AgentInput,
    ) -> ContentItem:
        niche = _infer_niche(opportunity, trend, allocations)
        topic = (trend.title or trend.topic)[:300]
        existing = await self.session.execute(
            select(ContentItem).where(ContentItem.opportunity_id == opportunity.id)
        )
        content = existing.scalar_one_or_none()
        if content is None:
            content = ContentItem(
                topic=topic,
                niche=niche,
                status=ContentStatus.APPROVED_FOR_RESEARCH.value,
                opportunity_id=opportunity.id,
                simulation=settings.dry_run,
            )
            self.session.add(content)
        else:
            content.topic = topic
            content.niche = niche or content.niche
            content.status = ContentStatus.APPROVED_FOR_RESEARCH.value
            content.simulation = settings.dry_run
        opportunity.approved = True
        opportunity.status = "approved"
        await self.session.flush()
        await enqueue(
            self.session,
            JobName.RESEARCH.value,
            {
                "content_id": str(content.id),
                "opportunity_id": str(opportunity.id),
                "topic": topic,
            },
            idempotency_key=f"research:{content.id}",
            content_id=content.id,
            workflow_id=content.workflow_id,
            correlation_id=agent_input.correlation_id,
        )
        await enqueue(
            self.session,
            JobName.PIPELINE_ADVANCE.value,
            {"content_id": str(content.id)},
            idempotency_key=f"advance:{content.id}:{content.status}",
            content_id=content.id,
            workflow_id=content.workflow_id,
            correlation_id=agent_input.correlation_id,
        )
        await self.session.flush()
        logger.info(
            "director_approved_opportunity",
            opportunity_id=str(opportunity.id),
            content_id=str(content.id),
            niche=niche,
            score=opportunity.score,
        )
        return content

    async def _top_unused_opportunity(self) -> tuple[Opportunity | None, TrendSignal | None]:
        used_rows = await self.session.execute(
            select(ContentItem.opportunity_id).where(
                ContentItem.opportunity_id.is_not(None),
                ContentItem.status.notin_(
                    [ContentStatus.DISCOVERED.value, ContentStatus.SCORED.value]
                ),
            )
        )
        used = {oid for oid in used_rows.scalars().all() if oid is not None}
        stmt = (
            select(Opportunity, TrendSignal)
            .join(TrendSignal, TrendSignal.id == Opportunity.trend_signal_id)
            .where(Opportunity.approved.is_(False), Opportunity.status == "scored")
            .order_by(Opportunity.score.desc(), Opportunity.created_at.asc())
        )
        if used:
            stmt = stmt.where(Opportunity.id.notin_(used))
        result = await self.session.execute(stmt.limit(1))
        row = result.first()
        if row is None:
            return None, None
        return row[0], row[1]

    async def _in_flight_count(self) -> int:
        rows = await self.session.execute(
            select(ContentItem.id).where(ContentItem.status.in_(IN_FLIGHT_CONTENT))
        )
        return len(list(rows.scalars().all()))

    async def _active_allocations(self) -> list[StrategyAllocation]:
        result = await self.session.execute(
            select(StrategyAllocation).where(StrategyAllocation.active.is_(True))
        )
        return list(result.scalars().all())

    async def _pause_unstarted_research(self, exc: BudgetExceeded) -> None:
        items = await self.session.execute(
            select(ContentItem).where(
                ContentItem.status == ContentStatus.APPROVED_FOR_RESEARCH.value
            )
        )
        paused = 0
        for item in items.scalars():
            item.status = ContentStatus.PAUSED_BY_BUDGET.value
            item.failure_reason = str(exc)
            paused += 1
        await self.session.flush()
        logger.info("director_paused_by_budget", kind=exc.kind, paused=paused)

    async def _enqueue_inflight_advances(self, agent_input: AgentInput) -> None:
        result = await self.session.execute(
            select(ContentItem)
            .where(ContentItem.status.in_(IN_FLIGHT_CONTENT))
            .order_by(ContentItem.created_at.asc())
            .limit(25)
        )
        for item in result.scalars():
            await enqueue(
                self.session,
                JobName.PIPELINE_ADVANCE.value,
                {"content_id": str(item.id)},
                idempotency_key=f"advance:{item.id}:{item.status}",
                content_id=item.id,
                workflow_id=item.workflow_id,
                correlation_id=agent_input.correlation_id,
            )

    async def _record_extra(self, decision: AgentDecision, agent_input: AgentInput) -> None:
        self._extra_decisions.append(decision)
        run_id = await self._latest_run_id(agent_input.task_id)
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
                content_id=agent_input.content_id,
            )
        )

    async def _latest_run_id(self, task_id: UUID) -> UUID | None:
        result = await self.session.execute(
            select(AgentRun.id)
            .where(AgentRun.task_id == task_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def handle_director_tick(session: AsyncSession, job: Job) -> None:
    settings = get_settings()
    task = AgentTask(
        agent=AgentName.DIRECTOR.value,
        status="running",
        payload=job.payload or {},
        content_id=job.content_id,
        workflow_id=job.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()
    agent_input = AgentInput(
        task_id=task.id,
        agent=AgentName.DIRECTOR,
        payload=job.payload or {},
        content_id=job.content_id,
        correlation_id=job.correlation_id,
        workflow_id=job.workflow_id,
    )
    context = AgentContext(
        content_id=job.content_id,
        dry_run=settings.dry_run,
        simulation=settings.dry_run,
        extra={"job_id": str(job.id), "job_name": job.name},
    )
    result = await Director(session).run(agent_input, context)
    task.status = result.status.value
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "director_tick_failed")


def _below_daily_caps(produced: int, settings: Settings) -> bool:
    return (
        produced < settings.target_daily_content
        and produced < settings.maximum_daily_content
        and produced < settings.max_content_per_day
    )


def _score_confidence(score: float) -> float:
    if 0.0 <= score <= 1.0:
        return max(0.35, min(0.95, score))
    return max(0.35, min(0.95, score / 100.0))


def _infer_niche(
    opportunity: Opportunity,
    trend: TrendSignal,
    allocations: list[StrategyAllocation],
) -> str:
    featured = None
    if isinstance(opportunity.features, dict):
        featured = opportunity.features.get("niche") or opportunity.features.get("matched_niche")
    if isinstance(featured, str) and featured.strip():
        return featured.strip().lower()[:80]
    haystack = f"{trend.topic} {trend.title}".lower()
    for niche, keywords in NICHE_KEYWORDS.items():
        if any(token in haystack for token in keywords):
            return niche
    if allocations:
        return max(allocations, key=lambda row: row.allocation).niche
    return "ai"


def _apply_learning(
    existing: dict[str, StrategyAllocation], rec: LearningRecommendation
) -> bool:
    if not existing:
        return False
    if rec.confidence < LEARNING_MIN_CONFIDENCE:
        return False
    features = rec.features if isinstance(rec.features, dict) else {}
    if _mentions_hard_caps(features, rec.recommendation):
        return False
    deltas = _learning_deltas(features, rec.recommendation, existing)
    if not deltas:
        return False
    proposed = {name: row.allocation for name, row in existing.items()}
    for name, delta in deltas.items():
        if name in proposed:
            proposed[name] = proposed[name] + delta
    bounded = _bounded_allocations(proposed)
    changed = False
    for name, row in existing.items():
        next_value = bounded[name]
        if abs(row.allocation - next_value) >= 0.01:
            row.allocation = next_value
            row.reason = f"learning:{rec.recommendation[:180]}"
            row.decided_by = AgentName.DIRECTOR.value
            changed = True
    return changed


def _mentions_hard_caps(features: dict[str, Any], text: str) -> bool:
    keys = {str(key) for key in features}
    if keys & FORBIDDEN_CAP_KEYS:
        return True
    lowered = text.lower()
    cap_terms = (
        "daily_ai_spend",
        "daily_media_spend",
        "max_content_per_day",
        "daily_cost_limit",
        "daily ai spend",
        "daily media spend",
    )
    return any(term in lowered for term in cap_terms)


def _learning_deltas(
    features: dict[str, Any],
    text: str,
    existing: dict[str, StrategyAllocation],
) -> dict[str, float] | None:
    niches = set(existing)
    raw = features.get("allocation_adjustments") or features.get("adjustments")
    if isinstance(raw, dict):
        deltas = {
            str(name).lower(): float(value)
            for name, value in raw.items()
            if str(name).lower() in niches
        }
        return deltas or None

    boost = str(
        features.get("boost_niche")
        or features.get("increase_niche")
        or features.get("recommended_niche")
        or ""
    ).lower()
    reduce = str(features.get("reduce_niche") or features.get("decrease_niche") or "").lower()
    direction = str(features.get("direction") or features.get("action") or "").lower()
    feature_niche = str(features.get("niche") or "").lower()
    try:
        step = abs(
            float(features.get("delta") or features.get("allocation_delta") or ALLOCATION_STEP)
        )
    except (TypeError, ValueError):
        step = ALLOCATION_STEP

    if boost not in niches and feature_niche in niches and direction in {
        "increase",
        "boost",
        "raise",
        "decrease",
        "reduce",
        "lower",
    }:
        boost = feature_niche
    if boost not in niches:
        parsed = _parse_text_niche(text, niches)
        if parsed is None:
            return None
        boost, signed = parsed
        if signed < 0:
            reduce = boost
            others = [name for name in niches if name != reduce]
            if not others:
                return None
            boost = min(others, key=lambda name: existing[name].allocation)
            step = abs(signed)
        else:
            step = abs(signed)
    if boost not in niches:
        return None
    if direction in {"decrease", "reduce", "lower"} and reduce not in niches:
        reduce = boost
        others = [name for name in niches if name != reduce]
        if not others:
            return None
        boost = min(others, key=lambda name: existing[name].allocation)
    if reduce not in niches:
        others = [name for name in niches if name != boost]
        if not others:
            return None
        reduce = max(others, key=lambda name: existing[name].allocation)
    if boost == reduce:
        return None
    return {boost: step, reduce: -step}


def _parse_text_niche(text: str, niches: set[str]) -> tuple[str, float] | None:
    lowered = text.lower()
    found = [name for name in niches if name in lowered]
    if not found:
        return None
    if any(word in lowered for word in ("increase", "boost", "raise", "more")):
        return found[0], ALLOCATION_STEP
    if any(word in lowered for word in ("decrease", "reduce", "lower", "less")):
        return found[0], -ALLOCATION_STEP
    return None


def _bounded_allocations(weights: dict[str, float]) -> dict[str, float]:
    names = list(weights)
    values = [
        min(MAX_NICHE_ALLOCATION, max(MIN_NICHE_ALLOCATION, float(weights[name])))
        for name in names
    ]
    target = 100.0
    for _ in range(8):
        current = sum(values)
        if abs(current - target) < 0.01:
            break
        if current <= 0:
            even = target / max(len(names), 1)
            values = [min(MAX_NICHE_ALLOCATION, even) for _ in names]
            continue
        scale = target / current
        values = [
            min(MAX_NICHE_ALLOCATION, max(MIN_NICHE_ALLOCATION, value * scale)) for value in values
        ]
    leftover = target - sum(values)
    if leftover > 0.01:
        for index, value in enumerate(values):
            room = MAX_NICHE_ALLOCATION - value
            take = min(room, leftover)
            values[index] += take
            leftover -= take
            if leftover <= 0.01:
                break
    elif leftover < -0.01:
        for index, value in enumerate(values):
            reducible = value - MIN_NICHE_ALLOCATION
            take = min(reducible, -leftover)
            values[index] -= take
            leftover += take
            if leftover >= -0.01:
                break
    rounded = [round(value, 2) for value in values]
    drift = round(target - sum(rounded), 2)
    if names and abs(drift) >= 0.01:
        if drift > 0:
            index = max(range(len(rounded)), key=lambda i: MAX_NICHE_ALLOCATION - rounded[i])
        else:
            index = max(range(len(rounded)), key=lambda i: rounded[i] - MIN_NICHE_ALLOCATION)
        rounded[index] = round(
            min(MAX_NICHE_ALLOCATION, max(MIN_NICHE_ALLOCATION, rounded[index] + drift)),
            2,
        )
    return {name: rounded[index] for index, name in enumerate(names)}


__all__ = ["Director", "handle_director_tick", "handle_pipeline_advance"]
