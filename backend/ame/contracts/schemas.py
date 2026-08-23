from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ame.contracts.enums import (
    AgentName,
    AgentRunStatus,
    ClaimKind,
    ContentStatus,
    QAVerdict,
    RevenueKind,
)


class AgentInput(BaseModel):
    task_id: UUID
    agent: AgentName
    payload: dict[str, Any] = Field(default_factory=dict)
    content_id: UUID | None = None
    correlation_id: str | None = None
    workflow_id: UUID | None = None


class AgentContext(BaseModel):
    content_id: UUID | None = None
    status: ContentStatus | None = None
    dry_run: bool = True
    simulation: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    decision: str
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    expected_effect: str | None = None
    related_entity_type: str | None = None
    related_entity_id: UUID | None = None


class AgentResult(BaseModel):
    status: AgentRunStatus
    output: dict[str, Any] = Field(default_factory=dict)
    decision: AgentDecision | None = None
    events: list[str] = Field(default_factory=list)
    error: str | None = None


class TrendSignalIn(BaseModel):
    source: str
    external_id: str
    topic: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    velocity: float = 0.0
    engagement_rate: float = 0.0
    age_hours: float = 0.0
    cross_platform_count: int = 1
    source_authority: float = 0.5
    risk_score: float = 0.1
    trend_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityScore(BaseModel):
    trend_signal_id: UUID
    score: float
    velocity_score: float
    recency_score: float
    engagement_score: float
    corroboration_score: float
    novelty_score: float
    shelf_life_score: float
    relevance_score: float
    factual_confidence: float
    cost_score: float
    copyright_risk: float
    explanation: str
    features: dict[str, Any] = Field(default_factory=dict)


class FactClaim(BaseModel):
    claim: str
    kind: ClaimKind
    sources: list[str] = Field(default_factory=list)
    freshness_checked: bool = False
    stale: bool = False
    publishable: bool = True


class ResearchPackOut(BaseModel):
    topic: str
    summary: str
    claims: list[FactClaim]
    source_urls: list[str]
    uncertain_claims: list[str] = Field(default_factory=list)
    unsuitable_claims: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class ScriptCandidate(BaseModel):
    hook: str
    body: str
    reveal: str
    cta: str
    estimated_duration: int
    on_screen_text: list[str] = Field(default_factory=list)
    scene_plan: list[dict[str, Any]] = Field(default_factory=list)
    voice_style: str = "clear_authoritative"
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    claims: list[FactClaim] = Field(default_factory=list)


class ScriptCritique(BaseModel):
    script_id: UUID
    hook: float
    clarity: float
    originality: float
    retention_potential: float
    factual_confidence: float
    platform_suitability: float
    production_feasibility: float
    brand_fit: float
    policy_risk: float
    repetition: float
    total: float
    selected: bool
    notes: str


class ProductionManifest(BaseModel):
    template_id: str = "vertical_clean_v1"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    scenes: list[dict[str, Any]]
    voiceover_path: str | None = None
    subtitle_path: str | None = None
    music_path: str | None = None
    thumbnail_path: str | None = None
    assets: list[dict[str, Any]] = Field(default_factory=list)


class QAResultOut(BaseModel):
    verdict: QAVerdict
    checks: dict[str, Any]
    reasons: list[str] = Field(default_factory=list)


class RevenueEventIn(BaseModel):
    kind: RevenueKind
    amount: float
    currency: str = "EUR"
    source: str
    platform: str | None = None
    content_id: UUID | None = None
    period: str | None = None


class CostEventIn(BaseModel):
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    job: str | None = None
    agent: str | None = None
    content_id: UUID | None = None
