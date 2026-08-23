from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ame.contracts.enums import JobName
from ame.db.models import Job

Handler = Callable[[AsyncSession, Job], Awaitable[Any]]


def resolve(name: str) -> Handler:
    from ame.jobs import handlers

    mapping: dict[str, Handler] = {
        JobName.DIRECTOR_TICK.value: handlers.handle_director_tick,
        JobName.TREND_INGEST.value: handlers.handle_trend_ingest,
        JobName.NICHE_EVALUATE.value: handlers.handle_niche_evaluate,
        JobName.OPPORTUNITY_SCORE.value: handlers.handle_opportunity_score,
        JobName.RESEARCH.value: handlers.handle_research,
        JobName.PATTERN_ANALYZE.value: handlers.handle_pattern_analyze,
        JobName.SCRIPT_GENERATE.value: handlers.handle_script_generate,
        JobName.SCRIPT_CRITIQUE.value: handlers.handle_script_critique,
        JobName.BRAND_PROPOSE.value: handlers.handle_brand_propose,
        JobName.MEDIA_PLAN.value: handlers.handle_media_plan,
        JobName.VOICE_SYNTH.value: handlers.handle_voice_synth,
        JobName.SUBTITLE_BUILD.value: handlers.handle_subtitle_build,
        JobName.VIDEO_RENDER.value: handlers.handle_video_render,
        JobName.QA_CHECK.value: handlers.handle_qa_check,
        JobName.PUBLISH.value: handlers.handle_publish,
        JobName.ANALYTICS_SNAPSHOT.value: handlers.handle_analytics_snapshot,
        JobName.LEARNING_UPDATE.value: handlers.handle_learning_update,
        JobName.REVENUE_SYNC.value: handlers.handle_revenue_sync,
        JobName.PIPELINE_ADVANCE.value: handlers.handle_pipeline_advance,
        JobName.STUCK_JOB_RECOVERY.value: handlers.handle_stuck_recovery,
        JobName.ACCEPTANCE_SEED.value: handlers.handle_acceptance_seed,
        JobName.DAILY_PLAN.value: handlers.handle_daily_plan,
        JobName.DAILY_REPORT.value: handlers.handle_daily_report,
        JobName.BOOTSTRAP_TICK.value: handlers.handle_bootstrap_tick,
        JobName.CALENDAR_TICK.value: handlers.handle_calendar_tick,
        JobName.ANALYTICS_SWEEP.value: handlers.handle_analytics_sweep,
    }
    if name not in mapping:
        raise KeyError(f"unknown job: {name}")
    return mapping[name]
