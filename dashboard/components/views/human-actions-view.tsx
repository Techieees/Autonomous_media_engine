"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { formatWhen, titleCaseToken } from "@/lib/format";
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
  StatusPill,
} from "../ui";

const LIMIT = 50;

export function HumanActionsView() {
  const params = useSearchParams();
  const router = useRouter();
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const page = useResource(() => api.humanActions(LIMIT, offset), [offset], 10_000);

  function setOffset(next: number) {
    const usp = new URLSearchParams(params.toString());
    if (next <= 0) usp.delete("offset");
    else usp.set("offset", String(next));
    router.replace(`/human-actions${usp.toString() ? `?${usp}` : ""}`);
  }

  return (
    <div>
      <PageHeader
        title="Human actions"
        description="Owner-only queue: OAuth consent, CAPTCHA, MFA, platform review, payout, legal. AME does not collect passwords here."
        actions={<MetaLine fetchedAt={page.fetchedAt} extra="10s refresh" />}
      />
      {page.error ? <ErrorBanner message={page.error} onRetry={page.reload} /> : null}
      {page.loading && !page.data ? (
        <LoadingBlock />
      ) : page.data && page.data.items.length === 0 ? (
        <EmptyState
          title="No open owner actions"
          detail="When a platform requires a human, the item is persisted and the rest of AME continues."
        />
      ) : page.data ? (
        <>
          <DataTable
            caption="Owner-only human actions"
            columns={["When", "Title", "Category", "Platform", "Status", "Blocking", "Instructions"]}
          >
            {page.data.items.map((row) => (
              <tr key={row.id} className="text-mist-200">
                <td className="px-2 py-2 align-top tabular text-mist-400">
                  {formatWhen(row.created_at)}
                </td>
                <td className="px-2 py-2 align-top">{row.title}</td>
                <td className="px-2 py-2 align-top">{titleCaseToken(row.category)}</td>
                <td className="px-2 py-2 align-top">{titleCaseToken(row.platform)}</td>
                <td className="px-2 py-2 align-top">
                  <StatusPill tone={statusTone(row.status)}>
                    {titleCaseToken(row.status)}
                  </StatusPill>
                </td>
                <td className="px-2 py-2 align-top">
                  {row.blocking ? (
                    <StatusPill tone="warn">Blocking</StatusPill>
                  ) : (
                    "no"
                  )}
                </td>
                <td className="max-w-md px-2 py-2 align-top text-mist-400">
                  {row.instructions || "—"}
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
