"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { eventsApi, type DailyDigestResponse } from "@/lib/api";
import { DeltaPill } from "@/components/indicators";
import { fmtDateTime, fmtPct, fmtSignedPct } from "@/lib/format";

// The digest rolls up a whole UTC day, so polling it every minute would be
// waste. Five minutes keeps a newly-published snapshot visible soon enough
// without re-parsing the event store on every tick. Exported so the
// visibility-gating test advances by the real interval instead of a literal
// that could silently stop covering the refresh path.
export const REFRESH_MS = 5 * 60_000;

export function DailyIntelDigestDashboard() {
  const [payload, setPayload] = useState<DailyDigestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const p = await eventsApi.digest();
      setPayload(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer the initial load to the next macrotask — matches the pattern in
    // quality-metrics-report-dashboard.tsx (avoids cascading renders from
    // set-state-in-effect).
    const timer = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => {
      if (document.hidden) return;
      void load();
    }, REFRESH_MS);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(interval);
    };
  }, [load]);

  if (loading && !payload) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">每日情报摘要</h1>
        <button
          type="button"
          onClick={load}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          刷新
        </button>
      </div>
      {error && (
        <div className="rounded-lg border border-neg/40 bg-neg/10 px-4 py-2 text-sm text-neg">
          {error}
        </div>
      )}

      {/* Overview bar */}
      <section className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm">
        <span className="rounded-md bg-secondary px-2 py-0.5 font-mono text-xs text-foreground">
          {payload?.date ?? "—"}
        </span>
        <span className="text-muted-foreground">
          变动事件 <span className="font-mono text-foreground">{payload?.count ?? 0}</span>
        </span>
        <span className="text-muted-foreground">
          生成于 <span className="font-mono text-foreground">{fmtDateTime(payload?.generated_at)}</span>
        </span>
      </section>

      {payload?.empty && (
        <section className="rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center text-muted-foreground">
          今日还没有产生任何概率快照，暂时无法生成摘要。
        </section>
      )}

      {/* Headline card */}
      {payload?.headline && (
        <section
          data-testid="digest-headline"
          className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-3"
        >
          <h2 className="mb-1.5 text-xs font-semibold text-primary">头条</h2>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <a
              href={`/events?id=${encodeURIComponent(payload.headline.event_id)}`}
              className="font-medium underline underline-offset-2 hover:text-primary"
            >
              {payload.headline.event_title || payload.headline.event_id}
            </a>
            <div className="flex items-center gap-2">
              <DeltaPill delta={payload.headline.day_net} />
              <span className="font-mono text-sm tabular-nums text-muted-foreground">
                现价 {fmtPct(payload.headline.close, 1)}
              </span>
            </div>
          </div>
        </section>
      )}

      {/* Movers table */}
      {payload && payload.movers.length > 0 && (
        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold">今日变动（按日内幅度降序）</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="digest-movers">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1.5 text-left font-medium">事件</th>
                  <th className="py-1.5 text-left font-medium">日内变动</th>
                  <th className="py-1.5 text-left font-medium">开盘 → 收盘</th>
                  <th className="py-1.5 text-left font-medium">基线</th>
                  <th className="py-1.5 text-left font-medium">观测</th>
                  <th className="py-1.5 text-left font-medium">更新于</th>
                </tr>
              </thead>
              <tbody>
                {payload.movers.map((m) => (
                  <tr key={m.event_id} className="border-t border-border">
                    <td className="py-1.5 pr-2">
                      <a
                        href={`/events?id=${encodeURIComponent(m.event_id)}`}
                        className="underline underline-offset-2 hover:text-primary"
                      >
                        {m.event_title || m.event_id}
                      </a>
                    </td>
                    <td className="py-1.5">
                      <DeltaPill delta={m.day_net} />
                    </td>
                    <td className="py-1.5 font-mono tabular-nums">
                      {fmtPct(m.open, 1)} → {fmtPct(m.close, 1)}
                    </td>
                    <td className="py-1.5 font-mono tabular-nums text-muted-foreground">
                      {m.open_source === "previous_close"
                        ? fmtSignedPct(m.all_time_net_change, 1)
                        : "首日"}
                    </td>
                    <td className="py-1.5 font-mono tabular-nums text-muted-foreground">
                      {m.day_observations}
                    </td>
                    <td className="py-1.5 font-mono tabular-nums text-muted-foreground">
                      {fmtDateTime(m.close_ts)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Unchanged shards */}
      {payload && (payload.unchanged.new_event_ids.length > 0 || payload.unchanged.quiet_event_ids.length > 0) && (
        <section className="grid gap-4 md:grid-cols-2">
          {payload.unchanged.new_event_ids.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="mb-2 text-sm font-semibold">
                新收录 <span className="font-mono text-xs text-muted-foreground">({payload.unchanged.new_event_ids.length})</span>
              </h2>
              <p className="mb-2 text-xs text-muted-foreground">今日首次收录，尚未有前收盘基线。</p>
              <ul className="space-y-1 text-xs">
                {payload.unchanged.new_event_ids.map((id) => (
                  <li key={id}>
                    <a
                      href={`/events?id=${encodeURIComponent(id)}`}
                      className="font-mono underline underline-offset-2 hover:text-primary"
                    >
                      {id}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {payload.unchanged.quiet_event_ids.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="mb-2 text-sm font-semibold">
                安静日 <span className="font-mono text-xs text-muted-foreground">({payload.unchanged.quiet_event_ids.length})</span>
              </h2>
              <p className="text-xs text-muted-foreground">已建立事件今日波动小于 0.5pt，无需要关注的信号。</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
