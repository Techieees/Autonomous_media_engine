"use client";

import { api } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import { Card, EmptyState, ErrorBanner, LoadingBlock, PageHeader } from "../ui";

type ReportPayload = {
  today?: {
    headline?: string;
    local_date?: string;
    timezone?: string;
    body?: Record<string, unknown>;
  };
  items?: Array<{
    local_date?: string;
    headline?: string;
    finalized?: boolean;
  }>;
};

export function ReportsView() {
  const page = useResource(() => api.reports() as Promise<ReportPayload>, [], 20_000);
  const today = page.data?.today;
  const body = today?.body ?? {};
  const todayStats = (body.today as Record<string, unknown> | undefined) ?? {};

  return (
    <div>
      <PageHeader title="Daily reports" description="The main owner readout. Generated once per owner-local day." />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? <LoadingBlock /> : null}
      {today ? (
        <Card title={today.headline || "Today"} className="mb-3">
          <p className="text-[12px] text-mist-500">
            {today.local_date} · {today.timezone}
          </p>
          <dl className="mt-3 grid gap-2 sm:grid-cols-3 text-[13px]">
            {Object.entries(todayStats).map(([key, value]) => (
              <div key={key}>
                <dt className="text-mist-500">{key.replaceAll("_", " ")}</dt>
                <dd className="text-mist-100">{String(value)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : (
        <EmptyState title="No report yet" />
      )}
      <Card title="History">
        {(page.data?.items ?? []).length === 0 ? (
          <EmptyState title="No archived reports" />
        ) : (
          <ul className="space-y-2 text-[13px]">
            {(page.data?.items ?? []).map((item) => (
              <li key={item.local_date} className="text-mist-200">
                {item.local_date} — {item.headline}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
