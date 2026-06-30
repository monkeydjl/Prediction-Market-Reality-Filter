"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { Activity, Clock3, RefreshCw, Zap } from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { eventsApi, type EdgePoint, type FreshEdge } from "@/lib/api";
import { fmtDateTime, fmtSignedPct } from "@/lib/format";

type EdgeClass = "fresh" | "decaying" | "stale" | "closed";

const GROUPS: { key: EdgeClass; label: string; detail: string; tone: string }[] = [
  { key: "fresh", label: "Fresh", detail: "仍接近峰值", tone: "text-pos" },
  { key: "decaying", label: "Decaying", detail: "已从峰值回落", tone: "text-warn" },
  { key: "stale", label: "Stale", detail: "快照已过时", tone: "text-muted-foreground" },
  { key: "closed", label: "Closed", detail: "分歧已收敛", tone: "text-muted-foreground" },
];

const EdgeTimelineChart = dynamic(
  () => import("@/components/edges/edge-timeline-chart").then((mod) => mod.EdgeTimelineChart),
  {
    ssr: false,
    loading: () => (
      <div
        className="h-[110px] w-full animate-pulse rounded-md border border-border bg-secondary/50"
        aria-hidden="true"
      />
    ),
  },
);

function fmtEdge(n: number | null | undefined) {
  if (n == null) return "—";
  return fmtSignedPct(n, 1);
}

function fmtAge(hours: number | null | undefined) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function edgeTone(n: number | null | undefined) {
  if (n == null || n === 0) return "text-muted-foreground";
  return n > 0 ? "text-pos" : "text-neg";
}

function Metric({ label, value, tone = "text-foreground" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className={`font-mono text-sm font-semibold tabular-nums ${tone}`}>{value}</span>
    </div>
  );
}

function EdgeTimeline({ series }: { series?: EdgePoint[] }) {
  const data = (series ?? [])
    .filter((p) => Number.isFinite(p.edge))
    .map((p) => ({
      label: fmtDateTime(p.timestamp),
      edge: p.edge,
      model: p.estimated,
      market: p.baseline,
    }));

  if (data.length < 2) {
    return (
      <div className="grid h-[110px] place-items-center rounded-md border border-dashed border-border text-xs text-muted-foreground">
        时间线不足
      </div>
    );
  }

  return <EdgeTimelineChart data={data} />;
}

function EdgeCard({ item }: { item: FreshEdge }) {
  const edge = item.edge;
  return (
    <Link
      href={`/events?id=${encodeURIComponent(item.event_id)}`}
      className="grid gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-secondary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="line-clamp-2 text-sm font-medium leading-snug">{item.event_title || item.event_id}</h3>
        <span className="rounded-md border border-border bg-secondary px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
          {edge.observations}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Metric label="当前 edge" value={fmtEdge(edge.latest_edge)} tone={edgeTone(edge.latest_edge)} />
        <Metric label="峰值 edge" value={fmtEdge(edge.peak_edge)} tone={edgeTone(edge.peak_edge)} />
        <Metric label="近期变化" value={fmtEdge(edge.recent_edge_change)} tone={edgeTone(edge.recent_edge_change)} />
        <Metric label="年龄" value={fmtAge(edge.age_hours)} />
      </div>

      <EdgeTimeline series={item.series} />
    </Link>
  );
}

export default function EdgesPage() {
  const [edges, setEdges] = useState<FreshEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    setError(null);
    const response = await eventsApi.edgeMonitor(50);
    if (mountedRef.current) setEdges(response.edges ?? []);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      await load();
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        await load();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const grouped = useMemo(() => {
    const byClass: Record<EdgeClass, FreshEdge[]> = {
      fresh: [],
      decaying: [],
      stale: [],
      closed: [],
    };
    for (const item of edges) {
      const key = item.edge.classification as EdgeClass;
      if (key in byClass) byClass[key].push(item);
    }
    return byClass;
  }, [edges]);

  return (
    <div className="min-h-screen">
      <AppNav />
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2 text-primary">
              <Zap className="size-4" aria-hidden="true" />
              <span className="text-xs font-medium uppercase">Edge Monitor</span>
            </div>
            <h1 className="text-balance text-xl font-semibold md:text-2xl">Edge 监测</h1>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-secondary px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            刷新
          </button>
        </div>

        <div className="grid gap-3 md:grid-cols-4">
          {GROUPS.map((group) => (
            <div key={group.key} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-sm font-semibold ${group.tone}`}>{group.label}</span>
                <span className="font-mono text-lg font-semibold tabular-nums">
                  {grouped[group.key].length}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{group.detail}</p>
            </div>
          ))}
        </div>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {loading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            加载中…
          </div>
        ) : edges.length === 0 ? (
          <div className="grid h-52 place-items-center rounded-lg border border-dashed border-border bg-card px-6 text-center">
            <div className="flex flex-col items-center gap-2">
              <Activity className="size-7 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm font-medium">暂无 edge 轨迹</p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-7">
            {GROUPS.map((group) => (
              <section key={group.key} className="flex flex-col gap-3">
                <div className="flex items-center justify-between border-b border-border pb-2">
                  <div className="flex items-center gap-2">
                    <Clock3 className={`size-4 ${group.tone}`} aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{group.label}</h2>
                    <span className="font-mono text-xs text-muted-foreground">{grouped[group.key].length}</span>
                  </div>
                  <span className="text-xs text-muted-foreground">{group.detail}</span>
                </div>
                {grouped[group.key].length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
                    暂无
                  </div>
                ) : (
                  <div className="grid gap-3 lg:grid-cols-2">
                    {grouped[group.key].map((item) => (
                      <EdgeCard key={item.event_id} item={item} />
                    ))}
                  </div>
                )}
              </section>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
