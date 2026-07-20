"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { EdgeTimelineChart } from "@/components/edges/edge-timeline-chart";
import { DomainScopeBanner } from "@/components/common/domain-scope-banner";
import { PaginationControls } from "@/components/pagination-controls";
import { eventsApi, type EdgePoint, type FreshEdge } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 10;

const CLASS_META: Record<string, { label: string; cls: string; rank: number }> = {
  fresh: { label: "仍接近峰值", cls: "border-pos/40 bg-pos/10 text-pos", rank: 0 },
  decaying: { label: "已从峰值回落", cls: "border-warn/40 bg-warn/10 text-warn", rank: 1 },
  stale: { label: "快照已过时", cls: "border-border bg-secondary text-muted-foreground", rank: 2 },
  closed: { label: "分歧已收敛", cls: "border-border bg-secondary text-muted-foreground", rank: 3 },
  no_data: { label: "无数据", cls: "border-border bg-secondary text-muted-foreground", rank: 4 },
};

function fmtEdge(n: number | null | undefined) {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}pt`;
}

function edgeTone(n: number | null | undefined) {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "text-muted-foreground";
  return v >= 0 ? "text-pos" : "text-neg";
}

function fmtAge(hours: number | null | undefined) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}


function EdgeTimeline({ series, compact }: { series?: EdgePoint[]; compact?: boolean }) {
  const data = (series ?? [])
    .filter((p) => Number.isFinite(p.edge))
    .map((p) => ({
      label: fmtDateTime(p.timestamp),
      edge: p.edge,
      model: p.estimated,
      market: p.baseline,
    }));

  const h = compact ? 44 : 110;
  if (data.length < 2) {
    return (
      <div
        className="grid place-items-center rounded-md border border-dashed border-border text-xs text-muted-foreground"
        style={{ height: h }}
      >
        时间线不足
      </div>
    );
  }

  return <EdgeTimelineChart data={data} height={h} />;
}

function EdgeCard({ item }: { item: FreshEdge }) {
  const edge = item.edge;
  return (
    <Link
      href={`/events?id=${encodeURIComponent(item.event_id)}`}
      className="group flex flex-col gap-2 rounded-lg border border-border bg-card p-3 transition-colors hover:border-primary/40 hover:bg-secondary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="line-clamp-2 text-xs font-medium leading-snug">
          {item.event_title_zh || item.event_title || item.event_id}
        </h3>
        <span className="shrink-0 rounded-md border border-border bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {edge.observations}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <div className="grid w-40 grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <div>
            <span className="block text-[10px] text-muted-foreground">当前</span>
            <span className={`font-mono font-semibold ${edgeTone(edge.latest_edge)}`}>{fmtEdge(edge.latest_edge)}</span>
          </div>
          <div>
            <span className="block text-[10px] text-muted-foreground">峰值</span>
            <span className={`font-mono font-semibold ${edgeTone(edge.peak_edge)}`}>{fmtEdge(edge.peak_edge)}</span>
          </div>
          <div>
            <span className="block text-[10px] text-muted-foreground">变化</span>
            <span className={`font-mono font-semibold ${edgeTone(edge.recent_edge_change)}`}>{fmtEdge(edge.recent_edge_change)}</span>
          </div>
          <div>
            <span className="block text-[10px] text-muted-foreground">年龄</span>
            <span className="font-mono font-semibold">{fmtAge(edge.age_hours)}</span>
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <EdgeTimeline series={item.series} compact />
        </div>
      </div>
    </Link>
  );
}

export default function EdgesPage() {
  const [edges, setEdges] = useState<FreshEdge[]>([]);
  const [total, setTotal] = useState(0);
  const [classTotals, setClassTotals] = useState<Record<string, number>>({});
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | "all">("all");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await eventsApi.edgeMonitor(PAGE_SIZE, page * PAGE_SIZE, active);
        if (!cancelled) {
          const rows = data.edges ?? [];
          setEdges(rows);
          setTotal(data.total ?? data.count ?? rows.length);
          setClassTotals(data.classification_totals ?? {});
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [active, page]);

  const classes = useMemo(() => {
    return Object.keys(CLASS_META)
      .filter((k) => (classTotals[k] ?? 0) > 0)
      .sort((a, b) => CLASS_META[a].rank - CLASS_META[b].rank);
  }, [classTotals]);

  const allTotal = useMemo(() => {
    const fromClasses = Object.values(classTotals).reduce((sum, count) => sum + count, 0);
    return fromClasses || (active === "all" ? total : edges.length);
  }, [active, classTotals, edges.length, total]);

  const showInitialLoading = loading && edges.length === 0 && total === 0;

  return (
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-col gap-1">
          <h1 className="text-balance text-xl font-semibold md:text-2xl">
            事件 Edge 监测
          </h1>
          <p className="text-sm text-muted-foreground">
            监控事件分析概率与预测市场（Polymarket 等）价格之间的偏离，按生命周期
            （新鲜 / 衰减 / 过时 / 收敛）分类。与体育 Kernel 比赛 edge 相互独立。
          </p>
        </div>
        <DomainScopeBanner domain="event" />

        {/* Summary cards */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <button
            type="button"
            onClick={() => {
              setActive("all");
              setPage(0);
            }}
            className={cn(
              "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors",
              active === "all"
                ? "border-primary/40 bg-primary/10"
                : "border-border bg-card hover:bg-secondary/30",
            )}
          >
            <span className="text-xs text-muted-foreground">全部</span>
            <span className="font-mono text-lg font-semibold">{allTotal}</span>
          </button>
          {classes.map((cls) => {
            const meta = CLASS_META[cls];
            const count = classTotals[cls] ?? 0;
            return (
              <button
                key={cls}
                type="button"
                onClick={() => {
                  setActive(cls);
                  setPage(0);
                }}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors",
                  active === cls
                    ? meta.cls
                    : "border-border bg-card hover:bg-secondary/30",
                )}
              >
                <span className={cn("text-xs", active === cls ? "opacity-90" : "text-muted-foreground")}>{meta.label}</span>
                <span className="font-mono text-lg font-semibold">{count}</span>
              </button>
            );
          })}
        </div>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {showInitialLoading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">加载中…</div>
        ) : edges.length === 0 ? (
          <div className="grid h-40 place-items-center rounded-lg border border-dashed border-border bg-card text-sm text-muted-foreground">
            当前没有 {active === "all" ? "" : CLASS_META[active]?.label} 的 edge
          </div>
        ) : (
          <>
            <div className="grid gap-3 lg:grid-cols-2">
              {edges.map((e) => (
                <EdgeCard key={e.event_id} item={e} />
              ))}
            </div>
            {total > PAGE_SIZE && (
              <PaginationControls
                page={page}
                pageSize={PAGE_SIZE}
                total={total}
                loading={loading}
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </main>
  );
}
