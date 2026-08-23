"use client";

import { api } from "@/lib/api";
import { formatEuro, formatWhen, titleCaseToken } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { RevenueBucket } from "@/lib/types";
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

function BucketPanel({
  title,
  tone,
  bucket,
  note,
}: {
  title: string;
  tone: "actual" | "forecast";
  bucket: RevenueBucket;
  note: string;
}) {
  return (
    <Card
      title={title}
      actions={<StatusPill tone={tone === "actual" ? "run" : "info"}>{tone}</StatusPill>}
    >
      <p className="mb-3 text-[12px] text-mist-400">{note}</p>
      <div className="grid gap-2 sm:grid-cols-3">
        <Kpi label="Today" value={formatEuro(bucket.today)} />
        <Kpi label="MTD" value={formatEuro(bucket.mtd)} />
        <Kpi label="Lifetime" value={formatEuro(bucket.lifetime ?? bucket.total)} />
      </div>
      {!bucket.has_data ? (
        <p className="mt-2 text-[11px] text-mist-500">
          No {tone} events in the ledger. Amounts stay €-- until data exists.
        </p>
      ) : null}
      {bucket.by_platform.length > 0 ? (
        <ul className="mt-3 space-y-1 text-[12px] text-mist-300">
          {bucket.by_platform.map((row) => (
            <li key={row.platform} className="flex justify-between gap-3">
              <span>
                {titleCaseToken(row.platform)}
                {row.simulation ? " · simulation" : ""}
              </span>
              <span className="tabular">
                {formatEuro(row.amount)} · {row.count}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {bucket.items.length === 0 ? (
        <div className="mt-3">
          <EmptyState title={`No ${tone} revenue events`} />
        </div>
      ) : (
        <div className="mt-3">
          <DataTable
            caption={`${title} events`}
            columns={["When", "Amount", "Source", "Platform", "Period"]}
          >
            {bucket.items.map((item) => (
              <tr key={item.id} className="text-mist-200">
                <td className="px-2 py-2 tabular text-mist-400">
                  {formatWhen(item.created_at)}
                </td>
                <td className="px-2 py-2 tabular">
                  <span className="mr-2">{formatEuro(item.amount)}</span>
                  <SimulationBadge active={item.simulation} />
                </td>
                <td className="px-2 py-2">{item.source}</td>
                <td className="px-2 py-2">{titleCaseToken(item.platform)}</td>
                <td className="px-2 py-2">{item.period ?? "—"}</td>
              </tr>
            ))}
          </DataTable>
        </div>
      )}
    </Card>
  );
}

export function RevenueView() {
  const page = useResource(() => api.revenue(), [], 20_000);

  return (
    <div>
      <PageHeader
        title="Revenue"
        description="Actual and forecast stay in separate ledgers. Null amounts render as €--. Simulated events stay badged."
        actions={<MetaLine fetchedAt={page.fetchedAt} />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? <LoadingBlock /> : null}
      {page.data ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <BucketPanel
            title="Actual"
            tone="actual"
            bucket={page.data.actual}
            note="Recorded platform or payout events. Never mixed with forecast."
          />
          <BucketPanel
            title="Forecast"
            tone="forecast"
            bucket={page.data.forecast}
            note="Model estimate only. Not cash and not shown as earned."
          />
        </div>
      ) : null}
    </div>
  );
}
