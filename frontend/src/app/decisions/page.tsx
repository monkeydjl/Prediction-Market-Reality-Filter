"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, RefreshCw, Target, Zap } from "lucide-react";
import { DecisionCard } from "@/components/decisions/decision-card";
import { eventsApi, type DecisionReport, type FreshEdge } from "@/lib/api";
import { fmtSignedPct } from "@/lib/format";
import { cn } from "@/lib/utils";

const SECTION_META: { key: string; label: string; cls: string }[] = [
  { key: "act", label: "建议行动", cls: "border-pos/40 bg-pos/10 text-pos" },
  { key: "provisional_act", label: "临时行动", cls: "border-blue-400/40 bg-blue-50/10 text-blue-600 dark:text-blue-400" },
  { key: "watch", label: "持续观察", cls: "border-warn/40 bg-warn/10 text-warn" },
];

function fmtEdge(n: number | null | undefined) {
  if (n == null) return "—";
  return fmtSignedPct(n, 1);
}


function FreshEdgesPanel({ edges, defaultExpanded }: { edges: FreshEdge[]; defaultExpanded?: boolean }) {
  const [open, setOpen] = useState(defaultExpanded ?? false);
  const edgeCount = edges.length;
  const freshCount = edges.filter((e) => e.edge.classification === "fresh").length;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex flex-wrap items-center justify-between gap-2 text-left"
      >
        <div className="flex items-center gap-2">
          <Zap className="size-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">新鲜 Edge</h2>
          <span className="font-mono text-xs text-muted-foreground">{edgeCount} 条</span>
          {freshCount > 0 && <span className="text-[11px] text-pos">· {freshCount} 个新鲜</span>}
        </div>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      {open && (
        edges.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">当前没有新鲜 edge。</p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {edges.map((e) => {
              const edge = e.edge;
              const cls = edge.classification === "fresh"
                ? "border-pos/40 bg-pos/10 text-pos"
                : "border-border bg-secondary text-muted-foreground";
              const label = edge.classification === "fresh" ? "新鲜" : edge.classification;
              const latest = edge.latest_edge ?? 0;
              return (
                <Link
                  key={e.event_id}
                  href={`/events?id=${encodeURIComponent(e.event_id)}`}
                  className="group flex flex-col gap-2 rounded-md border border-border bg-background/40 p-3 transition-colors hover:border-primary/40 hover:bg-secondary/30"
                >
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="line-clamp-2 text-xs font-medium leading-snug group-hover:text-primary">
                      {e.event_title_zh || e.event_title || e.event_id}
                    </h3>
                    <span className={`inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${cls}`}>
                      {label}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                    <span>Edge <span className={latest >= 0 ? "font-semibold text-pos" : "font-semibold text-neg"}>{fmtEdge(edge.latest_edge)}</span></span>
                    <span>峰值 {fmtEdge(edge.peak_edge)}</span>
                    <span>变化 {fmtEdge(edge.recent_edge_change)}</span>
                  </div>
                </Link>
              );
            })}
          </div>
        )
      )}
    </section>
  );
}

export default function DecisionsPage() {
  const [decisions, setDecisions] = useState<DecisionReport[]>([]);
  const [freshEdges, setFreshEdges] = useState<FreshEdge[]>([]);
  const [freshById, setFreshById] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "act" | "provisional_act" | "watch">("all");

  const load = useCallback(async () => {
    setError(null);
    const [open, fresh] = await Promise.all([
      eventsApi.openDecisions(undefined, 100),
      eventsApi.freshEdges(50),
    ]);
    setDecisions(open.decisions ?? []);
    setFreshEdges((fresh.edges ?? []) as FreshEdge[]);
    const map: Record<string, string> = {};
    for (const e of (fresh.edges ?? []) as FreshEdge[]) {
      map[e.event_id] = e.edge.classification;
    }
    setFreshById(map);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
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
    return () => { cancelled = true; };
  }, [load]);

  // Group decisions by type
  const grouped = useMemo(() => {
    const map: Record<string, DecisionReport[]> = {};
    for (const d of decisions) {
      const key = d.recommendation.decision ?? "skip";
      if (!map[key]) map[key] = [];
      map[key].push(d);
    }
    return map;
  }, [decisions]);

  const counts = useMemo(
    () => ({
      total: decisions.length,
      act: (grouped.act ?? []).length,
      provisional_act: (grouped.provisional_act ?? []).length,
      watch: (grouped.watch ?? []).length,
    }),
    [grouped, decisions],
  );

  const hasFreshEdges = freshEdges.length > 0;

  return (
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-balance text-xl font-semibold md:text-2xl">决策机会</h1>
            <p className="text-sm text-muted-foreground">
              系统检测到市场分歧、按调整后 Edge 排序的事件。
            </p>
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

        <div className="flex items-center gap-2">
          {([
            { key: "all", label: "全部", count: counts.total },
            { key: "act", label: "建议行动", count: counts.act },
            { key: "provisional_act", label: "临时行动", count: counts.provisional_act },
            { key: "watch", label: "持续观察", count: counts.watch },
          ] as const).map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                filter === f.key
                  ? "bg-secondary font-medium text-foreground"
                  : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
              }`}
            >
              {f.label} <span className="font-mono text-xs opacity-70">{f.count}</span>
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {loading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">加载中…</div>
        ) : counts.total === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card px-6 py-12 text-center">
            <Target className="size-8 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm font-medium text-foreground">当前没有可展示的机会</p>
            <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
              反馈闭环需要先积累已结算的预测。系统持续运行、事件陆续结算后，这里会陆续出现机会。
            </p>
          </div>
        ) : (
          <>
            {hasFreshEdges && filter === "all" && (
              <FreshEdgesPanel edges={freshEdges} defaultExpanded={freshEdges.filter((e) => e.edge.classification === "fresh").length > 0} />
            )}

            {/* Sections filtered by selected type */}
            {SECTION_META
              .filter((sec) => filter === "all" || filter === sec.key)
              .map((sec) => {
                const items = grouped[sec.key] ?? [];
                if (items.length === 0) return null;
                return (
                  <section key={sec.key} className="flex flex-col gap-3">
                    <h2 className={`inline-flex w-fit items-center gap-2 rounded-md border px-3 py-1 text-xs font-medium ${sec.cls}`}>
                      {sec.label}
                      <span className="font-mono">{items.length}</span>
                    </h2>
                    <div className="grid gap-3 lg:grid-cols-2">
                      {items.map((d) => (
                        <DecisionCard key={d.event_id} report={d} freshness={freshById[d.event_id]} />
                      ))}
                    </div>
                  </section>
                );
              })}
          </>
        )}
      </main>
  );
}
