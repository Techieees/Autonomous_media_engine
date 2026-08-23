"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { formatInt, formatWhen, titleCaseToken } from "@/lib/format";
import { PUBLISH_BUCKETS, statusTone } from "@/lib/status";
import { useResource } from "@/lib/use-resource";
import {
  DataTable,
  EmptyState,
  ErrorBanner,
  Kpi,
  LoadingBlock,
  MetaLine,
  PageHeader,
  Pagination,
  SimulationBadge,
  StatusPill,
} from "../ui";

const LIMIT = 50;

export function PublishingView() {
  const params = useSearchParams();
  const router = useRouter();
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const status = params.get("status") ?? "";
  const page = useResource(
    () => api.publishing(status ? 200 : LIMIT, status ? 0 : offset, undefined),
    [offset, status],
    10_000,
  );
  const visible = page.data
    ? status
      ? page.data.items.filter((row) => row.bucket === status)
      : page.data.items
    : [];

  function replace(next: { offset?: number; status?: string }) {
    const usp = new URLSearchParams(params.toString());
    const off = next.offset ?? offset;
    const st = next.status ?? status;
    if (off <= 0) usp.delete("offset");
    else usp.set("offset", String(off));
    if (!st) usp.delete("status");
    else usp.set("status", st);
    const q = usp.toString();
    router.replace(`/publishing${q ? `?${q}` : ""}`);
  }

  const counts = page.data?.counts;

  return (
    <div>
      <PageHeader
        title="Publishing"
        description="Queued, processing, published, failed, retry, awaiting. Production publishers refuse simulated content."
        actions={<MetaLine fetchedAt={page.fetchedAt} extra="10s refresh" />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}

      <div className="mb-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {PUBLISH_BUCKETS.map((bucket) => (
          <button
            key={bucket}
            type="button"
            onClick={() => replace({ status: status === bucket ? "" : bucket, offset: 0 })}
            className={`text-left ${status === bucket ? "ring-1 ring-signal-info" : ""}`}
          >
            <Kpi label={bucket} value={formatInt(counts?.[bucket] ?? 0)} />
          </button>
        ))}
      </div>

      {page.loading && !page.data ? (
        <LoadingBlock />
      ) : page.data && visible.length === 0 ? (
        <EmptyState
          title="No publishing jobs"
          detail={
            status
              ? `No rows in ${status}.`
              : "Queue is empty. Dry-run still writes simulated publications."
          }
        />
      ) : page.data ? (
        <>
          <DataTable
            caption="Publishing jobs"
            columns={["When", "Platform", "Status", "Title", "Error"]}
          >
            {visible.map((row) => (
              <tr key={row.id} className="text-mist-200">
                <td className="px-2 py-2 align-top tabular text-mist-400">
                  {formatWhen(row.created_at)}
                </td>
                <td className="px-2 py-2 align-top">{titleCaseToken(row.platform)}</td>
                <td className="px-2 py-2 align-top">
                  <div className="flex flex-wrap items-center gap-1">
                    <StatusPill tone={statusTone(row.status)}>
                      {titleCaseToken(row.status)}
                    </StatusPill>
                    <SimulationBadge active={row.simulation} />
                  </div>
                </td>
                <td className="px-2 py-2 align-top">
                    {row.url ? (
                    <a href={row.url} className="hover:underline" target="_blank" rel="noreferrer">
                      {row.title ?? row.url}
                    </a>
                  ) : (
                    row.title ?? row.content_id ?? "—"
                  )}
                </td>
                <td className="px-2 py-2 align-top text-mist-400">{row.error ?? "—"}</td>
              </tr>
            ))}
          </DataTable>
          {status ? (
            <p className="mt-3 text-[12px] text-mist-400">
              {visible.length} in {status} (filtered from the latest fetched jobs)
            </p>
          ) : (
            <Pagination
              total={page.data.total}
              limit={page.data.limit}
              offset={page.data.offset}
              onChange={(next) => replace({ offset: next })}
            />
          )}
        </>
      ) : null}
    </div>
  );
}
