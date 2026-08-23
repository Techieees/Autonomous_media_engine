"use client";

import { useState } from "react";
import { API_PREFIX, api } from "@/lib/api";
import { titleCaseToken } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import {
  Card,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  MetaLine,
  PageHeader,
  StatusPill,
} from "../ui";

export function BootstrapView() {
  const page = useResource(() => api.bootstrap(), [], 8_000);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const activation = page.data?.activation ?? {};
  const actions = page.data?.human_actions ?? [];
  const brandName =
    page.data?.brand && typeof page.data.brand.name === "string"
      ? page.data.brand.name
      : "Signal Brief";

  async function markCompleted(id: string) {
    setPending(id);
    setError(null);
    try {
      await api.completeHumanAction(id);
      await page.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not mark the checkpoint complete.");
    } finally {
      setPending(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Account activation"
        description="AME prepares branding, assets, and official flows. You only complete an unavoidable platform checkpoint."
        actions={<MetaLine fetchedAt={page.fetchedAt} />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {error ? <ErrorBanner message={error} /> : null}
      {page.loading && !page.data ? <LoadingBlock /> : null}

      <Card title="Brand" className="mb-3">
        <p className="text-[13px] text-mist-100">{brandName}</p>
        <p className="mt-1 text-[12px] text-mist-400">
          Selected automatically. Handle, bio, avatar, and developer metadata are already prepared.
        </p>
      </Card>

      <div className="grid gap-3 lg:grid-cols-3">
        {["youtube", "instagram", "tiktok"].map((name) => {
          const row = activation[name];
          const state = row?.state ?? "planning";
          const action = actions.find((item) => item.platform === name && item.status === "open");
          return (
            <Card
              key={name}
              title={titleCaseToken(name)}
              actions={<StatusPill tone={state === "ready" ? "ok" : "warn"}>{state}</StatusPill>}
            >
              <p className="text-[12px] text-mist-300">
                {row?.blocked_reason ||
                  (state === "ready"
                    ? "Ready. Autonomous publishing is permitted."
                    : "AME is completing every permitted machine step.")}
              </p>
              {row?.selected_handle ? (
                <p className="mt-2 text-[12px] text-mist-400">Handle: {row.selected_handle}</p>
              ) : null}
              {row?.handoff_url ? (
                <p className="mt-2 text-[12px] text-mist-400">
                  Official page {row.handoff_opened ? "already open" : "prepared"}:{" "}
                  <a href={row.handoff_url} className="text-signal-info hover:underline" target="_blank" rel="noreferrer">
                    open again
                  </a>
                </p>
              ) : null}
              {action ? (
                <div className="mt-3 rounded border border-ink-600 px-3 py-2">
                  <p className="text-[12px] text-mist-100">{action.title}</p>
                  <p className="mt-1 text-[12px] text-mist-400">{action.instructions}</p>
                  <button
                    type="button"
                    className="mt-2 rounded border border-ink-500 px-2 py-1 text-[12px] text-mist-100 hover:border-signal-info"
                    disabled={pending === action.id}
                    onClick={() => markCompleted(action.id)}
                  >
                    {pending === action.id ? "Checking…" : "Completed"}
                  </button>
                </div>
              ) : state === "ready" ? null : (
                <p className="mt-3 text-[12px] text-mist-500">No owner action. AME continues automatically.</p>
              )}
              {state === "oauth_required" || state === "oauth_in_progress" ? (
                <p className="mt-2 text-[12px]">
                  <a
                    href={`${API_PREFIX}/oauth/${name}/start`}
                    className="text-signal-info hover:underline"
                  >
                    Official {titleCaseToken(name)} OAuth
                  </a>
                </p>
              ) : null}
            </Card>
          );
        })}
      </div>

      {page.data && page.data.platforms.length === 0 ? (
        <div className="mt-3">
          <EmptyState title="No platform rows yet" detail="Bootstrap tick will create them." />
        </div>
      ) : null}
    </div>
  );
}
