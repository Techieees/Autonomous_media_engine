import type { ReactNode } from "react";
import type { Tone } from "@/lib/status";

const TONE: Record<Tone, string> = {
  run: "text-signal-run border-signal-run/40 bg-signal-run/10",
  warn: "text-signal-warn border-signal-warn/40 bg-signal-warn/10",
  fail: "text-signal-fail border-signal-fail/40 bg-signal-fail/10",
  info: "text-signal-info border-signal-info/40 bg-signal-info/10",
  idle: "text-mist-400 border-ink-600 bg-ink-800",
};

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-[13px] font-medium uppercase tracking-[0.16em] text-mist-200">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 max-w-3xl text-[12px] leading-5 text-mist-400">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function Card({
  title,
  actions,
  children,
  className = "",
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded border border-ink-600 bg-ink-850 ${className}`}
    >
      {title ? (
        <div className="flex items-center justify-between gap-3 border-b border-ink-600 px-3 py-2">
          <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-mist-400">
            {title}
          </h2>
          {actions}
        </div>
      ) : null}
      <div className="p-3">{children}</div>
    </section>
  );
}

export function Kpi({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded border border-ink-600 bg-ink-850 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-[0.14em] text-mist-400">
        {label}
      </div>
      <div className="mt-1 text-[22px] font-medium leading-none tabular text-mist-100">
        {value}
      </div>
      {hint ? <div className="mt-1.5 text-[11px] text-mist-500">{hint}</div> : null}
    </div>
  );
}

export function StatusPill({
  tone,
  children,
}: {
  tone: Tone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

export function SimulationBadge({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <StatusPill tone="warn">Simulation</StatusPill>
  );
}

export function EmptyState({
  title,
  detail,
}: {
  title: string;
  detail?: string;
}) {
  return (
    <div className="rounded border border-dashed border-ink-600 px-4 py-8 text-center">
      <p className="text-[13px] text-mist-200">{title}</p>
      {detail ? (
        <p className="mx-auto mt-1 max-w-xl text-[12px] text-mist-400">{detail}</p>
      ) : null}
    </div>
  );
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded border border-signal-fail/40 bg-signal-fail/10 px-3 py-2 text-[12px] text-mist-100"
    >
      <p>{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded border border-ink-600 bg-ink-800 px-2 py-1 text-[11px] uppercase tracking-[0.08em] hover:bg-ink-700"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function LoadingBlock({ label = "Loading from API…" }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded border border-ink-600 bg-ink-850 px-3 py-6 text-center text-[12px] text-mist-400"
    >
      {label}
    </div>
  );
}

export function MetaLine({
  fetchedAt,
  extra,
}: {
  fetchedAt: string | null;
  extra?: string;
}) {
  return (
    <p className="text-[11px] text-mist-500">
      {fetchedAt ? `Fetched ${new Date(fetchedAt).toLocaleTimeString()}` : "Not fetched"}
      {extra ? ` · ${extra}` : ""}
    </p>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);
  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  return (
    <nav
      aria-label="Pagination"
      className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[12px] text-mist-400"
    >
      <p className="tabular">
        {total === 0 ? "0 rows" : `${start}–${end} of ${total}`}
      </p>
      <div className="flex gap-1">
        <button
          type="button"
          disabled={!canPrev}
          onClick={() => onChange(Math.max(0, offset - limit))}
          className="rounded border border-ink-600 px-2 py-1 enabled:hover:bg-ink-800 disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          disabled={!canNext}
          onClick={() => onChange(offset + limit)}
          className="rounded border border-ink-600 px-2 py-1 enabled:hover:bg-ink-800 disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </nav>
  );
}

export function DataTable({
  columns,
  children,
  caption,
}: {
  columns: string[];
  children: ReactNode;
  caption: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-left text-[12px]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-ink-600 text-[10px] uppercase tracking-[0.12em] text-mist-400">
            {columns.map((col) => (
              <th key={col} scope="col" className="px-2 py-2 font-medium">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-700">{children}</tbody>
      </table>
    </div>
  );
}
