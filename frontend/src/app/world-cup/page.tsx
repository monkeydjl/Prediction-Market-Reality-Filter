"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Trophy,
} from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { WorldCupDataSources } from "@/components/dashboard/world-cup-data-sources";
import { WorldCupResolutionPanel } from "@/components/dashboard/world-cup-resolution-panel";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { eventsApi } from "@/lib/api";
import type { TrackedEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

type CategoryKey = "team_progression" | "match_format" | "discipline" | "player_awards" | "tournament_totals" | "group_stage";

const CATEGORY_META: Record<CategoryKey, { label: string; tone: string }> = {
  group_stage: { label: "小组赛", tone: "text-primary" },
  team_progression: { label: "球队晋级", tone: "text-primary" },
  match_format: { label: "赛制形式", tone: "text-warn" },
  discipline: { label: "纪律处分", tone: "text-neg" },
  player_awards: { label: "球员奖项", tone: "text-pos" },
  tournament_totals: { label: "赛事统计", tone: "text-muted-foreground" },
};

function getCategory(entry: TrackedEntry): CategoryKey | "other" {
  const cat = entry.record?.source?.category;
  if (cat && cat in CATEGORY_META) return cat as CategoryKey;
  return "other";
}

function outcomeLabel(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  return `${Number(value).toFixed(0)}%`;
}

function probabilityTone(p: number | undefined) {
  if (p == null) return "";
  if (p >= 70) return "text-pos";
  if (p <= 30) return "text-neg";
  return "text-warn";
}

function EventRow({ entry }: { entry: TrackedEntry }) {
  const record = entry.record;
  const estimated = record.probability?.estimated;
  const outcome = record.outcome;
  const resolved = outcome?.actual_outcome != null;

  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-3 py-2.5">
      <div className="min-w-0">
        <Link
          href={`/events?id=${encodeURIComponent(entry.event_id)}`}
          className="block truncate text-sm font-medium text-foreground hover:text-primary"
        >
          {record.event_title_zh || record.event_title || entry.event_id}
        </Link>
        <span className="text-[11px] text-muted-foreground">
          {record.source?.source_id || entry.event_id}
        </span>
      </div>
      <div className="flex items-center gap-3">
        {resolved ? (
          <span className="flex items-center gap-1 rounded-md border border-pos/40 bg-pos/10 px-2 py-0.5 text-[11px] font-medium text-pos">
            <CheckCircle2 className="size-3" aria-hidden="true" />
            {outcomeLabel(outcome.actual_outcome)}
          </span>
        ) : (
          <span className={cn("font-mono text-sm font-semibold tabular-nums", probabilityTone(estimated))}>
            {estimated != null ? `${estimated.toFixed(0)}%` : "—"}
          </span>
        )}
      </div>
    </li>
  );
}

function CategorySection({ category, entries }: { category: CategoryKey; entries: TrackedEntry[] }) {
  const meta = CATEGORY_META[category];
  return (
    <section className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <h3 className={cn("text-sm font-semibold", meta.tone)}>{meta.label}</h3>
        <span className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
          {entries.length}
        </span>
      </div>
      <ul className="divide-y divide-border">
        {entries.map((entry) => (
          <EventRow key={entry.event_id} entry={entry} />
        ))}
      </ul>
    </section>
  );
}

export default function WorldCupPage() {
  const [entries, setEntries] = useState<TrackedEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await eventsApi.list(100, 0, { category: "all" });
      const worldCup = (result.events ?? []).filter(
        (e) => e.record?.source?.type === "sports_event"
      );
      setEntries(worldCup);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载世界杯事件失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const grouped = useMemo(() => {
    const groups: Record<string, TrackedEntry[]> = {};
    for (const entry of entries) {
      const cat = getCategory(entry);
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(entry);
    }
    return groups;
  }, [entries]);

  const categoryOrder: CategoryKey[] = ["group_stage", "team_progression", "match_format", "discipline", "player_awards", "tournament_totals"];
  const resolvedCount = entries.filter((e) => e.record?.outcome?.actual_outcome != null).length;
  const pendingCount = entries.length - resolvedCount;

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <AppNav />
      <main id="main-content" className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Trophy className="size-5" aria-hidden="true" />
            </span>
            <div>
              <h1 className="text-lg font-semibold">2026 FIFA World Cup</h1>
              <p className="text-xs text-muted-foreground">
                {entries.length} 事件 · {pendingCount} 待定 · {resolvedCount} 已结算
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-3 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="size-3.5" aria-hidden="true" />
            )}
            刷新
          </button>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {error}
          </div>
        )}

        {!loading && entries.length === 0 && !error && (
          <div className="grid h-32 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            暂无世界杯事件。请先通过数据源面板导入事件。
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          {categoryOrder.map((cat) =>
            grouped[cat] && grouped[cat].length > 0 ? (
              <CategorySection key={cat} category={cat} entries={grouped[cat]} />
            ) : null
          )}
          {grouped["other"] && grouped["other"].length > 0 && (
            <section className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-muted-foreground">其他</h3>
                <span className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                  {grouped["other"].length}
                </span>
              </div>
              <ul className="divide-y divide-border">
                {grouped["other"].map((entry) => (
                  <EventRow key={entry.event_id} entry={entry} />
                ))}
              </ul>
            </section>
          )}
        </div>

        <SectionErrorBoundary title="世界杯数据源">
          <WorldCupDataSources />
        </SectionErrorBoundary>

        <SectionErrorBoundary title="世界杯结算">
          <WorldCupResolutionPanel />
        </SectionErrorBoundary>
      </main>
    </div>
  );
}
