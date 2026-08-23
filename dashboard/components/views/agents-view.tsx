"use client";

import { api } from "@/lib/api";
import { formatDuration, formatWhen, titleCaseToken } from "@/lib/format";
import { statusTone } from "@/lib/status";
import { useResource } from "@/lib/use-resource";
import {
  DataTable,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  MetaLine,
  PageHeader,
  SimulationBadge,
  StatusPill,
} from "../ui";

export function AgentsView() {
  const feed = useResource(() => api.agents(80, 0), [], 5_000);

  return (
    <div>
      <PageHeader
        title="Agents"
        description="Live feed of runs, tasks, and decisions. Prompt chat is not memory — this list is."
        actions={<MetaLine fetchedAt={feed.fetchedAt} extra="5s refresh" />}
      />
      {feed.error ? <ErrorBanner message={feed.error} onRetry={feed.reload} /> : null}
      {feed.loading && !feed.data ? (
        <LoadingBlock />
      ) : feed.data && feed.data.length === 0 ? (
        <EmptyState
          title="No agent runs"
          detail="Director, scouts, writer, critic, factory, QA, and learning appear here after work is persisted."
        />
      ) : feed.data ? (
        <DataTable
          caption="Agent activity"
          columns={["When", "Kind", "Agent", "Title", "Status", "Duration"]}
        >
          {feed.data.map((item) => (
            <tr key={`${item.kind}-${item.id}`} className="text-mist-200">
              <td className="px-2 py-2 align-top tabular text-mist-400">
                {formatWhen(item.created_at)}
              </td>
              <td className="px-2 py-2 align-top">
                <StatusPill tone="idle">{item.kind}</StatusPill>
              </td>
              <td className="px-2 py-2 align-top">{titleCaseToken(item.agent)}</td>
              <td className="px-2 py-2 align-top">
                <div className="flex flex-wrap items-center gap-2">
                  <span>{item.title}</span>
                  <SimulationBadge active={item.simulation} />
                </div>
                {item.detail ? (
                  <p className="mt-0.5 text-[11px] text-mist-400">{item.detail}</p>
                ) : null}
              </td>
              <td className="px-2 py-2 align-top">
                {item.status ? (
                  <StatusPill tone={statusTone(item.status)}>
                    {titleCaseToken(item.status)}
                  </StatusPill>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-2 py-2 align-top tabular">
                {formatDuration(item.duration_ms)}
              </td>
            </tr>
          ))}
        </DataTable>
      ) : null}
    </div>
  );
}
