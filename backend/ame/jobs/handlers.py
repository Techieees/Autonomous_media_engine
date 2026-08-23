from sqlalchemy.ext.asyncio import AsyncSession

from ame.agents.brand import handle_brand_propose
from ame.agents.director import handle_director_tick, handle_pipeline_advance
from ame.agents.niche_scout import handle_niche_evaluate
from ame.agents.pattern_analyst import handle_pattern_analyze
from ame.agents.research import handle_research
from ame.agents.script_critic import handle_script_critique
from ame.agents.script_writer import handle_script_generate
from ame.analytics.service import handle_analytics_snapshot
from ame.cli.acceptance import handle_acceptance_seed
from ame.db.models import Job
from ame.learning.engine import handle_learning_update
from ame.media.director import handle_media_plan
from ame.media.renderer import handle_video_render
from ame.media.subtitles import handle_subtitle_build
from ame.media.voice import handle_voice_synth
from ame.publishers.service import handle_publish
from ame.ops.analytics_sweep import handle_analytics_sweep
from ame.ops.calendar import handle_calendar_tick
from ame.ops.daily_plan import handle_daily_plan
from ame.ops.reports import handle_daily_report
from ame.ops.watchdog import handle_watchdog
from ame.bootstrap.orchestrator import handle_bootstrap_tick
from ame.qa.service import handle_qa_check
from ame.revenue.service import handle_revenue_sync
from ame.scoring.service import handle_opportunity_score
from ame.trends.service import handle_trend_ingest


async def handle_stuck_recovery(session: AsyncSession, job: Job) -> None:
    await handle_watchdog(session, job)


__all__ = [
    "handle_acceptance_seed",
    "handle_analytics_snapshot",
    "handle_brand_propose",
    "handle_director_tick",
    "handle_learning_update",
    "handle_media_plan",
    "handle_niche_evaluate",
    "handle_opportunity_score",
    "handle_pattern_analyze",
    "handle_pipeline_advance",
    "handle_publish",
    "handle_qa_check",
    "handle_research",
    "handle_revenue_sync",
    "handle_script_critique",
    "handle_script_generate",
    "handle_stuck_recovery",
    "handle_subtitle_build",
    "handle_trend_ingest",
    "handle_video_render",
    "handle_voice_synth",
    "handle_daily_plan",
    "handle_daily_report",
    "handle_bootstrap_tick",
    "handle_calendar_tick",
    "handle_analytics_sweep",
]
