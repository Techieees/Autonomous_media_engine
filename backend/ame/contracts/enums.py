from enum import StrEnum


class ContentStatus(StrEnum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    APPROVED_FOR_RESEARCH = "approved_for_research"
    RESEARCHED = "researched"
    SCRIPTING = "scripting"
    SCRIPT_SELECTED = "script_selected"
    PRODUCTION = "production"
    QA = "qa"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    MEASURING = "measuring"
    LEARNING_COMPLETE = "learning_complete"
    REJECTED = "rejected"
    FAILED = "failed"
    PAUSED_BY_BUDGET = "paused_by_budget"
    AWAITING_HUMAN = "awaiting_human"
    AWAITING_PLATFORM_APPROVAL = "awaiting_platform_approval"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    DEAD = "dead"
    CANCELLED = "cancelled"


class AgentName(StrEnum):
    DIRECTOR = "director"
    NICHE_SCOUT = "niche_scout"
    TREND_SCOUT = "trend_scout"
    OPPORTUNITY_SCORING = "opportunity_scoring"
    RESEARCH = "research"
    PATTERN_ANALYST = "pattern_analyst"
    SCRIPT_WRITER = "script_writer"
    SCRIPT_CRITIC = "script_critic"
    BRAND = "brand"
    MEDIA_DIRECTOR = "media_director"
    VIDEO_FACTORY = "video_factory"
    VOICE = "voice"
    SUBTITLE = "subtitle"
    QA = "qa"
    ANALYTICS = "analytics"
    REVENUE = "revenue"
    LEARNING = "learning"
    SCHEDULER = "scheduler"
    ACCOUNT_BOOTSTRAP = "account_bootstrap"


class AgentRunStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BUDGET_BLOCKED = "budget_blocked"


class ConnectionState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONNECTION_REQUIRED = "connection_required"
    CONNECTED = "connected"
    NEEDS_REAUTHORIZATION = "needs_reauthorization"
    NEEDS_PLATFORM_REVIEW = "needs_platform_review"
    READY = "ready"
    REQUIRES_HUMAN_ACTION = "requires_human_action"


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    DRY_RUN = "dry_run"


class PublishStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    RETRY = "retry"
    CONNECTION_REQUIRED = "connection_required"
    REQUIRES_HUMAN_ACTION = "requires_human_action"
    AWAITING_PLATFORM_REQUIRED_APPROVAL = "awaiting_platform_required_approval"
    REJECTED_SIMULATION = "rejected_simulation"


class QAVerdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRES_REVIEW = "requires_review"


class ClaimKind(StrEnum):
    VERIFIED_FACT = "verified_fact"
    REASONABLE_INTERPRETATION = "reasonable_interpretation"
    UNCERTAIN = "uncertain"
    PREDICTION = "prediction"
    OPINION = "opinion"


class RevenueKind(StrEnum):
    ACTUAL = "actual"
    FORECAST = "forecast"


class PerformanceClass(StrEnum):
    BASELINE = "baseline"
    GOOD = "good"
    STRONG = "strong"
    BREAKOUT = "breakout"
    VIRAL = "viral"


class HumanActionStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class HumanActionClass(StrEnum):
    GENUINELY_HUMAN_REQUIRED = "genuinely_human_required"
    AUTOMATABLE = "automatable"
    TECHNICAL_FAILURE = "technical_failure"


class AgentMessageType(StrEnum):
    PROPOSAL = "proposal"
    ASSIGNMENT = "assignment"
    RESULT = "result"
    REVIEW_REQUEST = "review_request"
    REVIEW_RESULT = "review_result"
    WARNING = "warning"
    HANDOFF = "handoff"
    RECOMMENDATION = "recommendation"


class AccountBootstrapState(StrEnum):
    PLANNING = "planning"
    BRAND_READY = "brand_ready"
    SIGNUP_PREPARED = "signup_prepared"
    SIGNUP_IN_PROGRESS = "signup_in_progress"
    HUMAN_VERIFICATION_REQUIRED = "human_verification_required"
    AWAITING_EXTERNAL_CONFIRMATION = "awaiting_external_confirmation"
    ACCOUNT_CREATED = "account_created"
    PROFILE_CONFIGURING = "profile_configuring"
    PROFILE_READY = "profile_ready"
    DEVELOPER_APP_REQUIRED = "developer_app_required"
    DEVELOPER_APP_READY = "developer_app_ready"
    OAUTH_REQUIRED = "oauth_required"
    OAUTH_IN_PROGRESS = "oauth_in_progress"
    CONNECTED = "connected"
    READY = "ready"
    PLATFORM_REVIEW_REQUIRED = "platform_review_required"
    RESTRICTED = "restricted"
    FAILED_RETRYABLE = "failed_retryable"


class NotificationKind(StrEnum):
    DAILY_REPORT_READY = "daily_report_ready"
    HUMAN_ACTION_REQUIRED = "human_action_required"
    PLATFORM_DISCONNECTED = "platform_disconnected"
    BUDGET_PAUSED = "budget_paused"
    CRITICAL_SYSTEM_FAILURE = "critical_system_failure"
    MAJOR_BREAKOUT_CONTENT = "major_breakout_content"


class DailyPlanStatus(StrEnum):
    ACTIVE = "active"
    FINALIZED = "finalized"


class CalendarSlotStatus(StrEnum):
    PLANNED = "planned"
    DUE = "due"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobName(StrEnum):
    DIRECTOR_TICK = "director.tick"
    TREND_INGEST = "trend.ingest"
    NICHE_EVALUATE = "niche.evaluate"
    OPPORTUNITY_SCORE = "opportunity.score"
    RESEARCH = "research.run"
    PATTERN_ANALYZE = "pattern.analyze"
    SCRIPT_GENERATE = "script.generate"
    SCRIPT_CRITIQUE = "script.critique"
    BRAND_PROPOSE = "brand.propose"
    MEDIA_PLAN = "media.plan"
    VOICE_SYNTH = "voice.synth"
    SUBTITLE_BUILD = "subtitle.build"
    VIDEO_RENDER = "video.render"
    QA_CHECK = "qa.check"
    PUBLISH = "publish.run"
    ANALYTICS_SNAPSHOT = "analytics.snapshot"
    LEARNING_UPDATE = "learning.update"
    REVENUE_SYNC = "revenue.sync"
    PIPELINE_ADVANCE = "pipeline.advance"
    STUCK_JOB_RECOVERY = "ops.stuck_recovery"
    ACCEPTANCE_SEED = "ops.acceptance_seed"
    DAILY_PLAN = "ops.daily_plan"
    DAILY_REPORT = "ops.daily_report"
    BOOTSTRAP_TICK = "ops.bootstrap_tick"
    CALENDAR_TICK = "ops.calendar_tick"
    ANALYTICS_SWEEP = "analytics.sweep"


class MetricCheckpoint(StrEnum):
    H1 = "1h"
    H6 = "6h"
    H24 = "24h"
    H72 = "72h"
    D7 = "7d"
    D30 = "30d"
