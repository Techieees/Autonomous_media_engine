from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import get_settings
from ame.contracts.enums import JobName
from ame.contracts.schemas import TrendSignalIn
from ame.db.models import Job
from ame.jobs.queue import enqueue
from ame.observability import get_logger
from ame.trends.adapters import network_adapters
from ame.trends.adapters.base import TrendAdapter
from ame.trends.adapters.fixture import load_fixture_signals
from ame.trends.http import DEFAULT_HEADERS, DEFAULT_TIMEOUT
from ame.trends.normalize import finalize_signal
from ame.trends.persist import CollectedSignal, dedupe_signals, upsert_trend_signals
from ame.trends.scoring import compute_trend_score

logger = get_logger("ame.trends")


async def handle_trend_ingest(session: AsyncSession, job: Job) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    collected = await _collect_network_signals(now)
    used_fixture = False
    if not collected:
        collected = _collect_fixture_signals(now)
        used_fixture = bool(collected)
        logger.info("trend_fixture_fallback", loaded=len(collected))

    for item in collected:
        finalized = finalize_signal(item.signal, now)
        finalized.trend_score = compute_trend_score(finalized)
        item.signal = finalized

    collected = dedupe_signals(collected)
    created = await upsert_trend_signals(
        session,
        collected,
        now=now,
        correlation_id=job.correlation_id,
        workflow_id=job.workflow_id,
    )
    hour_key = now.strftime("%Y-%m-%d-%H")
    await enqueue(
        session,
        JobName.OPPORTUNITY_SCORE.value,
        {
            "source_job_id": str(job.id),
            "ingested": len(collected),
            "created": len(created),
            "used_fixture": used_fixture,
        },
        idempotency_key=f"opportunity-score:{hour_key}",
        correlation_id=job.correlation_id,
        workflow_id=job.workflow_id,
    )
    logger.info(
        "trend_ingest_completed",
        ingested=len(collected),
        created=len(created),
        used_fixture=used_fixture,
        dry_run=settings.dry_run,
        correlation_id=job.correlation_id,
    )


async def _collect_network_signals(now: datetime) -> list[CollectedSignal]:
    settings = get_settings()
    adapters = network_adapters()
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
    ) as client:
        outcomes = await asyncio.gather(
            *(_run_adapter(adapter, client, now) for adapter in adapters)
        )
    collected: list[CollectedSignal] = []
    attempted = 0
    failed = 0
    for adapter, outcome in zip(adapters, outcomes, strict=True):
        if outcome == "skipped":
            logger.info("trend_adapter_skipped", adapter=adapter.name)
            continue
        attempted += 1
        if isinstance(outcome, BaseException):
            failed += 1
            logger.warning("trend_adapter_failed", adapter=adapter.name, error=str(outcome))
            continue
        if not outcome:
            logger.info("trend_adapter_empty", adapter=adapter.name)
            continue
        collected.extend(CollectedSignal(signal=signal, simulation=False) for signal in outcome)
        logger.info("trend_adapter_ok", adapter=adapter.name, count=len(outcome))
    logger.info(
        "trend_network_complete",
        attempted=attempted,
        failed=failed,
        signals=len(collected),
        env=settings.app_env,
    )
    return collected


async def _run_adapter(
    adapter: TrendAdapter,
    client: httpx.AsyncClient,
    now: datetime,
) -> list[TrendSignalIn] | str | BaseException:
    settings = get_settings()
    if not adapter.is_configured(settings):
        return "skipped"
    try:
        return await adapter.fetch(client, settings, now=now)
    except Exception as exc:  # noqa: BLE001
        return exc


def _collect_fixture_signals(now: datetime) -> list[CollectedSignal]:
    try:
        signals = load_fixture_signals(now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trend_fixture_failed", error=str(exc))
        return []
    return [CollectedSignal(signal=signal, simulation=True) for signal in signals]
