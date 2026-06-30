"use client";

import { useCallback, useEffect, useState } from "react";
import { AppNav } from "@/components/app-nav";
import { eventsApi, type SimTrade, type TradeStats } from "@/lib/api";
import { fmtSignedPct } from "@/lib/format";

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-4">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="mt-1 text-2xl font-bold tabular-nums text-foreground">{value}</span>
      {sub && <span className="mt-0.5 text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

function PnlBadge({ pnl }: { pnl: number | null }) {
  if (pnl == null) return <span className="text-muted-foreground">—</span>;
  const cls = pnl >= 0 ? "text-pos" : "text-neg";
  return <span className={`font-mono font-semibold tabular-nums ${cls}`}>{fmtSignedPct(pnl, 2)}%</span>;
}

export default function TradesPage() {
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [openTrades, setOpenTrades] = useState<SimTrade[]>([]);
  const [closedTrades, setClosedTrades] = useState<SimTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [s, o, c] = await Promise.all([
        eventsApi.tradeStats(),
        eventsApi.openTrades(),
        eventsApi.closedTrades(100),
      ]);
      setStats(s);
      setOpenTrades(o.trades ?? []);
      setClosedTrades(c.trades ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const fmtDate = (iso: string | null) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" }) + " " +
           d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <h1 className="text-xl font-semibold md:text-2xl">模拟交易</h1>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {loading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">加载中…</div>
        ) : (
          <>
            {/* Stats cards */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="总交易数（已结算）"
                value={String(stats?.total_closed ?? 0)}
                sub={stats?.win_rate != null ? `胜率 ${(stats.win_rate * 100).toFixed(1)}%` : "暂无结算"}
              />
              <StatCard
                label="累计 PnL"
                value={stats?.total_pnl_pct != null ? `${fmtSignedPct(stats.total_pnl_pct, 2)}%` : "—"}
              />
              <StatCard
                label="平均收益"
                value={stats?.avg_pnl_pct != null ? `${fmtSignedPct(stats.avg_pnl_pct, 2)}%` : "—"}
              />
              <StatCard
                label="平均入场 edge"
                value={stats?.avg_edge_at_entry != null ? `${fmtSignedPct(stats.avg_edge_at_entry, 2)}%` : "—"}
                sub="入场时的系统优势"
              />
            </div>

            {/* Direction breakdown */}
            {stats?.by_direction && Object.keys(stats.by_direction).length > 0 && (
              <section className="flex flex-col gap-2">
                <h2 className="text-sm font-semibold">按方向统计</h2>
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-secondary/50 text-left text-xs text-muted-foreground">
                        <th className="px-4 py-2">方向</th>
                        <th className="px-4 py-2">交易数</th>
                        <th className="px-4 py-2">胜</th>
                        <th className="px-4 py-2">胜率</th>
                        <th className="px-4 py-2">平均收益</th>
                        <th className="px-4 py-2">累计 PnL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(stats.by_direction).map(([dir, d]) => (
                        <tr key={dir} className="border-b border-border last:border-0">
                          <td className="px-4 py-2 font-medium">{dir}</td>
                          <td className="px-4 py-2 font-mono tabular-nums">{d.total}</td>
                          <td className="px-4 py-2 font-mono tabular-nums">{d.wins}</td>
                          <td className="px-4 py-2 font-mono tabular-nums">{(d.win_rate * 100).toFixed(1)}%</td>
                          <td className="px-4 py-2"><PnlBadge pnl={d.avg_pnl} /></td>
                          <td className="px-4 py-2"><PnlBadge pnl={d.total_pnl} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* Open positions */}
            <section className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold">当前持仓 ({openTrades.length})</h2>
              {openTrades.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                  暂无持仓。系统发现事件时会自动建立模拟交易。
                </p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-secondary/50 text-left text-xs text-muted-foreground">
                        <th className="px-4 py-2">事件</th>
                        <th className="px-4 py-2">方向</th>
                        <th className="px-4 py-2">入场概率</th>
                        <th className="px-4 py-2">入场 edge</th>
                        <th className="px-4 py-2">仓位%</th>
                        <th className="px-4 py-2">决策</th>
                        <th className="px-4 py-2">入场时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openTrades.map((t) => (
                        <tr key={t.trade_id} className="border-b border-border last:border-0">
                          <td className="max-w-[280px] truncate px-4 py-2 font-medium">{t.event_title || t.event_id.slice(0, 12)}</td>
                          <td className={`px-4 py-2 font-mono font-semibold ${t.direction === "YES" ? "text-pos" : "text-neg"}`}>{t.direction}</td>
                          <td className="px-4 py-2 font-mono tabular-nums">{t.entry_prob.toFixed(1)}%</td>
                          <td className="px-4 py-2">{fmtSignedPct(t.entry_edge, 1)}%</td>
                          <td className="px-4 py-2 font-mono tabular-nums">{t.position_pct.toFixed(1)}%</td>
                          <td className="px-4 py-2">
                            <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${
                              t.decision === "act" ? "bg-pos/10 text-pos" :
                              t.decision === "provisional_act" ? "bg-warn/10 text-warn" :
                              "bg-secondary text-muted-foreground"
                            }`}>{t.decision}</span>
                          </td>
                          <td className="px-4 py-2 text-xs text-muted-foreground">{fmtDate(t.entry_time)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Closed trades */}
            <section className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold">已平仓 ({closedTrades.length})</h2>
              {closedTrades.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                  暂无已平仓记录。事件结算后会自动计算盈亏。
                </p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-secondary/50 text-left text-xs text-muted-foreground">
                        <th className="px-4 py-2">事件</th>
                        <th className="px-4 py-2">方向</th>
                        <th className="px-4 py-2">入场→结算</th>
                        <th className="px-4 py-2">结果</th>
                        <th className="px-4 py-2">PnL</th>
                        <th className="px-4 py-2">原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {closedTrades.map((t) => (
                        <tr key={t.trade_id} className="border-b border-border last:border-0">
                          <td className="max-w-[280px] truncate px-4 py-2 font-medium">{t.event_title || t.event_id.slice(0, 12)}</td>
                          <td className={`px-4 py-2 font-mono font-semibold ${t.direction === "YES" ? "text-pos" : "text-neg"}`}>{t.direction}</td>
                          <td className="px-4 py-2 font-mono text-xs tabular-nums">{t.entry_prob.toFixed(0)}% → {t.actual_outcome?.toFixed(0) ?? "?"}%</td>
                          <td className={`px-4 py-2 font-mono tabular-nums ${t.is_win === 1 ? "text-pos" : "text-neg"}`}>
                            {t.is_win === 1 ? "赢" : t.is_win === 0 ? "输" : "—"}
                          </td>
                          <td className="px-4 py-2"><PnlBadge pnl={t.pnl_pct} /></td>
                          <td className="px-4 py-2 text-xs text-muted-foreground">{t.exit_reason ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
