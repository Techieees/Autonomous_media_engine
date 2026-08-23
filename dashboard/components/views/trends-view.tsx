"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { formatDecimal, formatWhen, titleCaseToken } from "@/lib/format";
import { statusTone } from "@/lib/status";
import { useResource } from "@/lib/use-resource";
import {
  DataTable,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  MetaLine,
  PageHeader,
  Pagination,
  SimulationBadge,
  StatusPill,
} from "../ui";

const LIMIT = 50;

export function TrendsView() {
  const params = useSearchParams();
  const router = useRouter();
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const page = useResource(() => api.trends(LIMIT, offset), [offset]);

  function setOffset(next: number) {
    const usp = new URLSearchParams(params.toString());
    if (next <= 0) usp.delete("offset");
    else usp.set("offset", String(next));
    router.replace(`/trends${usp.toString() ? `?${usp}` : ""}`);
  }

  return (
    <div>
      <PageHeader
        title="Trends"
        description="Signals with scores and opportunity decisions. Official or permitted public sources only."
        actions={<MetaLine fetchedAt={page.fetchedAt} />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? (
        <LoadingBlock />
      ) : page.data && page.data.items.length === 0 ? (
        <EmptyState
          title="No trend signals"
          detail="Ingest has not persisted signals yet. Empty is expected before the first cycle."
        />
      ) : page.data ? (
        <>
          <DataTable
            caption="Trend signals"
            columns={[
              "Topic",
              "Source",
              "Trend",
              "Velocity",
              "Opportunity",
              "Decision",
              "Risk",
              "Observed",
            ]}
          >
            {page.data.items.map((row) => (
              <tr key={row.id} className="text-mist-200">
                <td className="px-2 py-2 align-top">
                  <div className="flex flex-wrap items-center gap-2">
                    {row.url ? (
                      <a
                        href={row.url}
                        className="hover:underline"
                        target="_blank"
                        rel="noreferrer"
                      >
                        {row.title}
                      </a>
                    ) : (
                      <span>{row.title}</span>
                    )}
                    <SimulationBadge active={row.simulation} />
                  </div>
                  <div className="text-[11px] text-mist-500">{row.topic}</div>
                </td>
                <td className="px-2 py-2 align-top">{titleCaseToken(row.source)}</td>
                <td className="px-2 py-2 align-top tabular">
                  {formatDecimal(row.trend_score)}
                </td>
                <td className="px-2 py-2 align-top tabular">
                  {formatDecimal(row.velocity)}
                </td>
                <td className="px-2 py-2 align-top tabular">
                  {formatDecimal(row.opportunity_score)}
                </td>
                <td className="px-2 py-2 align-top">
                  {row.opportunity_status ? (
                    <StatusPill tone={statusTone(row.opportunity_status)}>
                      {titleCaseToken(row.opportunity_status)}
                    </StatusPill>
                  ) : (
                    "—"
                  )}
                  {row.opportunity_approved === true ? (
                    <span className="ml-1 text-[11px] text-signal-run">approved</span>
                  ) : null}
                  {row.opportunity_explanation ? (
                    <p className="mt-1 max-w-xs text-[11px] text-mist-400">
                      {row.opportunity_explanation}
                    </p>
                  ) : null}
                </td>
                <td className="px-2 py-2 align-top tabular">
                  {formatDecimal(row.risk_score)}
                </td>
                <td className="px-2 py-2 align-top text-mist-400 tabular">
                  {formatWhen(row.observed_at)}
                </td>
              </tr>
            ))}
          </DataTable>
          <Pagination
            total={page.data.total}
            limit={page.data.limit}
            offset={page.data.offset}
            onChange={setOffset}
          />
        </>
      ) : null}
    </div>
  );
}
