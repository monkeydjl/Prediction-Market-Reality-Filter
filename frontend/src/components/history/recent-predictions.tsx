"use client";

import { useEffect, useState } from "react";
import { ClipboardList } from "lucide-react";
import { eventsApi, type PredictionRecord } from "@/lib/api";
import { fmtDateTime, fmtPct, fmtSignedPct } from "@/lib/format";

function fmtBrier(n: number | null | undefined) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toFixed(3) : "—";
}

export function RecentPredictions() {
  const [predictions, setPredictions] = useState<PredictionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const resp = await eventsApi.recentPredictions(50);
      setPredictions(resp.predictions ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "预测记录加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ClipboardList className="size-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">最近预测记录</h2>
          <span className="font-mono text-xs text-muted-foreground">{predictions.length}</span>
        </div>
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
      {loading ? (
        <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
      ) : predictions.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">暂无冻结预测。</p>
      ) : (
        <ul className="divide-y divide-border">
          {predictions.map((p) => (
            <li
              key={p.id}
              className="grid grid-cols-2 gap-x-4 gap-y-2 py-3 text-sm md:grid-cols-[1fr_auto_auto_auto_auto]"
            >
              <div className="col-span-2 min-w-0 md:col-span-1">
                <p className="truncate font-mono text-xs text-muted-foreground">{p.event_id}</p>
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
          ))}
        </ul>
      )}
    </section>
  );
}
