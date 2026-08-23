"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { PlatformCompareChart, TimeSeriesChart, WindowTotalsChart } from "@/components/charts";
import { api } from "@/lib/api";
import { formatDecimal, formatInt, titleCaseToken } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { AnalyticsWindow } from "@/lib/types";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorBanner,
  Kpi,
  LoadingBlock,
  MetaLine,
  PageHeader,
  SimulationBadge,
  StatusPill,
} from "../ui";

const WINDOWS: AnalyticsWindow[] = ["24h", "7d", "30d", "lifetime"];

function isWindow(value: string | null): value is AnalyticsWindow {
  return WINDOWS.includes(value as AnalyticsWindow);
}

export function AnalyticsView() {
  const params = useSearchParams();
  const router = useRouter();
  const window: AnalyticsWindow = isWindow(params.get("window"))
    ? (params.get("window") as AnalyticsWindow)
    : "7d";
  const page = useResource(() => api.analytics(window), [window]);

  function setWindow(next: AnalyticsWindow) {
    const usp = new URLSearchParams(params.toString());
    usp.set("window", next);
    router.replace(`/analytics?${usp.toString()}`);
  }

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Normalized metrics from official snapshots. Simulated reach is labeled, never presented as real."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <div role="tablist" aria-label="Analytics window" className="flex gap-1">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  type="button"
                  role="tab"
                  aria-selected={window === w}
                  onClick={() => setWindow(w)}
                  className={`rounded border px-2 py-1 text-[11px] uppercase tracking-[0.08em] ${
                    window === w
                      ? "border-ink-500 bg-ink-700 text-mist-100"
                      : "border-ink-600 text-mist-400 hover:bg-ink-800"
                  }`}
                >
                  {w}
                </button>
              ))}
            </div>
            <MetaLine fetchedAt={page.fetchedAt} />
          </div>
        }
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? <LoadingBlock /> : null}
      {page.data ? (
        <div className="space-y-3">
          {page.data.simulation ? (
            <StatusPill tone="warn">Simulation included</StatusPill>
          ) : null}

          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Kpi label="Views" value={formatInt(page.data.totals.views)} />
            <Kpi label="Likes" value={formatInt(page.data.totals.likes)} />
            <Kpi label="Comments" value={formatInt(page.data.totals.comments)} />
            <Kpi label="Shares" value={formatInt(page.data.totals.shares)} />
            <Kpi label="Followers" value={formatInt(page.data.totals.followers_gained)} />
            <Kpi label="Publications" value={formatInt(page.data.totals.publications)} />
          </div>

          <Card title={`Window totals · ${window}`}>
            <WindowTotalsChart totals={page.data.totals} windowLabel={window} />
          </Card>

          <Card title={`Series · ${window}`}>
            {page.data.series.length === 0 ? (
              <EmptyState
                title="No time series for this window"
                detail="The API returned window totals and distributions, not a timestamped series. Values above are the real totals."
              />
            ) : (
              <TimeSeriesChart data={page.data.series} windowLabel={window} />
            )}
          </Card>

          <Card title="Platform comparison">
            {page.data.platforms.length === 0 ? (
              <EmptyState title="No platform metrics" />
            ) : (
              <>
                <PlatformCompareChart data={page.data.platforms} />
                <DataTable
                  caption="Platform metrics"
                  columns={["Platform", "Views", "Likes", "Comments", "Followers", "Publications"]}
                >
                  {page.data.platforms.map((row) => (
                    <tr key={row.platform} className="text-mist-200">
                      <td className="px-2 py-2">
                        <span className="mr-2">{titleCaseToken(row.platform)}</span>
                        <SimulationBadge active={row.simulation} />
                      </td>
                      <td className="px-2 py-2 tabular">{formatInt(row.views)}</td>
                      <td className="px-2 py-2 tabular">{formatInt(row.likes)}</td>
                      <td className="px-2 py-2 tabular">{formatInt(row.comments)}</td>
                      <td className="px-2 py-2 tabular">{formatInt(row.followers_gained)}</td>
                      <td className="px-2 py-2 tabular">{formatInt(row.publications)}</td>
                    </tr>
                  ))}
                </DataTable>
              </>
            )}
          </Card>

          {Object.keys(page.data.distributions).length > 0 ? (
            <Card title="Distributions">
              <DataTable
                caption="Metric distributions"
                columns={["Metric", "Median", "P75", "P90", "P95", "Max", "Count"]}
              >
                {Object.entries(page.data.distributions).map(([name, stat]) => (
                  <tr key={name} className="text-mist-200">
                    <td className="px-2 py-2">{titleCaseToken(name)}</td>
                    <td className="px-2 py-2 tabular">{formatDecimal(stat.median)}</td>
                    <td className="px-2 py-2 tabular">{formatDecimal(stat.p75)}</td>
                    <td className="px-2 py-2 tabular">{formatDecimal(stat.p90)}</td>
                    <td className="px-2 py-2 tabular">{formatDecimal(stat.p95)}</td>
                    <td className="px-2 py-2 tabular">{formatDecimal(stat.max)}</td>
                    <td className="px-2 py-2 tabular">{formatInt(stat.count)}</td>
                  </tr>
                ))}
              </DataTable>
            </Card>
          ) : null}

          {Object.keys(page.data.performance_classes).length > 0 ? (
            <Card title="Performance classes">
              <ul className="grid gap-2 sm:grid-cols-5 text-[12px]">
                {Object.entries(page.data.performance_classes).map(([name, count]) => (
                  <li key={name} className="rounded border border-ink-600 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-[0.12em] text-mist-400">
                      {titleCaseToken(name)}
                    </div>
                    <div className="mt-1 text-[18px] tabular">{formatInt(count)}</div>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
