import { publishBucket } from "./status";
import type {
  ActivityItem,
  Allocation,
  AnalyticsResponse,
  AnalyticsSeriesPoint,
  AnalyticsWindow,
  BootstrapResponse,
  ConnectionState,
  ContentRow,
  DirectorDecision,
  ExperimentRow,
  HealthResponse,
  HumanActionRow,
  JsonRecord,
  DistributionStat,
  LearningRec,
  OverviewResponse,
  Paginated,
  PlatformConnection,
  PlatformMetric,
  PublishingCounts,
  PublishingResponse,
  PublishingRow,
  RevenueBucket,
  RevenueItem,
  RevenuePlatform,
  RevenueResponse,
  RunCycleResult,
  StrategyResponse,
  SystemStatus,
  TrendRow,
  WinningTopic,
} from "./types";

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown, fallback = ""): string {
  if (value === null || value === undefined) return fallback;
  return String(value);
}

function strOrNull(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

function num(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function numOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function bool(value: unknown, fallback = false): boolean {
  if (typeof value === "boolean") return value;
  if (value === "true" || value === 1) return true;
  if (value === "false" || value === 0) return false;
  return fallback;
}

function pick<T>(record: JsonRecord, keys: string[]): T | undefined {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) {
      return record[key] as T;
    }
  }
  return undefined;
}

export function asPaginated<T>(
  data: unknown,
  mapItem: (row: unknown, index: number) => T,
  limit = 50,
  offset = 0,
): Paginated<T> {
  if (Array.isArray(data)) {
    return {
      items: data.map(mapItem),
      total: data.length,
      limit,
      offset,
    };
  }
  if (!isRecord(data)) {
    return { items: [], total: 0, limit, offset };
  }
  const rawItems = pick<unknown>(data, ["items", "results", "rows", "data"]);
  const items = asArray(rawItems).map(mapItem);
  return {
    items,
    total: num(pick(data, ["total", "count"]), items.length),
    limit: num(data.limit, limit),
    offset: num(data.offset, offset),
  };
}

function healthComponent(value: unknown): HealthResponse["db"] {
  if (typeof value === "boolean") return { ok: value };
  if (!isRecord(value)) return {};
  return {
    ok: bool(value.ok ?? value.available, false),
    latency_ms: numOrNull(value.latency_ms),
    path: strOrNull(value.path),
    error: strOrNull(value.error),
  };
}

export function normalizeHealth(data: unknown): HealthResponse {
  if (!isRecord(data)) return {};
  const worker = isRecord(data.worker) ? data.worker : {};
  const queue = isRecord(data.queue) ? data.queue : {};
  const budget = isRecord(data.budget) ? data.budget : {};
  const leases = isRecord(worker.active_leases)
    ? Object.fromEntries(
        Object.entries(worker.active_leases).map(([k, v]) => [k, num(v)]),
      )
    : {};
  return {
    status: strOrNull(data.status) ?? undefined,
    db: healthComponent(data.db),
    redis: healthComponent(data.redis),
    ffmpeg: healthComponent(data.ffmpeg),
    dry_run: typeof data.dry_run === "boolean" ? data.dry_run : undefined,
    worker: {
      hint: strOrNull(worker.hint) ?? undefined,
      active_leases: leases,
    },
    queue: {
      queued: numOrNull(queue.queued) ?? undefined,
      leased: numOrNull(queue.leased) ?? undefined,
      running: numOrNull(queue.running) ?? undefined,
      failed: numOrNull(queue.failed) ?? undefined,
      dead: numOrNull(queue.dead) ?? undefined,
      retry_wait: numOrNull(queue.retry_wait) ?? undefined,
      depth: numOrNull(queue.depth) ?? undefined,
    },
    budget: {
      spent_today: numOrNull(budget.spent_today),
      ai_spent_today: numOrNull(budget.ai_spent_today ?? budget.daily_ai_spend),
      media_spent_today: numOrNull(
        budget.media_spent_today ?? budget.daily_media_spend,
      ),
      daily_ai_spend_limit: numOrNull(
        budget.daily_ai_spend_limit ?? budget.daily_ai_limit,
      ),
      daily_media_spend_limit: numOrNull(
        budget.daily_media_spend_limit ?? budget.daily_media_limit,
      ),
      daily_cost_limit: numOrNull(budget.daily_cost_limit),
      max_content_per_day: numOrNull(budget.max_content_per_day),
    },
  };
}

function normalizeWinningTopic(value: unknown): WinningTopic | null {
  if (!value) return null;
  if (typeof value === "string") return { topic: value, score: null };
  if (!isRecord(value)) return null;
  const topic = strOrNull(value.topic ?? value.name ?? value.title);
  if (!topic) return null;
  return { topic, score: numOrNull(value.score) };
}

function normalizeDirector(value: unknown): DirectorDecision | null {
  if (!value) return null;
  if (typeof value === "string") {
    return {
      decision: value,
      reason: null,
      confidence: null,
      created_at: null,
      agent: "director",
    };
  }
  if (!isRecord(value)) return null;
  const decision = strOrNull(value.decision ?? value.title ?? value.action);
  if (!decision) return null;
  return {
    decision,
    reason: strOrNull(value.reason ?? value.explanation),
    confidence: numOrNull(value.confidence),
    created_at: strOrNull(value.created_at),
    agent: strOrNull(value.agent) ?? "director",
  };
}

export function normalizeActivity(row: unknown, index = 0): ActivityItem {
  if (typeof row === "string") {
    return {
      id: `evt-${index}`,
      kind: "event",
      agent: null,
      title: row,
      detail: null,
      status: null,
      created_at: null,
      duration_ms: null,
      content_id: null,
      simulation: false,
    };
  }
  if (!isRecord(row)) {
    return {
      id: `evt-${index}`,
      kind: "event",
      agent: null,
      title: "Unknown event",
      detail: null,
      status: null,
      created_at: null,
      duration_ms: null,
      content_id: null,
      simulation: false,
    };
  }
  const kindRaw = str(row.kind ?? row.type, "event");
  const kind: ActivityItem["kind"] =
    kindRaw === "run" || kindRaw === "task" || kindRaw === "decision"
      ? kindRaw
      : "event";
  const title =
    strOrNull(row.title ?? row.decision ?? row.name ?? row.agent) ?? "Event";
  return {
    id: str(row.id, `${kind}-${index}`),
    kind,
    agent: strOrNull(row.agent ?? row.from_agent),
    title,
    detail: strOrNull(
      row.detail ?? row.reason ?? row.error ?? row.message ?? row.status,
    ),
    status: strOrNull(row.status),
    created_at: strOrNull(row.created_at ?? row.observed_at),
    duration_ms: numOrNull(row.duration_ms),
    content_id: strOrNull(row.content_id),
    simulation: bool(row.simulation, false),
  };
}

export function normalizeOverview(data: unknown): OverviewResponse {
  const record = isRecord(data) ? data : {};
  const statusRaw = str(record.system_status ?? record.status, "degraded").toLowerCase();
  const system_status: SystemStatus =
    statusRaw === "running" || statusRaw === "ok" || statusRaw === "healthy"
      ? "running"
      : "degraded";

  const activitySource =
    record.recent_activity ?? record.activity ?? record.recent_agent_activity;
  const recent_activity = asArray(activitySource).map(normalizeActivity);

  return {
    system_status,
    produced_today: num(record.produced_today),
    published_today: num(record.published_today),
    rejected_today: num(record.rejected_today),
    views_today: num(record.views_today),
    views_7d: num(record.views_7d),
    followers_7d: num(record.followers_7d),
    revenue_today: numOrNull(record.revenue_today),
    revenue_mtd: numOrNull(record.revenue_mtd),
    experiments_active: num(record.experiments_active),
    winning_topic: normalizeWinningTopic(record.winning_topic),
    director_decision: normalizeDirector(record.director_decision),
    recent_activity,
    dry_run: typeof record.dry_run === "boolean" ? record.dry_run : null,
    simulation: bool(record.simulation, false),
    autonomous_mode: bool(record.autonomous_mode, true),
    account_activation: isRecord(record.account_activation)
      ? (record.account_activation as OverviewResponse["account_activation"])
      : {},
    daily_report: isRecord(record.daily_report)
      ? {
          local_date: str(record.daily_report.local_date),
          timezone: str(record.daily_report.timezone),
          status: str(record.daily_report.status),
          headline: str(record.daily_report.headline),
          body: isRecord(record.daily_report.body) ? record.daily_report.body : {},
          finalized: bool(record.daily_report.finalized, false),
        }
      : null,
    human_actions: asArray(record.human_actions).map(normalizeHumanAction),
    notifications: asArray(record.notifications).filter(isRecord),
  };
}

export function normalizeContent(row: unknown, index = 0): ContentRow {
  const r = isRecord(row) ? row : {};
  const scriptObj = isRecord(r.script) ? r.script : null;
  const script =
    strOrNull(r.script_hook ?? r.hook ?? r.script_preview) ??
    (scriptObj ? strOrNull(scriptObj.hook ?? scriptObj.body) : null) ??
    (typeof r.script === "string" ? r.script : null);
  const qaObj = isRecord(r.qa) ? r.qa : null;
  const platforms = asArray(r.platforms).map((item) => {
    const p = isRecord(item) ? item : {};
    return {
      platform: str(p.platform, "unknown"),
      status: str(p.status, "unknown"),
      url: strOrNull(p.url),
      simulation: bool(p.simulation, false),
    };
  });
  return {
    id: str(r.id, `content-${index}`),
    topic: str(r.topic, "Untitled"),
    niche: strOrNull(r.niche),
    status: str(r.status, "unknown"),
    script,
    platform: strOrNull(r.platform ?? r.primary_platform ?? platforms[0]?.platform),
    platforms,
    views: numOrNull(r.views),
    qa: strOrNull(r.qa_verdict ?? qaObj?.verdict ?? (typeof r.qa === "string" ? r.qa : null)),
    simulation: bool(r.simulation, false),
    created_at: strOrNull(r.created_at),
    updated_at: strOrNull(r.updated_at),
  };
}

export function normalizeTrend(row: unknown, index = 0): TrendRow {
  const r = isRecord(row) ? row : {};
  const opp = isRecord(r.opportunity) ? r.opportunity : null;
  return {
    id: str(r.id, `trend-${index}`),
    source: str(r.source, "unknown"),
    topic: str(r.topic, "—"),
    title: str(r.title, str(r.topic, "Untitled")),
    url: strOrNull(r.url),
    trend_score: numOrNull(r.trend_score ?? r.score),
    velocity: numOrNull(r.velocity),
    engagement_rate: numOrNull(r.engagement_rate),
    risk_score: numOrNull(r.risk_score),
    opportunity_score: numOrNull(
      r.opportunity_score ?? opp?.score ?? r.decision_score,
    ),
    opportunity_status: strOrNull(
      r.opportunity_status ?? opp?.status ?? r.decision,
    ),
    opportunity_approved:
      typeof (r.opportunity_approved ?? opp?.approved) === "boolean"
        ? Boolean(r.opportunity_approved ?? opp?.approved)
        : null,
    opportunity_explanation: strOrNull(
      r.opportunity_explanation ?? opp?.explanation ?? r.explanation,
    ),
    age_hours: numOrNull(r.age_hours),
    simulation: bool(r.simulation, false),
    observed_at: strOrNull(r.observed_at ?? r.created_at),
  };
}

export function normalizeAgents(data: unknown): ActivityItem[] {
  if (Array.isArray(data)) return data.map(normalizeActivity);
  if (!isRecord(data)) return [];
  if (Array.isArray(data.items) || Array.isArray(data.activity)) {
    return asArray(data.items ?? data.activity).map(normalizeActivity);
  }
  const rows: ActivityItem[] = [];
  asArray(data.runs).forEach((row, i) => {
    const r = isRecord(row) ? row : {};
    rows.push(
      normalizeActivity(
        {
          ...r,
          kind: "run",
          title: r.agent ?? "run",
          detail: r.error ?? r.status,
        },
        i,
      ),
    );
  });
  asArray(data.tasks).forEach((row, i) => {
    const r = isRecord(row) ? row : {};
    rows.push(
      normalizeActivity(
        { ...r, kind: "task", title: r.agent ?? "task" },
        1000 + i,
      ),
    );
  });
  asArray(data.decisions).forEach((row, i) => {
    const r = isRecord(row) ? row : {};
    rows.push(
      normalizeActivity(
        {
          ...r,
          kind: "decision",
          title: r.decision ?? r.agent ?? "decision",
          detail: r.reason,
        },
        2000 + i,
      ),
    );
  });
  return rows.sort((a, b) => {
    const ta = a.created_at ? Date.parse(a.created_at) : 0;
    const tb = b.created_at ? Date.parse(b.created_at) : 0;
    return tb - ta;
  });
}

export function normalizeStrategy(data: unknown): StrategyResponse {
  const r = isRecord(data) ? data : {};
  const allocations = asArray(
    r.allocations ?? r.strategy_allocations,
  ).map((row, i): Allocation => {
    const a = isRecord(row) ? row : {};
    return {
      id: str(a.id, `alloc-${i}`),
      niche: str(a.niche, "unknown"),
      allocation: num(a.allocation),
      reason: str(a.reason, ""),
      active: bool(a.active, true),
      decided_by: strOrNull(a.decided_by),
    };
  });
  const learning_recommendations = asArray(
    r.learning_recommendations ?? r.recommendations,
  ).map((row, i): LearningRec => {
    const rec = isRecord(row) ? row : {};
    return {
      id: str(rec.id, `rec-${i}`),
      recommendation: str(rec.recommendation ?? rec.text, ""),
      method: strOrNull(rec.method),
      confidence: numOrNull(rec.confidence),
      consumed: bool(rec.consumed, false),
      created_at: strOrNull(rec.created_at),
    };
  });
  const experiments = asArray(r.experiments ?? r.experiment_results).map(
    (row, i): ExperimentRow => {
      const e = isRecord(row) ? row : {};
      return {
        id: str(e.id, `exp-${i}`),
        name: str(e.name, "Untitled experiment"),
        status: str(e.status, "unknown"),
        locked: bool(e.locked, false),
        dimensions: isRecord(e.dimensions) ? e.dimensions : {},
        results: isRecord(e.results)
          ? e.results
          : isRecord(e.result)
            ? e.result
            : null,
      };
    },
  );
  return { allocations, learning_recommendations, experiments };
}

function emptyAnalytics(window: AnalyticsWindow): AnalyticsResponse {
  return {
    window,
    totals: {
      views: 0,
      likes: 0,
      comments: 0,
      shares: 0,
      followers_gained: 0,
      watch_time_seconds: 0,
      publications: 0,
    },
    series: [],
    platforms: [],
    distributions: {},
    performance_classes: {},
    simulation: false,
  };
}

export function normalizeAnalytics(
  data: unknown,
  window: AnalyticsWindow,
): AnalyticsResponse {
  if (!isRecord(data)) return emptyAnalytics(window);
  const totalsSrc = isRecord(data.totals)
    ? data.totals
    : isRecord(data.metrics)
      ? data.metrics
      : data;
  const series = asArray(data.series ?? data.timeline ?? data.points).map(
    (row, i): AnalyticsSeriesPoint => {
      const p = isRecord(row) ? row : {};
      return {
        ts: str(p.ts ?? p.timestamp ?? p.date ?? p.checkpoint, `p${i}`),
        views: num(p.views),
        likes: num(p.likes),
        comments: num(p.comments),
        shares: num(p.shares),
        followers_gained: num(p.followers_gained ?? p.followers),
      };
    },
  );
  const platforms = asArray(
    data.platforms ?? data.by_platform ?? data.platform_comparison,
  ).map((row, i): PlatformMetric => {
    const p = isRecord(row) ? row : {};
    return {
      platform: str(p.platform ?? p.name, `platform-${i}`),
      views: num(p.views),
      likes: num(p.likes),
      comments: num(p.comments),
      shares: num(p.shares),
      publications: num(p.publications ?? p.count),
      followers_gained: num(p.followers_gained ?? p.followers),
      simulation: bool(p.simulation, false),
    };
  });
  const distRaw = isRecord(data.distributions) ? data.distributions : {};
  const distributions: Record<string, DistributionStat> = {};
  for (const [key, value] of Object.entries(distRaw)) {
    if (isRecord(value)) {
      distributions[key] = {
        median: numOrNull(value.median),
        p75: numOrNull(value.p75),
        p90: numOrNull(value.p90),
        p95: numOrNull(value.p95),
        max: numOrNull(value.max),
        count: num(value.count),
      };
    }
  }
  const classesRaw = isRecord(data.performance_classes)
    ? data.performance_classes
    : {};
  return {
    window: (str(data.window, window) as AnalyticsWindow) || window,
    totals: {
      views: num(totalsSrc.views),
      likes: num(totalsSrc.likes),
      comments: num(totalsSrc.comments),
      shares: num(totalsSrc.shares),
      followers_gained: num(
        totalsSrc.followers_gained ?? totalsSrc.followers,
      ),
      watch_time_seconds: num(totalsSrc.watch_time_seconds),
      publications: num(totalsSrc.publications ?? totalsSrc.content),
    },
    series,
    platforms,
    distributions,
    performance_classes: Object.fromEntries(
      Object.entries(classesRaw).map(([k, v]) => [k, num(v)]),
    ),
    simulation: bool(data.simulation, false),
  };
}

function revenueItems(value: unknown, kind: "actual" | "forecast"): RevenueItem[] {
  return asArray(value).map((row, i): RevenueItem => {
    const r = isRecord(row) ? row : {};
    const rowKind = str(r.kind, kind);
    return {
      id: str(r.id, `${kind}-${i}`),
      kind: rowKind === "forecast" ? "forecast" : "actual",
      amount: num(r.amount),
      currency: str(r.currency, "EUR"),
      source: str(r.source, "unknown"),
      platform: strOrNull(r.platform),
      period: strOrNull(r.period),
      content_id: strOrNull(r.content_id),
      simulation: bool(r.simulation, false),
      created_at: strOrNull(r.created_at),
    };
  });
}

function revenuePlatforms(value: unknown): RevenuePlatform[] {
  return asArray(value).map((row, i) => {
    const p = isRecord(row) ? row : {};
    return {
      platform: str(p.platform, `platform-${i}`),
      amount: num(p.amount),
      count: num(p.count),
      simulation: bool(p.simulation, false),
    };
  });
}

function revenueBucket(value: unknown, kind: "actual" | "forecast"): RevenueBucket {
  if (Array.isArray(value)) {
    const items = revenueItems(value, kind);
    const total = items.reduce((sum, item) => sum + item.amount, 0);
    return {
      today: null,
      mtd: null,
      lifetime: null,
      total: items.length ? total : null,
      has_data: items.length > 0,
      items,
      by_platform: [],
    };
  }
  if (!isRecord(value)) {
    return {
      today: null,
      mtd: null,
      lifetime: null,
      total: null,
      has_data: false,
      items: [],
      by_platform: [],
    };
  }
  const items = revenueItems(value.items ?? value.events, kind);
  return {
    today: numOrNull(value.today ?? value.revenue_today),
    mtd: numOrNull(value.mtd ?? value.revenue_mtd),
    lifetime: numOrNull(value.lifetime),
    total: numOrNull(value.total ?? value.lifetime),
    has_data: bool(value.has_data, items.length > 0),
    items,
    by_platform: revenuePlatforms(value.by_platform),
  };
}

export function normalizeRevenue(data: unknown): RevenueResponse {
  if (!isRecord(data)) {
    return {
      currency: "EUR",
      actual: {
        today: null,
        mtd: null,
        lifetime: null,
        total: null,
        has_data: false,
        items: [],
        by_platform: [],
      },
      forecast: {
        today: null,
        mtd: null,
        lifetime: null,
        total: null,
        has_data: false,
        items: [],
        by_platform: [],
      },
    };
  }
  let actual = revenueBucket(data.actual, "actual");
  let forecast = revenueBucket(data.forecast, "forecast");
  if (!data.actual && !data.forecast && Array.isArray(data.items)) {
    const items = revenueItems(data.items, "actual");
    actual = {
      today: numOrNull(data.actual_today),
      mtd: numOrNull(data.actual_mtd),
      lifetime: numOrNull(data.actual_lifetime),
      total: numOrNull(data.actual_total),
      has_data: items.some((item) => item.kind === "actual"),
      items: items.filter((item) => item.kind === "actual"),
      by_platform: [],
    };
    forecast = {
      today: numOrNull(data.forecast_today),
      mtd: numOrNull(data.forecast_mtd),
      lifetime: numOrNull(data.forecast_lifetime),
      total: numOrNull(data.forecast_total),
      has_data: items.some((item) => item.kind === "forecast"),
      items: items.filter((item) => item.kind === "forecast"),
      by_platform: [],
    };
  }
  return {
    currency: str(data.currency, "EUR"),
    actual,
    forecast,
  };
}

const EMPTY_COUNTS: PublishingCounts = {
  queued: 0,
  processing: 0,
  published: 0,
  failed: 0,
  retry: 0,
  awaiting: 0,
};

export function normalizePublishing(
  data: unknown,
  limit = 50,
  offset = 0,
): PublishingResponse {
  const page = asPaginated(data, (row, i): PublishingRow => {
    const r = isRecord(row) ? row : {};
    const status = str(r.status, "queued");
    return {
      id: str(r.id, `pub-${i}`),
      content_id: strOrNull(r.content_id),
      title: strOrNull(r.title),
      platform: str(r.platform, "unknown"),
      status,
      bucket: publishBucket(status),
      url: strOrNull(r.url),
      error: strOrNull(r.error ?? r.last_error),
      simulation: bool(r.simulation, false),
      created_at: strOrNull(r.created_at),
    };
  }, limit, offset);
  const record = isRecord(data) ? data : {};
  const countsSrc = isRecord(record.counts) ? record.counts : {};
  const counts: PublishingCounts = { ...EMPTY_COUNTS };
  for (const key of Object.keys(EMPTY_COUNTS) as (keyof PublishingCounts)[]) {
    counts[key] = num(countsSrc[key]);
  }
  const hasCountPayload = Object.values(counts).some((n) => n > 0);
  if (!hasCountPayload) {
    for (const item of page.items) {
      counts[item.bucket] += 1;
    }
  }
  return { ...page, counts };
}

export function normalizeBootstrap(data: unknown): BootstrapResponse {
  if (!isRecord(data)) {
    return {
      platforms: [],
      human_checklist: [],
      human_actions: [],
      production_accounts_connected: false,
      message: null,
    };
  }
  const platforms = asArray(data.platforms ?? data.connections).map(
    (row, i): PlatformConnection => {
      const p = isRecord(row) ? row : {};
      const state = str(p.state ?? p.status, "not_configured") as ConnectionState;
      return {
        platform: str(p.platform ?? p.name, `platform-${i}`),
        state,
        account_label: strOrNull(p.account_label ?? p.label),
        checklist: asArray(p.checklist).map((item) => String(item)),
      };
    },
  );
  const human_actions = asArray(data.human_actions).map(normalizeHumanAction);
  const human_checklist = asArray(data.human_checklist ?? data.checklist).map(
    (item) => String(item),
  );
  if (human_checklist.length === 0) {
    for (const action of human_actions) {
      human_checklist.push(action.title);
    }
  }
  const production_accounts_connected = bool(
    data.production_accounts_connected,
    platforms.some((p) => p.state === "ready" || p.state === "connected"),
  );
  const rawActivation = isRecord(data.activation) ? data.activation : {};
  const platformsBlock = isRecord(rawActivation.platforms) ? rawActivation.platforms : rawActivation;
  const activation: BootstrapResponse["activation"] = {};
  for (const [name, value] of Object.entries(platformsBlock)) {
    if (!isRecord(value)) continue;
    const handoff = isRecord(value.handoff) ? value.handoff : {};
    activation[name] = {
      platform: str(value.platform, name),
      state: str(value.state, "planning"),
      blocked_reason: strOrNull(value.blocked_reason),
      checkpoint_kind: strOrNull(value.checkpoint_kind),
      selected_handle: strOrNull(value.selected_handle),
      ready: bool(value.ready, false),
      handoff_url: strOrNull(handoff.url ?? value.handoff_url),
      handoff_opened: bool(handoff.opened, false),
    };
  }
  return {
    platforms,
    human_checklist,
    human_actions,
    production_accounts_connected,
    message: strOrNull(data.message),
    activation,
    brand: isRecord(rawActivation.brand)
      ? rawActivation.brand
      : isRecord(data.brand)
        ? data.brand
        : null,
  };
}

export function normalizeHumanAction(row: unknown, index = 0): HumanActionRow {
  const r = isRecord(row) ? row : {};
  return {
    id: str(r.id, `ha-${index}`),
    title: str(r.title, "Untitled action"),
    instructions: str(r.instructions, ""),
    category: str(r.category, "general"),
    status: str(r.status, "open"),
    platform: strOrNull(r.platform),
    blocking: bool(r.blocking, false),
    created_at: strOrNull(r.created_at),
  };
}

export function normalizeRunCycle(data: unknown): RunCycleResult {
  if (!isRecord(data)) {
    return {
      accepted: true,
      job_id: null,
      status: null,
      message: "Cycle request accepted.",
      correlation_id: null,
      blocked: false,
    };
  }
  const jobIds = isRecord(data.job_ids) ? data.job_ids : {};
  const firstJob = strOrNull(jobIds.director_tick ?? data.job_id ?? data.id);
  const blocked = bool(data.blocked, false);
  const parts = [
    blocked ? "Cycle blocked." : "Cycle enqueued.",
    firstJob ? `director ${firstJob}` : null,
    jobIds.trend_ingest ? `trend ${String(jobIds.trend_ingest)}` : null,
    data.correlation_id ? `corr ${String(data.correlation_id)}` : null,
  ].filter(Boolean);
  return {
    accepted: bool(data.accepted ?? data.ok, !blocked),
    job_id: firstJob,
    status: strOrNull(data.status),
    message: str(data.message ?? data.detail, parts.join(" · ")),
    correlation_id: strOrNull(data.correlation_id),
    blocked,
  };
}
