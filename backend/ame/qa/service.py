from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.base import Agent
from ame.config import get_settings
from ame.contracts.enums import (
    AgentName,
    AgentRunStatus,
    ConnectionState,
    ContentStatus,
    HumanActionStatus,
    JobName,
    JobStatus,
    Platform,
    QAVerdict,
)
from ame.contracts.schemas import AgentContext, AgentDecision, AgentInput, AgentResult, QAResultOut
from ame.db.models import (
    AgentTask,
    ContentItem,
    HumanAction,
    Job,
    MediaAsset,
    Opportunity,
    PlatformConnection,
    ProductionManifestRecord,
    QAResult,
    ResearchPack,
    Script,
)
from ame.jobs.queue import enqueue
from ame.observability import get_logger
from ame.originality.fingerprints import compute_fingerprint, persist_fingerprint
from ame.qa.checks import CHECK_KEYS, QABundle, decide_verdict, run_checks
from ame.storage.base import get_store

logger = get_logger("ame.qa")

_EVENT = {
    QAVerdict.APPROVED: "qa.approved",
    QAVerdict.REJECTED: "qa.rejected",
    QAVerdict.REQUIRES_REVIEW: "qa.requires_review",
}


class QAAgent(Agent):
    name = AgentName.QA

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentResult:
        if agent_input.content_id is None:
            raise ValueError("qa agent requires content_id")
        content = await self.session.get(ContentItem, agent_input.content_id)
        if content is None:
            raise ValueError(f"content not found: {agent_input.content_id}")

        content.status = ContentStatus.QA.value
        script = await _load_script(self.session, content)
        assets = await _load_assets(self.session, content.id)
        research = await _load_research(self.session, content.id)
        opportunity = await _load_opportunity(self.session, content)
        manifest = await _load_manifest(self.session, content.id)
        store = get_store()

        report = await compute_fingerprint(self.session, content, script, assets)
        await persist_fingerprint(self.session, content, script, report)

        bundle = QABundle(
            content=content,
            script=script,
            assets=assets,
            research=research,
            opportunity=opportunity,
            manifest=manifest,
            store=store,
            originality=report,
        )
        checks = await run_checks(bundle)
        missing = [key for key in CHECK_KEYS if key not in checks]
        if missing:
            raise RuntimeError(f"QA checks incomplete: {missing}")
        verdict, reasons = decide_verdict(checks)
        qa_out = QAResultOut(verdict=verdict, checks=checks, reasons=reasons)

        self.session.add(
            QAResult(
                content_id=content.id,
                verdict=qa_out.verdict.value,
                checks=qa_out.checks,
                reasons=qa_out.reasons,
            )
        )

        await _apply_verdict(
            self.session,
            content,
            qa_out.verdict,
            qa_out.reasons,
            correlation_id=agent_input.correlation_id,
        )
        await self.session.flush()

        logger.info(
            "qa_completed",
            content_id=str(content.id),
            verdict=qa_out.verdict.value,
            reasons=qa_out.reasons,
        )
        return AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            output={
                "verdict": qa_out.verdict.value,
                "reasons": qa_out.reasons,
                "checks": qa_out.checks,
                "originality": report.as_dict(),
                "status": content.status,
            },
            decision=AgentDecision(
                decision=qa_out.verdict.value,
                reason="; ".join(qa_out.reasons) or "all QA checks passed",
                evidence={
                    "check_names": list(qa_out.checks),
                    "originality": report.as_dict(),
                    "dry_run": context.dry_run,
                },
                confidence=_confidence(qa_out.verdict, qa_out.checks),
                expected_effect=_expected_effect(qa_out.verdict),
                related_entity_type="content_item",
                related_entity_id=content.id,
            ),
            events=[_EVENT[qa_out.verdict]],
        )


async def handle_qa_check(session: AsyncSession, job: Job) -> None:
    content_id = _job_content_id(job)
    content = await session.get(ContentItem, content_id)
    if content is None:
        raise ValueError(f"qa.check content not found: {content_id}")

    content.status = ContentStatus.QA.value
    task = AgentTask(
        agent=AgentName.QA.value,
        status="running",
        payload=job.payload or {},
        content_id=content.id,
        workflow_id=job.workflow_id or content.workflow_id,
        correlation_id=job.correlation_id,
    )
    session.add(task)
    await session.flush()

    agent = QAAgent(session)
    result = await agent.run(
        AgentInput(
            task_id=task.id,
            agent=AgentName.QA,
            payload=job.payload or {},
            content_id=content.id,
            correlation_id=job.correlation_id,
            workflow_id=job.workflow_id or content.workflow_id,
        ),
        AgentContext(
            content_id=content.id,
            status=ContentStatus.QA,
            dry_run=get_settings().dry_run,
            simulation=bool(content.simulation),
            extra={"job_id": str(job.id)},
        ),
    )
    task.status = result.status.value
    if result.status == AgentRunStatus.FAILED:
        content.status = ContentStatus.FAILED.value
        content.failure_reason = result.error
        await session.flush()
        raise RuntimeError(result.error or "qa agent failed")
    await session.flush()


async def _apply_verdict(
    session: AsyncSession,
    content: ContentItem,
    verdict: QAVerdict,
    reasons: list[str],
    *,
    correlation_id: str | None,
) -> None:
    joined = "; ".join(reasons)[:4000] if reasons else None
    if verdict == QAVerdict.APPROVED:
        content.status = ContentStatus.APPROVED.value
        content.failure_reason = None
        await _enqueue_publish(session, content, correlation_id=correlation_id)
        return
    if verdict == QAVerdict.REJECTED:
        content.status = ContentStatus.REJECTED.value
        content.failure_reason = joined or "qa rejected"
        await _cancel_publish_jobs(session, content.id)
        return
    if get_settings().autonomous_mode:
        content.status = ContentStatus.REJECTED.value
        content.failure_reason = joined or "qa requires review; autonomous reject without owner ticket"
        await _cancel_publish_jobs(session, content.id)
        return
    content.status = ContentStatus.AWAITING_HUMAN.value
    content.failure_reason = joined or "qa requires review"
    await _cancel_publish_jobs(session, content.id)
    await _ensure_human_action(session, content, reasons)


async def _enqueue_publish(
    session: AsyncSession, content: ContentItem, *, correlation_id: str | None
) -> None:
    settings = get_settings()
    if settings.dry_run or content.simulation:
        platforms = [Platform.DRY_RUN.value]
    else:
        rows = await session.execute(
            select(PlatformConnection.platform).where(
                PlatformConnection.state == ConnectionState.READY.value
            )
        )
        platforms = [row[0] for row in rows.all()]
    for platform in platforms:
        await enqueue(
            session,
            JobName.PUBLISH.value,
            payload={"content_id": str(content.id), "platform": platform},
            idempotency_key=f"publish:{content.id}:{platform}",
            content_id=content.id,
            workflow_id=content.workflow_id,
            correlation_id=correlation_id,
        )


async def _cancel_publish_jobs(session: AsyncSession, content_id: UUID) -> None:
    await session.execute(
        update(Job)
        .where(
            Job.content_id == content_id,
            Job.name == JobName.PUBLISH.value,
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]),
        )
        .values(status=JobStatus.CANCELLED.value)
    )


async def _ensure_human_action(
    session: AsyncSession, content: ContentItem, reasons: list[str]
) -> None:
    title = f"QA review: {(content.topic or 'content')[:80]} [{content.id}]"
    existing = await session.execute(
        select(HumanAction).where(
            HumanAction.category == "qa",
            HumanAction.status == HumanActionStatus.OPEN.value,
            HumanAction.title == title[:200],
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    reason_text = "\n".join(f"- {item}" for item in reasons) or "- see qa_results.checks"
    session.add(
        HumanAction(
            title=title[:200],
            instructions=(
                f"Content {content.id} is awaiting human QA review.\n"
                f"Topic: {content.topic}\n"
                "Do not publish until the owner accepts or the script/assets are fixed.\n"
                f"Reasons:\n{reason_text}"
            ),
            category="qa",
            status=HumanActionStatus.OPEN.value,
            platform=None,
            blocking=True,
        )
    )


async def _load_script(session: AsyncSession, content: ContentItem) -> Script | None:
    if content.selected_script_id is not None:
        selected = await session.get(Script, content.selected_script_id)
        if selected is not None:
            return selected
    tagged = await session.execute(
        select(Script)
        .where(Script.content_id == content.id, Script.selected.is_(True))
        .order_by(Script.created_at.desc())
    )
    found = tagged.scalars().first()
    if found is not None:
        return found
    latest = await session.execute(
        select(Script).where(Script.content_id == content.id).order_by(Script.created_at.desc())
    )
    return latest.scalars().first()


async def _load_assets(session: AsyncSession, content_id: UUID) -> list[MediaAsset]:
    result = await session.execute(select(MediaAsset).where(MediaAsset.content_id == content_id))
    return list(result.scalars())


async def _load_research(session: AsyncSession, content_id: UUID) -> ResearchPack | None:
    result = await session.execute(
        select(ResearchPack)
        .where(ResearchPack.content_id == content_id)
        .order_by(ResearchPack.created_at.desc())
    )
    return result.scalars().first()


async def _load_opportunity(session: AsyncSession, content: ContentItem) -> Opportunity | None:
    if content.opportunity_id is None:
        return None
    return await session.get(Opportunity, content.opportunity_id)


async def _load_manifest(
    session: AsyncSession, content_id: UUID
) -> ProductionManifestRecord | None:
    result = await session.execute(
        select(ProductionManifestRecord)
        .where(ProductionManifestRecord.content_id == content_id)
        .order_by(ProductionManifestRecord.created_at.desc())
    )
    return result.scalars().first()


def _job_content_id(job: Job) -> UUID:
    if job.content_id is not None:
        return job.content_id
    raw = (job.payload or {}).get("content_id")
    if not raw:
        raise ValueError("qa.check missing content_id")
    return UUID(str(raw))


def _confidence(verdict: QAVerdict, checks: dict[str, Any]) -> float:
    probe = checks.get("ffprobe") or {}
    skipped = bool(probe.get("skipped"))
    if verdict == QAVerdict.APPROVED:
        return 0.72 if skipped else 0.94
    if verdict == QAVerdict.REQUIRES_REVIEW:
        return 0.6
    return 0.88


def _expected_effect(verdict: QAVerdict) -> str:
    if verdict == QAVerdict.APPROVED:
        return "content approved; publish job enqueued"
    if verdict == QAVerdict.REJECTED:
        return "content rejected; publish blocked"
    return "content awaiting_human; HumanAction opened"
