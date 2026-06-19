"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Target } from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { DecisionCard } from "@/components/decisions/decision-card";
import { eventsApi, type DecisionReport, type FreshEdge } from "@/lib/api";

type Filter = "all" | "act" | "watch";

export default function DecisionsPage() {
  const [decisions, setDecisions] = useState<DecisionReport[]>([]);
  const [freshById, setFreshById] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const [open, fresh] = await Promise.all([
      eventsApi.openDecisions(),
      eventsApi.freshEdges(50),
    ]);
    setDecisions(open.decisions ?? []);
    // Index edge freshness by event so a decision card can show its band.
    const map: Record<string, string> = {};
    for (const e of (fresh.edges ?? []) as FreshEdge[]) {
      map[e.event_id] = e.edge.classification;
    }
    setFreshById(map);
  }, []);

  // Manual refresh button: toggles the spinner around a load().
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
    return () => {
      cancelled = true;
    };
  }, [load]);

  const shown = useMemo(
    () =>
      filter === "all"
        ? decisions
        : decisions.filter((d) => d.recommendation.decision === filter),
    [decisions, filter],
  );

  const counts = useMemo(
    () => ({
      all: decisions.length,
      act: decisions.filter((d) => d.recommendation.decision === "act").length,
      watch: decisions.filter((d) => d.recommendation.decision === "watch").length,
    }),
    [decisions],
  );

  const FILTERS: { key: Filter; label: string }[] = [
    { key: "all", label: `全部 ${counts.all}` },
    { key: "act", label: `建议行动 ${counts.act}` },
    { key: "watch", label: `持续观察 ${counts.watch}` },
  ];

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-balance text-xl font-semibold md:text-2xl">决策机会</h1>
            <p className="text-sm text-muted-foreground">
              系统认为当前与市场存在分歧、值得关注的事件，按调整后 edge（已用历史校准信任度加权）排序。
              只有在某类别积累足够已结算预测后才会出现“建议行动”。
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

        <div className="flex items-center gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                filter === f.key
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {loading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            加载中…
          </div>
        ) : shown.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card px-6 py-12 text-center">
            <Target className="size-8 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm font-medium text-foreground">当前没有可展示的机会</p>
            <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
              反馈闭环需要先积累已结算的预测，才能为各类别建立校准信任度并发现 edge。
              在系统持续运行、市场事件陆续结算之前，这里通常为空或仅有观察级条目。
            </p>
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {shown.map((d) => (
              <DecisionCard key={d.event_id} report={d} freshness={freshById[d.event_id]} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
