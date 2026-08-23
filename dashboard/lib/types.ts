export type SystemStatus = "running" | "degraded";

export type AnalyticsWindow = "24h" | "7d" | "30d" | "lifetime";

export type ConnectionState =
  | "not_configured"
  | "connection_required"
  | "connected"
  | "needs_reauthorization"
  | "needs_platform_review"
  | "ready"
  | "requires_human_action";

export type PublishBucket =
  | "queued"
  | "processing"
  | "published"
  | "failed"
  | "retry"
  | "awaiting";

export type JsonRecord = Record<string, unknown>;

export type Paginated<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type HealthComponent = {
  ok?: boolean;
  latency_ms?: number | null;
  path?: string | null;
  error?: string | null;
};

export type HealthResponse = {
  status?: string;
  db?: HealthComponent;
  redis?: HealthComponent;
  ffmpeg?: HealthComponent;
  dry_run?: boolean;
  worker?: {
    hint?: string;
    active_leases?: Record<string, number>;
  };
  queue?: {
    queued?: number;
    leased?: number;
    running?: number;
    failed?: number;
    dead?: number;
    retry_wait?: number;
    depth?: number;
  };
  budget?: {
    spent_today?: number | null;
    ai_spent_today?: number | null;
    media_spent_today?: number | null;
    daily_ai_spend_limit?: number | null;
    daily_media_spend_limit?: number | null;
    daily_cost_limit?: number | null;
    max_content_per_day?: number | null;
  };
};

export type DirectorDecision = {
  decision: string;
  reason: string | null;
  confidence: number | null;
  created_at: string | null;
  agent: string | null;
};

export type WinningTopic = {
  topic: string;
  score: number | null;
};

export type ActivityItem = {
  id: string;
  kind: "run" | "task" | "decision" | "event";
  agent: string | null;
  title: string;
  detail: string | null;
  status: string | null;
  created_at: string | null;
  duration_ms: number | null;
  content_id: string | null;
  simulation: boolean;
};

export type AccountActivation = {
  platform: string;
  state: string;
  blocked_reason: string | null;
  checkpoint_kind: string | null;
  selected_handle: string | null;
  ready: boolean;
  handoff_url?: string | null;
  handoff_opened?: boolean;
};

export type DailyReport = {
  local_date: string;
  timezone: string;
  status: string;
  headline: string;
  body: JsonRecord;
  finalized: boolean;
};

export type OverviewResponse = {
  system_status: SystemStatus | "action required";
  produced_today: number;
  published_today: number;
  rejected_today: number;
  views_today: number;
  views_7d: number;
  followers_7d: number;
  revenue_today: number | null;
  revenue_mtd: number | null;
  experiments_active: number;
  winning_topic: WinningTopic | null;
  director_decision: DirectorDecision | null;
  recent_activity: ActivityItem[];
  dry_run: boolean | null;
  simulation: boolean;
  autonomous_mode?: boolean;
  account_activation?: Record<string, AccountActivation>;
  daily_report?: DailyReport | null;
  human_actions?: HumanActionRow[];
  notifications?: JsonRecord[];
};

export type ContentPlatform = {
  platform: string;
  status: string;
  url: string | null;
  simulation: boolean;
};

export type ContentRow = {
  id: string;
  topic: string;
  niche: string | null;
  status: string;
  script: string | null;
  platform: string | null;
  platforms: ContentPlatform[];
  views: number | null;
  qa: string | null;
  simulation: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type TrendRow = {
  id: string;
  source: string;
  topic: string;
  title: string;
  url: string | null;
  trend_score: number | null;
  velocity: number | null;
  engagement_rate: number | null;
  risk_score: number | null;
  opportunity_score: number | null;
  opportunity_status: string | null;
  opportunity_approved: boolean | null;
  opportunity_explanation: string | null;
  age_hours: number | null;
  simulation: boolean;
  observed_at: string | null;
};

export type Allocation = {
  id: string;
  niche: string;
  allocation: number;
  reason: string;
  active: boolean;
  decided_by: string | null;
};

export type LearningRec = {
  id: string;
  recommendation: string;
  method: string | null;
  confidence: number | null;
  consumed: boolean;
  created_at: string | null;
};

export type ExperimentRow = {
  id: string;
  name: string;
  status: string;
  locked: boolean;
  dimensions: JsonRecord;
  results: JsonRecord | null;
};

export type StrategyResponse = {
  allocations: Allocation[];
  learning_recommendations: LearningRec[];
  experiments: ExperimentRow[];
};

export type AnalyticsSeriesPoint = {
  ts: string;
  views: number;
  likes: number;
  comments: number;
  shares: number;
  followers_gained: number;
};

export type PlatformMetric = {
  platform: string;
  views: number;
  likes: number;
  comments: number;
  shares: number;
  publications: number;
  followers_gained: number;
  simulation: boolean;
};

export type DistributionStat = {
  median: number | null;
  p75: number | null;
  p90: number | null;
  p95: number | null;
  max: number | null;
  count: number;
};

export type AnalyticsResponse = {
  window: AnalyticsWindow;
  totals: {
    views: number;
    likes: number;
    comments: number;
    shares: number;
    followers_gained: number;
    watch_time_seconds: number;
    publications: number;
  };
  series: AnalyticsSeriesPoint[];
  platforms: PlatformMetric[];
  distributions: Record<string, DistributionStat>;
  performance_classes: Record<string, number>;
  simulation: boolean;
};

export type RevenueItem = {
  id: string;
  kind: "actual" | "forecast";
  amount: number;
  currency: string;
  source: string;
  platform: string | null;
  period: string | null;
  content_id: string | null;
  simulation: boolean;
  created_at: string | null;
};

export type RevenuePlatform = {
  platform: string;
  amount: number;
  count: number;
  simulation: boolean;
};

export type RevenueBucket = {
  today: number | null;
  mtd: number | null;
  lifetime: number | null;
  total: number | null;
  has_data: boolean;
  items: RevenueItem[];
  by_platform: RevenuePlatform[];
};

export type RevenueResponse = {
  currency: string;
  actual: RevenueBucket;
  forecast: RevenueBucket;
};

export type PublishingCounts = Record<PublishBucket, number>;

export type PublishingRow = {
  id: string;
  content_id: string | null;
  title: string | null;
  platform: string;
  status: string;
  bucket: PublishBucket;
  url: string | null;
  error: string | null;
  simulation: boolean;
  created_at: string | null;
};

export type PublishingResponse = Paginated<PublishingRow> & {
  counts: PublishingCounts;
};

export type PlatformConnection = {
  platform: string;
  state: ConnectionState;
  account_label: string | null;
  checklist: string[];
};

export type BootstrapResponse = {
  platforms: PlatformConnection[];
  human_checklist: string[];
  human_actions: HumanActionRow[];
  production_accounts_connected: boolean;
  message: string | null;
  activation?: Record<string, AccountActivation>;
  brand?: JsonRecord | null;
};

export type HumanActionRow = {
  id: string;
  title: string;
  instructions: string;
  category: string;
  status: string;
  platform: string | null;
  blocking: boolean;
  created_at: string | null;
};

export type RunCycleResult = {
  accepted: boolean;
  job_id: string | null;
  status: string | null;
  message: string;
  correlation_id: string | null;
  blocked: boolean;
};
