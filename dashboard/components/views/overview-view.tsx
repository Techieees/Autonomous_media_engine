"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { formatEuro, formatInt, formatWhen, titleCaseToken } from "@/lib/format";
import { statusTone, systemLabel } from "@/lib/status";
import { useResource } from "@/lib/use-resource";
import {
  Card,
  EmptyState,
  ErrorBanner,
  Kpi,
  LoadingBlock,
  MetaLine,
  StatusPill,
} from "../ui";

export function OverviewView() {
  const overview = useResource(() => api.overview(), [], 15_000);

  if (overview.loading && !overview.data) {
    return <LoadingBlock label="Loading overview…" />;
  }

  const data = overview.data;
  const activation = data?.account_activation ?? {};
  const platforms = ["youtube", "instagram", "tiktok"];

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[13px] font-medium uppercase tracking-[0.16em] text-mist-200">
            Overview
          </h1>
          <p className="mt-1 text-[12px] text-mist-400">
            Results only. Agents keep working without a Run cycle.
          </p>
        </div>
        <MetaLine fetchedAt={overview.fetchedAt} extra="15s refresh" />
      </div>

      {overview.error ? (
        <ErrorBanner message={overview.error} onRetry={overview.reload} />
      ) : null}

      {data ? (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2 rounded border border-ink-600 bg-ink-850 px-3 py-2">
            <StatusPill tone={statusTone(data.system_status)}>
              {systemLabel(String(data.system_status))}
            </StatusPill>
            {data.autonomous_mode ? <StatusPill tone="ok">Autonomous</StatusPill> : null}
            {data.dry_run === true ? <StatusPill tone="warn">Dry-run</StatusPill> : null}
          </div>

          <Card title="Account activation" className="mb-3">
            <div className="grid gap-2 sm:grid-cols-3">
              {platforms.map((name) => {
                const row = activation[name];
                const state = row?.state ?? "planning";
                return (
                  <div key={name} className="rounded border border-ink-700 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13px] text-mist-100">{titleCaseToken(name)}</span>
                      <StatusPill tone={state === "ready" ? "ok" : "warn"}>{state}</StatusPill>
                    </div>
                    <p className="mt-1 text-[12px] text-mist-400">
                      {row?.blocked_reason ||
                        (state === "ready"
                          ? "Publishing permitted."
                          : "AME is advancing every permitted step.")}
                    </p>
                  </div>
                );
              })}
            </div>
          </Card>

          <div className="grid gap-2 sm:grid-cols-3">
            <Kpi label="Today's output" value={formatInt(data.produced_today)} />
            <Kpi label="Published" value={formatInt(data.published_today)} />
            <Kpi label="Rejected" value={formatInt(data.rejected_today)} />
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            <Kpi label="Views today" value={formatInt(data.views_today)} />
            <Kpi label="Views 7d" value={formatInt(data.views_7d)} />
            <Kpi label="Followers 7d" value={formatInt(data.followers_7d)} />
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            <Kpi label="Revenue today" value={formatEuro(data.revenue_today)} />
            <Kpi label="Revenue MTD" value={formatEuro(data.revenue_mtd)} />
            <Kpi label="Best topic" value={data.winning_topic?.topic ?? "—"} />
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <Card
              title="Today's report"
              actions={
                <Link href="/reports" className="text-[12px] text-mist-400 hover:text-mist-200">
                  All reports
                </Link>
              }
            >
              {data.daily_report ? (
                <div className="text-[13px] text-mist-200">
                  <p>{data.daily_report.headline}</p>
                  <p className="mt-1 text-[12px] text-mist-500">
                    {data.daily_report.local_date} · {data.daily_report.timezone}
                  </p>
                </div>
              ) : (
                <EmptyState title="Report not generated yet" detail="The daily report appears after the autonomous loop runs." />
              )}
            </Card>
            <Card title="Director decisions">
              {data.director_decision ? (
                <div className="space-y-1 text-[13px]">
                  <p className="text-mist-100">{data.director_decision.decision}</p>
                  {data.director_decision.reason ? (
                    <p className="text-mist-400">{data.director_decision.reason}</p>
                  ) : null}
                  <p className="text-[11px] text-mist-500">
                    {formatWhen(data.director_decision.created_at)}
                  </p>
                </div>
              ) : (
                <EmptyState title="No director decision yet" />
              )}
            </Card>
          </div>

          <div className="mt-3">
            <Card title="Human actions">
              {(data.human_actions ?? []).length === 0 ? (
                <p className="text-[13px] text-mist-400">None. AME is handling all automatable work.</p>
              ) : (
                <ul className="space-y-2 text-[13px]">
                  {(data.human_actions ?? []).map((action) => (
                    <li key={action.id}>
                      <span className="text-mist-100">{action.title}</span>
                      <p className="text-[12px] text-mist-400">{action.instructions}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}
