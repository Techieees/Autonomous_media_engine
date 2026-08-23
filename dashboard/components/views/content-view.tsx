"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { formatInt, formatWhen, titleCaseToken } from "@/lib/format";
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

export function ContentView() {
  const params = useSearchParams();
  const router = useRouter();
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const page = useResource(() => api.content(LIMIT, offset), [offset]);

  function setOffset(next: number) {
    const usp = new URLSearchParams(params.toString());
    if (next <= 0) usp.delete("offset");
    else usp.set("offset", String(next));
    router.replace(`/content${usp.toString() ? `?${usp}` : ""}`);
  }

  return (
    <div>
      <PageHeader
        title="Content"
        description="Pipeline rows with selected script, status, platform, views, and QA verdict."
        actions={<MetaLine fetchedAt={page.fetchedAt} />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? (
        <LoadingBlock />
      ) : page.data && page.data.items.length === 0 ? (
        <EmptyState
          title="No content items"
          detail="Nothing has entered the lifecycle yet. Run a cycle to discover and score opportunities."
        />
      ) : page.data ? (
        <>
          <DataTable
            caption="Content items"
            columns={["Topic", "Script", "Status", "Platform", "Views", "QA", "Updated"]}
          >
            {page.data.items.map((row) => (
              <tr key={row.id} className="text-mist-200">
                <td className="px-2 py-2 align-top">
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{row.topic}</span>
                    <SimulationBadge active={row.simulation} />
                  </div>
                  <div className="text-[11px] text-mist-500">
                    {row.niche ? titleCaseToken(row.niche) : "—"}
                  </div>
                </td>
                <td className="max-w-[280px] px-2 py-2 align-top text-mist-400">
                  {row.script ?? "—"}
                </td>
                <td className="px-2 py-2 align-top">
                  <StatusPill tone={statusTone(row.status)}>
                    {titleCaseToken(row.status)}
                  </StatusPill>
                </td>
                <td className="px-2 py-2 align-top">
                  {row.platforms.length > 0
                    ? row.platforms.map((p) => (
                        <div key={`${row.id}-${p.platform}`} className="flex items-center gap-1">
                          <span>{titleCaseToken(p.platform)}</span>
                          <SimulationBadge active={p.simulation} />
                        </div>
                      ))
                    : titleCaseToken(row.platform)}
                </td>
                <td className="px-2 py-2 align-top tabular">{formatInt(row.views)}</td>
                <td className="px-2 py-2 align-top">
                  {row.qa ? (
                    <StatusPill tone={statusTone(row.qa)}>{titleCaseToken(row.qa)}</StatusPill>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-2 py-2 align-top text-mist-400 tabular">
                  {formatWhen(row.updated_at ?? row.created_at)}
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
