"use client";

import { useCallback, useEffect, useState } from "react";
import { ClipboardList } from "lucide-react";
import Link from "next/link";
import { eventsApi, type PredictionRecord } from "@/lib/api";
import { fmtDateTime, fmtPct, fmtSignedPct } from "@/lib/format";

function fmtBrier(n: number | null | undefined) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toFixed(3) : "—";
}

const RECENT_PREDICTIONS_PAGE_SIZE = 10;

export function RecentPredictions() {
  const [predictions, setPredictions] = useState<PredictionRecord[]>([]);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (nextPage = 0) => {
    if (nextPage < 0) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await eventsApi.recentPredictions(
        RECENT_PREDICTIONS_PAGE_SIZE,
        nextPage * RECENT_PREDICTIONS_PAGE_SIZE,
      );
      const nextPredictions = resp.predictions ?? [];
      setPredictions(nextPredictions);
      setTotal(resp.total ?? resp.count ?? nextPredictions.length);
      setPage(nextPage);
    } catch (e) {
      setError(e instanceof Error ? e.message : "预测记录加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(0), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const pageCount = Math.max(1, Math.ceil(total / RECENT_PREDICTIONS_PAGE_SIZE));
  const canGoPrevious = page > 0 && !loading;
  const canGoNext = (page + 1) * RECENT_PREDICTIONS_PAGE_SIZE < total && !loading;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ClipboardList className="size-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">最近预测记录</h2>
          <span className="font-mono text-xs text-muted-foreground">{total}</span>
        </div>
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
      {loading ? (
        <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
      ) : predictions.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">暂无冻结预测。</p>
      ) : (
        <ul className="divide-y divide-border">
          {predictions.map((p) => {
            const title = p.event_title_zh || p.event_title || p.event_id;
            return (
              <li
                key={p.id}
                className="grid grid-cols-2 gap-x-4 gap-y-2 py-3 text-sm md:grid-cols-[1fr_auto_auto_auto_auto]"
              >
                <div className="col-span-2 min-w-0 md:col-span-1">
                  <Link
                    href={`/events?id=${encodeURIComponent(p.event_id)}`}
                    className="truncate text-sm font-medium text-foreground hover:text-primary"
                    title={title}
                  >
                    {title}
                  </Link>
                  <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                    {p.event_id}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {p.platform || "market"} · {fmtDateTime(p.created_at)} · {p.status ?? "open"}
                  </p>
                </div>
                <div className="font-mono tabular-nums md:w-[72px] md:text-right">
                  AI {fmtPct(p.ai_probability)}
                </div>
                <div className="font-mono tabular-nums md:w-[92px] md:text-right">
                  市场 {fmtPct(p.market_probability)}
                </div>
                <div className="font-mono tabular-nums md:w-[76px] md:text-right">
                  {fmtSignedPct(p.raw_edge)}
                </div>
                <div className="font-mono tabular-nums text-muted-foreground md:w-[72px] md:text-right">
                  {fmtBrier(p.brier_score)}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {total > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-2 text-sm text-muted-foreground">
          <button
            type="button"
            onClick={() => void load(page - 1)}
            disabled={!canGoPrevious}
            className="inline-flex h-9 items-center rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            上一页
          </button>
          <span className="font-mono text-xs">
            第 {page + 1} / {pageCount} 页 · 共 {total} 条
          </span>
          <button
            type="button"
            onClick={() => void load(page + 1)}
            disabled={!canGoNext}
            className="inline-flex h-9 items-center rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            {loading ? "加载中…" : "下一页"}
          </button>
        </div>
      )}
    </section>
  );
}
