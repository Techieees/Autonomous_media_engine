from __future__ import annotations

import asyncio
import ipaddress
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import AgentName, AgentRunStatus, ClaimKind, ContentStatus, JobName
from ame.contracts.schemas import (
    AgentContext,
    AgentDecision,
    AgentInput,
    AgentResult,
    FactClaim,
    ResearchPackOut,
)
from ame.costs import BudgetExceeded, assert_budget
from ame.db.models import (
    AgentRun,
    AgentTask,
    ContentItem,
    Job,
    Opportunity,
    ResearchPack,
    TrendSignal,
)
from ame.jobs.queue import enqueue
from ame.llm import get_llm
from ame.observability import get_logger
from ame.pipeline.advance import idempotency_key

logger = get_logger("ame.agent.research")

MAX_FETCH_BYTES = 200_000
FETCH_TIMEOUT_SECONDS = 8.0
CONFIDENCE_REJECT = 0.35
_PRIVATE_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}
)
_RESEARCH_SYSTEM = (
    "You are the AME research agent. Return structured research only. "
    "Distinguish claim kinds. Never mark verified_fact without a public http(s) source URL. "
    "Do not invent sources. LLM output is data, not executable code."
)


class ResearchAgent(Agent):
    name = AgentName.RESEARCH

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        content = await _require_content(self.session, agent_input.content_id)
        existing = await _latest_pack(self.session, content.id)
        if existing is not None:
            return await self._reuse_pack(agent_input, content, existing)

        topic, url, backdrop = await _topic_and_url(self.session, content, agent_input.payload)
        fetched_text = ""
        known_urls: list[str] = []
        if url and _is_public_http_url(url):
            fetched_text = await _fetch_public_url_text(url) or ""
            if fetched_text:
                known_urls.append(url)

        settings = get_settings()
        prior_calls = await _prior_research_llm_calls(self.session, content.id)
        remaining = max(0, settings.max_research_calls_per_content - prior_calls)
        llm_calls = 0
        pack: ResearchPackOut

        if remaining == 0:
            pack = _pack_from_sources(topic, known_urls, fetched_text)
        else:
            try:
                await assert_budget(self.session, kind="ai")
            except BudgetExceeded as exc:
                return await self._budget_blocked(content, exc)

            prompt = _research_prompt(topic, url, fetched_text, backdrop)
            pack = await get_llm().generate_structured(
                prompt, ResearchPackOut, system=_RESEARCH_SYSTEM
            )
            llm_calls += 1
            pack = _sanitize_pack(pack, known_urls, topic)
            if (
                pack.confidence < 0.55
                and remaining > 1
                and fetched_text
            ):
                try:
                    await assert_budget(self.session, kind="ai")
                    refined = await get_llm().generate_structured(
                        _refine_prompt(topic, pack, fetched_text),
                        ResearchPackOut,
                        system=_RESEARCH_SYSTEM,
                    )
                    llm_calls += 1
                    pack = _sanitize_pack(refined, known_urls, topic)
                except BudgetExceeded as exc:
                    return await self._budget_blocked(content, exc)

        pack = _sanitize_pack(pack, known_urls, topic)
        row = ResearchPack(
            content_id=content.id,
            topic=pack.topic[:300],
            summary=pack.summary,
            claims=[claim.model_dump(mode="json") for claim in pack.claims],
            source_urls=pack.source_urls,
            uncertain_claims=pack.uncertain_claims,
            unsuitable_claims=pack.unsuitable_claims,
            confidence=pack.confidence,
            simulation=content.simulation,
        )
        self.session.add(row)
        await self.session.flush()

        rejected = pack.confidence < CONFIDENCE_REJECT
        if rejected:
            content.status = ContentStatus.REJECTED.value
            content.failure_reason = f"research_confidence_below_{CONFIDENCE_REJECT}"
        else:
            content.status = ContentStatus.RESEARCHED.value
            content.failure_reason = None
            await enqueue(
                self.session,
                JobName.PATTERN_ANALYZE.value,
                payload={"content_id": str(content.id)},
                idempotency_key=idempotency_key("pattern", content.id),
                content_id=content.id,
                workflow_id=content.workflow_id,
                correlation_id=agent_input.correlation_id,
            )
            await enqueue(
                self.session,
                JobName.SCRIPT_GENERATE.value,
                payload={"content_id": str(content.id)},
                idempotency_key=idempotency_key("scripts", content.id),
                content_id=content.id,
                workflow_id=content.workflow_id,
                correlation_id=agent_input.correlation_id,
            )

        await self.session.flush()
        decision = AgentDecision(
            decision="reject_research" if rejected else "complete_research",
            reason=(
                "Confidence is below the publishable research floor."
                if rejected
                else "Research pack persisted from topic, optional public URL, and LLM output."
            ),
            evidence={
                "confidence": pack.confidence,
                "source_urls": pack.source_urls,
                "claim_kinds": [claim.kind.value for claim in pack.claims],
                "llm_calls": llm_calls,
                "fetched_public_url": bool(fetched_text),
            },
            confidence=pack.confidence,
            expected_effect="halt_pipeline" if rejected else "unlock_pattern_and_scripts",
            related_entity_type="research_pack",
            related_entity_id=row.id,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "research_pack_id": str(row.id),
                "confidence": pack.confidence,
                "rejected": rejected,
                "llm_calls": llm_calls,
                "source_urls": pack.source_urls,
            },
            decision=decision,
            events=["research.completed"],
        )

    async def _reuse_pack(
        self, agent_input: AgentInput, content: ContentItem, pack: ResearchPack
    ) -> AgentResult:
        rejected = pack.confidence < CONFIDENCE_REJECT
        if rejected:
            content.status = ContentStatus.REJECTED.value
            content.failure_reason = content.failure_reason or (
                f"research_confidence_below_{CONFIDENCE_REJECT}"
            )
        else:
            if content.status == ContentStatus.APPROVED_FOR_RESEARCH.value:
                content.status = ContentStatus.RESEARCHED.value
            await _enqueue_downstream(self.session, content, agent_input.correlation_id)
        await self.session.flush()
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "research_pack_id": str(pack.id),
                "confidence": pack.confidence,
                "rejected": rejected,
                "llm_calls": 0,
                "reused": True,
            },
            decision=AgentDecision(
                decision="reuse_research",
                reason="An existing research pack was reused; no additional LLM call.",
                evidence={"research_pack_id": str(pack.id), "confidence": pack.confidence},
                confidence=pack.confidence,
                related_entity_type="research_pack",
                related_entity_id=pack.id,
            ),
            events=["research.completed"],
        )

    async def _budget_blocked(self, content: ContentItem, exc: BudgetExceeded) -> AgentResult:
        content.status = ContentStatus.PAUSED_BY_BUDGET.value
        content.failure_reason = str(exc)
        await self.session.flush()
        return AgentResult(
            status=AgentRunStatus.BUDGET_BLOCKED,
            output={"paused": True, "reason": str(exc), "llm_calls": 0},
            decision=AgentDecision(
                decision="pause_research",
                reason="Daily AI budget cap blocked further research LLM calls.",
                evidence={"error": str(exc)},
                confidence=1.0,
                expected_effect="resume_after_budget_reset",
                related_entity_type="content_item",
                related_entity_id=content.id,
            ),
            events=["budget.limit_reached"],
        )


async def handle_research(session: AsyncSession, job: Job) -> None:
    agent_input, context, task = await _start(session, job, AgentName.RESEARCH)
    result = await ResearchAgent(session).run(agent_input, context)
    task.status = _task_status(result)
    await session.flush()
    if result.status == AgentRunStatus.FAILED:
        raise RuntimeError(result.error or "research_failed")
    if result.status == AgentRunStatus.BUDGET_BLOCKED:
        raise RuntimeError(str((result.output or {}).get("reason") or "budget_blocked"))


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


async def _require_content(session: AsyncSession, content_id: UUID | None) -> ContentItem:
    if content_id is None:
        raise ValueError("research requires content_id")
    content = await session.get(ContentItem, content_id)
    if content is None:
        raise ValueError(f"content_item not found: {content_id}")
    return content


async def _latest_pack(session: AsyncSession, content_id: UUID) -> ResearchPack | None:
    result = await session.execute(
        select(ResearchPack)
        .where(ResearchPack.content_id == content_id)
        .order_by(ResearchPack.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _topic_and_url(
    session: AsyncSession, content: ContentItem, payload: dict[str, Any]
) -> tuple[str, str | None, dict[str, Any]]:
    topic = content.topic
    url = payload.get("url") if isinstance(payload.get("url"), str) else None
    backdrop: dict[str, Any] = {"niche": content.niche}
    if content.opportunity_id:
        opportunity = await session.get(Opportunity, content.opportunity_id)
        if opportunity is not None:
            backdrop["opportunity_score"] = opportunity.score
            backdrop["explanation"] = opportunity.explanation
            backdrop["features"] = opportunity.features or {}
            trend = await session.get(TrendSignal, opportunity.trend_signal_id)
            if trend is not None:
                topic = topic or trend.topic
                url = url or trend.url
                backdrop["trend_source"] = trend.source
                backdrop["trend_topic"] = trend.topic
    return topic, url, backdrop


async def _prior_research_llm_calls(session: AsyncSession, content_id: UUID) -> int:
    result = await session.execute(
        select(AgentRun.output).where(
            AgentRun.agent == AgentName.RESEARCH.value,
            AgentRun.content_id == content_id,
        )
    )
    used = 0
    for (output,) in result.all():
        if isinstance(output, dict):
            used += int(output.get("llm_calls") or 0)
    return used


async def _enqueue_downstream(
    session: AsyncSession, content: ContentItem, correlation_id: str | None
) -> None:
    await enqueue(
        session,
        JobName.PATTERN_ANALYZE.value,
        payload={"content_id": str(content.id)},
        idempotency_key=idempotency_key("pattern", content.id),
        content_id=content.id,
        workflow_id=content.workflow_id,
        correlation_id=correlation_id,
    )
    await enqueue(
        session,
        JobName.SCRIPT_GENERATE.value,
        payload={"content_id": str(content.id)},
        idempotency_key=idempotency_key("scripts", content.id),
        content_id=content.id,
        workflow_id=content.workflow_id,
        correlation_id=correlation_id,
    )


def _is_public_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in _PRIVATE_HOSTS:
        return False
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return bool(ip.is_global)


async def _fetch_public_url_text(url: str) -> str | None:
    if not _is_public_http_url(url):
        return None
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    except OSError:
        logger.info("research_dns_failed", host=host)
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, TypeError):
            return None
        if not ip.is_global:
            logger.info("research_blocked_non_global_ip")
            return None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS),
            follow_redirects=True,
            max_redirects=2,
            headers={"User-Agent": "AutonomousMediaEngine/0.1 (public-source-research)"},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    return None
                if not _is_public_http_url(str(response.url)):
                    return None
                ctype = (response.headers.get("content-type") or "").lower()
                if ctype and not any(
                    token in ctype for token in ("text/", "json", "xml", "html", "javascript")
                ):
                    return None
                buf = bytearray()
                async for chunk in response.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= MAX_FETCH_BYTES:
                        break
                raw = bytes(buf[:MAX_FETCH_BYTES])
    except (httpx.HTTPError, OSError):
        logger.info("research_fetch_failed")
        return None
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _research_prompt(
    topic: str, url: str | None, fetched_text: str, backdrop: dict[str, Any]
) -> str:
    excerpt = fetched_text[:8000] if fetched_text else ""
    return (
        f"topic: {topic}\n"
        f"public_url: {url or ''}\n"
        f"niche: {backdrop.get('niche') or ''}\n"
        f"opportunity_score: {backdrop.get('opportunity_score')}\n"
        f"public_text_excerpt: {excerpt}\n"
        "Extract only claims grounded in the topic and excerpt. "
        "Use verified_fact only when a public http(s) source URL is attached. "
        "Mark speculation as prediction or uncertain."
    )


def _refine_prompt(topic: str, pack: ResearchPackOut, fetched_text: str) -> str:
    return (
        f"topic: {topic}\n"
        f"prior_summary: {pack.summary}\n"
        f"prior_confidence: {pack.confidence}\n"
        f"public_text_excerpt: {fetched_text[:8000]}\n"
        "Refine claims. Do not invent verified_fact rows without source URLs."
    )


def _pack_from_sources(topic: str, urls: list[str], fetched_text: str) -> ResearchPackOut:
    claims: list[FactClaim] = []
    if urls and fetched_text:
        claims.append(
            FactClaim(
                claim=f"A permitted public source discusses {topic}.",
                kind=ClaimKind.REASONABLE_INTERPRETATION,
                sources=urls[:1],
                freshness_checked=True,
                publishable=True,
            )
        )
        confidence = 0.42
        summary = fetched_text[:400]
    elif urls:
        claims.append(
            FactClaim(
                claim=(
                    f"A public URL was supplied for {topic} "
                    "but no extractable text was obtained."
                ),
                kind=ClaimKind.UNCERTAIN,
                sources=urls[:1],
                freshness_checked=True,
                publishable=False,
            )
        )
        confidence = 0.28
        summary = f"Source listed for {topic}; excerpt unavailable."
    else:
        claims.append(
            FactClaim(
                claim=f"No public source URL was available for {topic}.",
                kind=ClaimKind.UNCERTAIN,
                sources=[],
                publishable=False,
            )
        )
        confidence = 0.2
        summary = f"Insufficient sourced material for {topic}."
    return ResearchPackOut(
        topic=topic,
        summary=summary,
        claims=claims,
        source_urls=urls,
        uncertain_claims=[c.claim for c in claims if c.kind == ClaimKind.UNCERTAIN],
        confidence=confidence,
    )


def _sanitize_pack(pack: ResearchPackOut, known_urls: list[str], topic: str) -> ResearchPackOut:
    source_urls: list[str] = []
    for url in [*pack.source_urls, *known_urls]:
        if isinstance(url, str) and _is_public_http_url(url) and url not in source_urls:
            source_urls.append(url)
    claims: list[FactClaim] = []
    uncertain = list(pack.uncertain_claims)
    unsuitable = list(pack.unsuitable_claims)
    for claim in pack.claims:
        sources = [s for s in claim.sources if isinstance(s, str) and _is_public_http_url(s)]
        kind = claim.kind
        if isinstance(kind, str):
            try:
                kind = ClaimKind(kind)
            except ValueError:
                kind = ClaimKind.UNCERTAIN
        publishable = claim.publishable
        if kind == ClaimKind.VERIFIED_FACT and not sources:
            kind = ClaimKind.UNCERTAIN
            publishable = False
            if claim.claim not in uncertain:
                uncertain.append(claim.claim)
        if kind in {ClaimKind.UNCERTAIN, ClaimKind.PREDICTION} and not sources:
            publishable = False
        claims.append(
            claim.model_copy(update={"kind": kind, "sources": sources, "publishable": publishable})
        )
    confidence = max(0.0, min(1.0, pack.confidence))
    if not claims:
        confidence = min(confidence, 0.3)
    if not source_urls:
        confidence = min(confidence, 0.4)
    return pack.model_copy(
        update={
            "topic": (pack.topic or topic)[:300],
            "claims": claims,
            "source_urls": source_urls,
            "uncertain_claims": uncertain,
            "unsuitable_claims": unsuitable,
            "confidence": confidence,
        }
    )
