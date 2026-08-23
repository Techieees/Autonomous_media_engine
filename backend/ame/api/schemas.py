from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PageMeta(BaseModel):
    items: list[Any]
    limit: int
    offset: int
    total: int


class HealthComponent(BaseModel):
    ok: bool
    latency_ms: float | None = None
    path: str | None = None
    error: str | None = None


class BudgetHealth(BaseModel):
    spent_today: float
    ai_spent_today: float
    media_spent_today: float
    daily_ai_spend_limit: float
    daily_media_spend_limit: float
    daily_cost_limit: float
    max_content_per_day: int


class QueueHealth(BaseModel):
    queued: int
    leased: int
    running: int
    retry_wait: int
    dead: int
    depth: int


class WorkerHealth(BaseModel):
    hint: str
    active_leases: dict[str, int] = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str
    db: HealthComponent
    redis: HealthComponent
    ffmpeg: HealthComponent
    dry_run: bool
    budget: BudgetHealth
    queue: QueueHealth
    worker: WorkerHealth


class DirectorDecisionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    agent: str
    decision: str
    reason: str
    confidence: float
    expected_effect: str | None = None
    created_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class OverviewOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    produced_today: int
    published_today: int
    rejected_today: int
    views_today: int | None = None
    views_7d: int | None = None
    followers_7d: int | None = None
    revenue_today: float | None = None
    revenue_mtd: float | None = None
    experiments_active: int
    winning_topic: str | None = None
    director_decision: Any = None
    system_status: str
    dry_run: bool = True
    simulation: Any = False


class ScriptSummary(BaseModel):
    id: UUID
    hook: str
    candidate_label: str
    selected: bool = False


class PlatformPublicationOut(BaseModel):
    platform: str
    status: str
    url: str | None = None
    simulation: bool = False


class QASummary(BaseModel):
    verdict: str
    reasons: list[Any] = Field(default_factory=list)


class ContentRowOut(BaseModel):
    id: UUID
    topic: str
    niche: str | None
    status: str
    script: ScriptSummary | None
    platforms: list[PlatformPublicationOut]
    views: int
    qa: QASummary | None
    simulation: bool
    created_at: datetime


class OpportunitySummary(BaseModel):
    id: UUID
    score: float
    status: str
    approved: bool
    explanation: str
    simulation: bool = False


class TrendRowOut(BaseModel):
    id: UUID
    source: str
    topic: str
    title: str
    url: str | None
    trend_score: float
    velocity: float
    engagement_rate: float
    opportunity: OpportunitySummary | None
    simulation: bool
    observed_at: datetime
    created_at: datetime


class AgentRunOut(BaseModel):
    id: UUID
    agent: str
    status: str
    duration_ms: int | None
    error: str | None
    content_id: UUID | None
    task_id: UUID | None
    created_at: datetime


class AgentTaskOut(BaseModel):
    id: UUID
    agent: str
    status: str
    parent_task_id: UUID | None
    content_id: UUID | None
    created_at: datetime


class AgentDecisionOut(BaseModel):
    id: UUID
    agent: str
    decision: str
    reason: str
    confidence: float
    expected_effect: str | None
    content_id: UUID | None
    created_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class AgentsOut(BaseModel):
    runs: list[AgentRunOut]
    tasks: list[AgentTaskOut]
    decisions: list[AgentDecisionOut]
    limit: int
    offset: int
    totals: dict[str, int]


class AllocationOut(BaseModel):
    id: UUID
    niche: str
    allocation: float
    reason: str
    active: bool
    decided_by: str
    created_at: datetime


class ExperimentOut(BaseModel):
    id: UUID
    name: str
    status: str
    locked: bool
    dimensions: dict[str, Any]
    created_at: datetime


class RecommendationOut(BaseModel):
    id: UUID
    recommendation: str
    method: str
    confidence: float
    consumed: bool
    features: dict[str, Any]
    created_at: datetime


class StrategyOut(BaseModel):
    allocations: list[AllocationOut]
    experiments: list[ExperimentOut]
    recommendations: list[RecommendationOut]
    limit: int
    offset: int


class DistributionOut(BaseModel):
    median: float | None = None
    p75: float | None = None
    p90: float | None = None
    p95: float | None = None
    max: float | None = None
    count: int = 0


class AnalyticsOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    window: str
    totals: dict[str, Any] | None = None
    distributions: dict[str, Any] | None = None
    by_platform: list[dict[str, Any]] | None = None
    performance_classes: dict[str, int] | None = None
    actual: dict[str, Any] | None = None
    simulation: Any = False


class RevenueBucketOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    today: float | None = None
    mtd: float | None = None
    lifetime: float | None = None
    by_platform: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    has_data: bool = False


class RevenueOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    actual: RevenueBucketOut
    forecast: RevenueBucketOut
    currency: str | None = None
    simulation: Any = False


class PublishingCounts(BaseModel):
    queued: int = 0
    processing: int = 0
    published: int = 0
    failed: int = 0
    retry: int = 0
    awaiting: int = 0


class PublishingJobOut(BaseModel):
    id: UUID
    content_id: UUID
    platform: str
    status: str
    error: str | None
    simulation: bool
    created_at: datetime


class PublishingOut(BaseModel):
    counts: PublishingCounts
    items: list[PublishingJobOut]
    limit: int
    offset: int
    total: int


class ConnectionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID | None = None
    platform: str
    state: str
    account_label: str | None = None
    scopes: list[Any] = Field(default_factory=list)
    expires_at: datetime | None = None
    has_access_token: bool = False
    has_refresh_token: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class HumanActionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    title: str
    instructions: str
    category: str
    status: str
    platform: str | None = None
    blocking: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BootstrapOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    connections: list[ConnectionOut]
    human_actions: list[HumanActionOut]
    production_accounts_connected: bool
    message: str


class RunCycleOut(BaseModel):
    job_ids: dict[str, str]
    jobs: list[dict[str, Any]]
    correlation_id: str
    blocked: bool = False


class EventOut(BaseModel):
    id: UUID
    name: str
    payload: dict[str, Any]
    correlation_id: str | None
    workflow_id: UUID | None
    content_id: UUID | None
    agent_run_id: UUID | None
    simulation: bool
    created_at: datetime


class EventsOut(BaseModel):
    items: list[EventOut]
    limit: int
    offset: int
    total: int


class OAuthRequiredOut(BaseModel):
    state: str
    instructions: str
    platform: str | None = None
