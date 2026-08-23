"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Allocation, AnalyticsSeriesPoint, PlatformMetric } from "@/lib/types";
import { formatInt } from "@/lib/format";

const AXIS = { stroke: "#3d4754", fontSize: 11 };
const GRID = { stroke: "#1e242d" };
const TOOLTIP_STYLE = {
  background: "#11141a",
  border: "1px solid #2a323d",
  borderRadius: 2,
  fontSize: 12,
  color: "#e8eaed",
};

export function TimeSeriesChart({
  data,
  windowLabel,
}: {
  data: AnalyticsSeriesPoint[];
  windowLabel: string;
}) {
  const summary = data.length
    ? `Views range ${formatInt(Math.min(...data.map((d) => d.views)))}–${formatInt(Math.max(...data.map((d) => d.views)))} across ${data.length} points`
    : "No series points returned";

  return (
    <div>
      <div
        role="img"
        aria-label={`Views, likes, comments over ${windowLabel}. ${summary}.`}
        className="h-72"
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} strokeDasharray="3 3" />
            <XAxis dataKey="ts" tick={AXIS} />
            <YAxis tick={AXIS} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend />
            <Line type="monotone" dataKey="views" stroke="#5b8def" dot={false} strokeWidth={1.6} name="Views" />
            <Line type="monotone" dataKey="likes" stroke="#3dba7a" dot={false} strokeWidth={1.6} name="Likes" />
            <Line type="monotone" dataKey="comments" stroke="#d4a017" dot={false} strokeWidth={1.6} name="Comments" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {data.length > 0 ? (
        <table className="sr-only">
          <caption>Analytics series {windowLabel}</caption>
          <thead>
            <tr>
              <th>Time</th>
              <th>Views</th>
              <th>Likes</th>
              <th>Comments</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.ts}>
                <td>{row.ts}</td>
                <td>{row.views}</td>
                <td>{row.likes}</td>
                <td>{row.comments}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

export function PlatformCompareChart({ data }: { data: PlatformMetric[] }) {
  const summary = data
    .map((p) => `${p.platform} ${p.views} views`)
    .join("; ");
  return (
    <div>
      <div
        role="img"
        aria-label={`Platform comparison. ${summary || "No platform metrics."}`}
        className="h-72"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} strokeDasharray="3 3" />
            <XAxis dataKey="platform" tick={AXIS} />
            <YAxis tick={AXIS} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend />
            <Bar dataKey="views" fill="#5b8def" name="Views" />
            <Bar dataKey="likes" fill="#3dba7a" name="Likes" />
            <Bar dataKey="followers_gained" fill="#c76b3a" name="Followers" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {data.length > 0 ? (
        <table className="sr-only">
          <caption>Platform comparison</caption>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Views</th>
              <th>Likes</th>
              <th>Followers</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.platform}>
                <td>{row.platform}</td>
                <td>{row.views}</td>
                <td>{row.likes}</td>
                <td>{row.followers_gained}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

export function WindowTotalsChart({
  totals,
  windowLabel,
}: {
  totals: {
    views: number;
    likes: number;
    comments: number;
    shares: number;
    followers_gained: number;
  };
  windowLabel: string;
}) {
  const data = [
    { metric: "Views", value: totals.views },
    { metric: "Likes", value: totals.likes },
    { metric: "Comments", value: totals.comments },
    { metric: "Shares", value: totals.shares },
    { metric: "Followers", value: totals.followers_gained },
  ];
  return (
    <div>
      <div
        role="img"
        aria-label={`Window ${windowLabel} totals. ${data.map((d) => `${d.metric} ${d.value}`).join("; ")}.`}
        className="h-64"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} strokeDasharray="3 3" />
            <XAxis dataKey="metric" tick={AXIS} />
            <YAxis tick={AXIS} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Bar dataKey="value" fill="#5b8def" name={windowLabel} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <caption>Totals for {windowLabel}</caption>
        <thead>
          <tr>
            <th>Metric</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.metric}>
              <td>{row.metric}</td>
              <td>{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AllocationChart({ data }: { data: Allocation[] }) {
  const rows = data.map((row) => ({
    ...row,
    pct: row.allocation <= 1 ? row.allocation * 100 : row.allocation,
  }));
  return (
    <div
      role="img"
      aria-label={`Niche allocations: ${rows.map((r) => `${r.niche} ${r.pct.toFixed(0)} percent`).join(", ") || "none"}`}
      className="h-64"
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 16, left: 16, bottom: 0 }}>
          <CartesianGrid {...GRID} strokeDasharray="3 3" />
          <XAxis type="number" tick={AXIS} unit="%" />
          <YAxis type="category" dataKey="niche" tick={AXIS} width={110} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Bar dataKey="pct" fill="#5b8def" name="Allocation %" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
