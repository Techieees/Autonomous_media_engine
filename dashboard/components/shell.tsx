"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api, API_BASE } from "@/lib/api";
import { statusTone, systemLabel } from "@/lib/status";
import { useResource } from "@/lib/use-resource";
import { StatusPill } from "./ui";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/content", label: "Content" },
  { href: "/trends", label: "Trends" },
  { href: "/agents", label: "Agents" },
  { href: "/strategy", label: "Strategy" },
  { href: "/analytics", label: "Analytics" },
  { href: "/revenue", label: "Revenue" },
  { href: "/publishing", label: "Publishing" },
  { href: "/reports", label: "Reports" },
  { href: "/bootstrap", label: "Bootstrap" },
  { href: "/human-actions", label: "Human actions" },
];

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-ink-950 text-mist-100">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:bg-ink-800 focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <MobileNav />
          <main id="main" className="flex-1 px-5 py-4">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}

function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="hidden w-[220px] shrink-0 border-r border-ink-600 bg-ink-900 md:flex md:flex-col">
      <div className="border-b border-ink-600 px-4 py-3">
        <div className="text-[11px] uppercase tracking-[0.2em] text-mist-400">
          AME
        </div>
        <div className="mt-0.5 text-[13px] font-medium text-mist-100">
          Operations
        </div>
      </div>
      <nav aria-label="Primary" className="flex flex-col gap-0.5 p-2">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded px-2.5 py-1.5 text-[13px] ${
                active
                  ? "bg-ink-700 text-mist-100"
                  : "text-mist-400 hover:bg-ink-800 hover:text-mist-200"
              }`}
              aria-current={active ? "page" : undefined}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-ink-600 px-3 py-3 text-[10px] leading-4 text-mist-500">
        Official APIs only.
        <br />
        Simulated rows stay labeled.
      </div>
    </aside>
  );
}

function MobileNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Primary mobile"
      className="flex gap-1 overflow-x-auto border-b border-ink-600 bg-ink-900 px-3 py-2 md:hidden"
    >
      {NAV.map((item) => {
        const active =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`shrink-0 rounded px-2 py-1 text-[12px] ${
              active ? "bg-ink-700 text-mist-100" : "text-mist-400"
            }`}
            aria-current={active ? "page" : undefined}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function TopBar() {
  const health = useResource(() => api.health(), [], 10_000);
  const [cycleMsg, setCycleMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const apiUp = !health.error && health.data !== null;
  const status = (health.data?.status ?? "").toLowerCase();
  const ffmpegOk = health.data?.ffmpeg?.ok !== false;
  const dbOk = health.data?.db?.ok !== false;
  const redisOk = health.data?.redis?.ok !== false;
  const pending = health.loading && !health.data && !health.error;
  const degraded =
    !pending &&
    (!apiUp ||
      status === "degraded" ||
      status === "down" ||
      !ffmpegOk ||
      !dbOk ||
      !redisOk);
  const running = apiUp && (status === "ok" || status === "running") && !degraded;
  const label = pending ? "SYSTEM …" : systemLabel(running ? "running" : "degraded");
  const tone = pending ? "idle" : running ? statusTone("running") : statusTone("degraded");

  async function runCycle() {
    setBusy(true);
    setCycleMsg(null);
    try {
      const result = await api.runCycle();
      setCycleMsg(
        result.message +
          (result.job_id ? ` · job ${result.job_id}` : "") +
          (result.status ? ` · ${result.status}` : ""),
      );
    } catch (err) {
      setCycleMsg(err instanceof Error ? err.message : "Run cycle failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-600 bg-ink-900 px-5 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={tone}>{label}</StatusPill>
        {health.data?.dry_run === true ? (
          <StatusPill tone="warn">Dry-run</StatusPill>
        ) : null}
        <span className="hidden max-w-[420px] truncate text-[11px] text-mist-500 lg:inline" title={health.data?.worker?.hint}>
          API {API_BASE}
          {health.data?.queue?.depth !== undefined && health.data.queue.depth !== null
            ? ` · queue ${health.data.queue.depth}`
            : ""}
          {health.data?.worker?.hint ? ` · ${health.data.worker.hint}` : ""}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {cycleMsg ? (
          <span className="max-w-[360px] truncate text-[11px] text-mist-400" title={cycleMsg}>
            {cycleMsg}
          </span>
        ) : null}
        <button
          type="button"
          onClick={runCycle}
          disabled={busy}
          className="rounded border border-ink-700 bg-ink-900 px-2 py-1 text-[11px] text-mist-500 hover:text-mist-300 disabled:opacity-50"
          title="Operator/debug only. Autonomous mode does not require this."
        >
          {busy ? "Enqueueing…" : "Debug: run cycle"}
        </button>
      </div>
    </header>
  );
}
