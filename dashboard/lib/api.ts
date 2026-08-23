import {
  normalizeAgents,
  normalizeAnalytics,
  normalizeBootstrap,
  normalizeContent,
  normalizeHealth,
  normalizeHumanAction,
  normalizeOverview,
  normalizePublishing,
  normalizeRevenue,
  normalizeRunCycle,
  normalizeStrategy,
  normalizeTrend,
  asPaginated,
} from "./normalize";
import type {
  ActivityItem,
  AnalyticsResponse,
  AnalyticsWindow,
  BootstrapResponse,
  ContentRow,
  HealthResponse,
  HumanActionRow,
  OverviewResponse,
  Paginated,
  PublishingResponse,
  RevenueResponse,
  RunCycleResult,
  StrategyResponse,
  TrendRow,
} from "./types";

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export const API_PREFIX = `${API_BASE}/api/v1`;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function query(params?: Record<string, string | number | undefined>): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    usp.set(key, String(value));
  }
  const raw = usp.toString();
  return raw ? `?${raw}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith("http") ? path : `${API_PREFIX}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `API unreachable at ${API_BASE}. Start FastAPI or set NEXT_PUBLIC_API_URL.`,
      0,
      path,
    );
  }

  if (!response.ok) {
    const body = (await response.text()).slice(0, 240);
    throw new ApiError(
      `HTTP ${response.status} ${path}${body ? ` — ${body}` : ""}`,
      response.status,
      path,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<unknown>("/health").then(normalizeHealth),
  overview: () => request<unknown>("/overview").then(normalizeOverview),
  content: (limit: number, offset: number) =>
    request<unknown>(`/content${query({ limit, offset })}`).then((data) =>
      asPaginated(data, normalizeContent, limit, offset),
    ),
  trends: (limit: number, offset: number) =>
    request<unknown>(`/trends${query({ limit, offset })}`).then((data) =>
      asPaginated(data, normalizeTrend, limit, offset),
    ),
  agents: (limit: number, offset: number) =>
    request<unknown>(`/agents${query({ limit, offset })}`).then(normalizeAgents),
  events: (limit: number, offset: number) =>
    request<unknown>(`/events${query({ limit, offset })}`).then((data) =>
      asPaginated(data, (row, i) => {
        const items = normalizeAgents([row]);
        return items[0] ?? { id: String(i), kind: "event" as const, agent: null, title: "event", detail: null, status: null, created_at: null, duration_ms: null, content_id: null, simulation: false };
      }, limit, offset),
    ),
  strategy: () => request<unknown>("/strategy").then(normalizeStrategy),
  analytics: (window: AnalyticsWindow) =>
    request<unknown>(`/analytics${query({ window })}`).then((data) =>
      normalizeAnalytics(data, window),
    ),
  revenue: () => request<unknown>("/revenue").then(normalizeRevenue),
  publishing: (limit: number, offset: number, status?: string) =>
    request<unknown>(`/publishing${query({ limit, offset, status })}`).then(
      (data) => normalizePublishing(data, limit, offset),
    ),
  bootstrap: () => request<unknown>("/bootstrap").then(normalizeBootstrap),
  completeHumanAction: (id: string) =>
    request<unknown>(`/human-actions/${id}/complete`, { method: "POST" }).then(
      normalizeHumanAction,
    ),
  humanActions: (limit: number, offset: number) =>
    request<unknown>(`/human-actions${query({ limit, offset })}`).then((data) =>
      asPaginated(data, normalizeHumanAction, limit, offset),
    ),
  runCycle: () =>
    request<unknown>("/actions/run-cycle", {
      method: "POST",
      body: JSON.stringify({}),
    }).then(normalizeRunCycle),
  reports: () => request<unknown>("/reports"),
  notifications: () => request<unknown>("/notifications"),
};

export type {
  ActivityItem,
  AnalyticsResponse,
  BootstrapResponse,
  ContentRow,
  HealthResponse,
  HumanActionRow,
  OverviewResponse,
  Paginated,
  PublishingResponse,
  RevenueResponse,
  RunCycleResult,
  StrategyResponse,
  TrendRow,
};
