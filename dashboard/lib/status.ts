import type { ConnectionState, PublishBucket, SystemStatus } from "./types";

export type Tone = "run" | "warn" | "fail" | "info" | "idle";

export function systemLabel(status: SystemStatus | "unreachable" | "action required" | string): string {
  if (status === "running") return "SYSTEM RUNNING";
  if (status === "action required") return "ACTION REQUIRED";
  if (status === "unreachable") return "SYSTEM DEGRADED";
  return "SYSTEM DEGRADED";
}

export function connectionLabel(state: string): string {
  const map: Record<string, string> = {
    not_configured: "Not configured",
    connection_required: "Connection required",
    connected: "Connected",
    needs_reauthorization: "Needs reauthorization",
    needs_platform_review: "Needs platform review",
    ready: "Ready",
    requires_human_action: "Requires human action",
  };
  return map[state] ?? titleize(state);
}

export function connectionTone(state: string): Tone {
  switch (state as ConnectionState | string) {
    case "ready":
      return "run";
    case "connected":
      return "info";
    case "needs_reauthorization":
    case "needs_platform_review":
    case "requires_human_action":
    case "connection_required":
      return "warn";
    case "not_configured":
      return "idle";
    default:
      return "idle";
  }
}

export function publishBucket(status: string): PublishBucket {
  const s = status.toLowerCase();
  if (s === "queued") return "queued";
  if (s === "processing" || s === "running" || s === "leased") return "processing";
  if (s === "published" || s === "succeeded") return "published";
  if (s === "failed" || s === "dead" || s === "rejected_simulation") return "failed";
  if (s === "retry" || s === "retry_wait") return "retry";
  return "awaiting";
}

export function statusTone(status: string | null | undefined): Tone {
  if (!status) return "idle";
  const s = status.toLowerCase();
  if (
    [
      "running",
      "ready",
      "published",
      "approved",
      "succeeded",
      "ok",
      "good",
      "strong",
      "breakout",
      "viral",
      "completed",
      "active",
    ].includes(s)
  ) {
    return "run";
  }
  if (
    [
      "degraded",
      "needs_reauthorization",
      "needs_platform_review",
      "awaiting_human",
      "awaiting_platform_approval",
      "awaiting_platform_required_approval",
      "requires_human_action",
      "connection_required",
      "requires_review",
      "paused_by_budget",
      "retry",
      "retry_wait",
      "queued",
      "processing",
      "budget_blocked",
      "blocked",
    ].includes(s)
  ) {
    return s === "queued" || s === "processing" ? "info" : "warn";
  }
  if (
    [
      "failed",
      "rejected",
      "dead",
      "error",
      "cancelled",
      "rejected_simulation",
    ].includes(s)
  ) {
    return "fail";
  }
  return "idle";
}

export const PUBLISH_BUCKETS: PublishBucket[] = [
  "queued",
  "processing",
  "published",
  "failed",
  "retry",
  "awaiting",
];

function titleize(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
