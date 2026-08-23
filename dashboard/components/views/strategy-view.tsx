"use client";

import { AllocationChart } from "@/components/charts";
import { api } from "@/lib/api";
import { formatDecimal, formatPct, formatWhen } from "@/lib/format";
import { statusTone } from "@/lib/status";
import { useResource } from "@/lib/use-resource";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  MetaLine,
  PageHeader,
  StatusPill,
} from "../ui";

export function StrategyView() {
  const page = useResource(() => api.strategy(), [], 20_000);

  return (
    <div>
      <PageHeader
        title="Strategy"
        description="Director allocations below owner caps, learning recommendations, and experiment results."
        actions={<MetaLine fetchedAt={page.fetchedAt} />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? <LoadingBlock /> : null}
      {page.data ? (
        <div className="space-y-3">
          <Card title="Allocations">
            {page.data.allocations.length === 0 ? (
              <EmptyState title="No allocations" detail="Director has not written a niche mix yet." />
            ) : (
              <>
                <AllocationChart data={page.data.allocations} />
                <DataTable
                  caption="Strategy allocations"
                  columns={["Niche", "Allocation", "Active", "Decided by", "Reason"]}
                >
                  {page.data.allocations.map((row) => (
                    <tr key={row.id} className="text-mist-200">
                      <td className="px-2 py-2">{row.niche}</td>
                      <td className="px-2 py-2 tabular">
                        {row.allocation <= 1
                          ? formatPct(row.allocation)
                          : `${formatDecimal(row.allocation, 1)}%`}
                      </td>
                      <td className="px-2 py-2">{row.active ? "yes" : "no"}</td>
                      <td className="px-2 py-2">{row.decided_by ?? "—"}</td>
                      <td className="px-2 py-2 text-mist-400">{row.reason || "—"}</td>
                    </tr>
                  ))}
                </DataTable>
              </>
            )}
          </Card>

          <Card title="Learning recommendations">
            {page.data.learning_recommendations.length === 0 ? (
              <EmptyState title="No learning recommendations" />
            ) : (
              <ul className="divide-y divide-ink-700">
                {page.data.learning_recommendations.map((rec) => (
                  <li key={rec.id} className="py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] text-mist-100">{rec.recommendation}</span>
                      {rec.consumed ? <StatusPill tone="idle">Consumed</StatusPill> : (
                        <StatusPill tone="info">Open</StatusPill>
                      )}
                    </div>
                    <p className="mt-1 text-[11px] text-mist-500">
                      {rec.method ?? "—"}
                      {rec.confidence !== null ? ` · confidence ${rec.confidence.toFixed(2)}` : ""}
                      {rec.created_at ? ` · ${formatWhen(rec.created_at)}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Experiment results">
            {page.data.experiments.length === 0 ? (
              <EmptyState title="No experiments" />
            ) : (
              <DataTable
                caption="Experiments"
                columns={["Name", "Status", "Locked", "Dimensions", "Results"]}
              >
                {page.data.experiments.map((exp) => (
                  <tr key={exp.id} className="text-mist-200">
                    <td className="px-2 py-2">{exp.name}</td>
                    <td className="px-2 py-2">
                      <StatusPill tone={statusTone(exp.status)}>{exp.status}</StatusPill>
                    </td>
                    <td className="px-2 py-2">{exp.locked ? "yes" : "no"}</td>
                    <td className="px-2 py-2 font-mono text-[11px] text-mist-400">
                      {JSON.stringify(exp.dimensions)}
                    </td>
                    <td className="px-2 py-2 font-mono text-[11px] text-mist-400">
                      {exp.results ? JSON.stringify(exp.results) : "—"}
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Card>
        </div>
      ) : null}
    </div>
  );
}
