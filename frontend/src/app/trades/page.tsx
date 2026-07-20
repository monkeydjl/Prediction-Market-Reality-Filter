"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PaginationControls } from "@/components/pagination-controls";
import { eventsApi, type SimTrade, type TradeStats } from "@/lib/api";
import { fmtSignedPct } from "@/lib/format";

const PAGE_SIZE = 10;

const DECISION_ORDER: Record<string, number> = { act: 0, provisional_act: 1, watch: 2 };
const DECISION_META: Record<string, { label: string }> = {
  act: { label: "正式行动" },
  provisional_act: { label: "临时行动" },
  watch: { label: "探索观察" },
};
const DECISION_BREAKDOWN_NOTE =
  "act 是校准后的正式行动；provisional_act 是冷启动/样本不足下的临时行动；watch 是探索观察样本，用于收集数据，不代表生产级胜率。";

function directionalEdgeOf(t: SimTrade): number {
  if (t.directional_edge != null && Number.isFinite(t.directional_edge)) {
    return t.directional_edge;
  }
  return t.direction === "NO" ? -t.entry_edge : t.entry_edge;
}

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
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
  return (
    <span className={`font-mono font-semibold tabular-nums ${cls}`}>
      {fmtSignedPct(pnl, 2)}%
    </span>
  );
}

export default function TradesPage() {
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [openTrades, setOpenTrades] = useState<SimTrade[]>([]);
  const [closedTrades, setClosedTrades] = useState<SimTrade[]>([]);
  const [openTotal, setOpenTotal] = useState(0);
  const [closedTotal, setClosedTotal] = useState(0);
  const [openPage, setOpenPage] = useState(0);
  const [closedPage, setClosedPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tradeView, setTradeView] = useState<"open" | "closed">("open");

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [s, o, c] = await Promise.all([
        eventsApi.tradeStats(),
        eventsApi.openTrades(PAGE_SIZE, openPage * PAGE_SIZE),
        eventsApi.closedTrades(PAGE_SIZE, closedPage * PAGE_SIZE),
      ]);
      const openRows = o.trades ?? [];
      const closedRows = c.trades ?? [];
      setStats(s);
      setOpenTrades(openRows);
      setClosedTrades(closedRows);
      setOpenTotal(o.total ?? o.count ?? openRows.length);
      setClosedTotal(c.total ?? c.count ?? closedRows.length);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [closedPage, openPage]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const fmtDate = (iso: string | null) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return (
      d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" }) +
      " " +
      d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    );
  };

  const decisionBreakdown = stats?.by_decision
    ? Object.entries(stats.by_decision).sort(
        ([a], [b]) =>
          (DECISION_ORDER[a] ?? 99) - (DECISION_ORDER[b] ?? 99) || a.localeCompare(b),
      )
    : [];

  return (
    <main
      id="main-content"
      className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8"
    >
      <div className="flex flex-col gap-1">
        <h1 className="text-balance text-xl font-semibold md:text-2xl">模拟交易</h1>
        <p className="text-sm text-muted-foreground">
          系统在 act / provisional_act / watch 决策时自动建立纸面交易，事件结算后计算 PnL。
          入场 edge 与事件预测、
          <Link href="/edges" className="text-primary underline underline-offset-2">
            事件 Edge 监测
          </Link>
          共用定义：
          <span className="font-mono text-foreground"> raw_edge = AI% − 市场%</span>
          （0–100 pp）。
        </p>
      </div>

      <div
        role="note"
        data-testid="edge-definition-banner"
        className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">Edge 定义（事件 EIP）</span>
          ：存储字段 <code className="rounded bg-muted px-1">entry_edge</code> = 原始
          raw_edge（与 predictions 一致）。表中「方向 edge」为持仓有利方向（YES 取 raw，NO
          取 −raw）。统计卡「|Edge|」为已平仓 |raw_edge| 均值。
        </p>
        <p className="mt-1">
          体育 Kernel 的 0–1 比赛 edge 见{" "}
          <Link
            href="/sports/edges"
            className="text-primary underline underline-offset-2"
          >
            体育 Edge
          </Link>
          ，勿与本页混读。
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">
          {error}
        </div>
      )}

      {loading && stats === null ? (
        <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
          加载中…
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard
              label="总交易数（已结算）"
              value={String(stats?.total_closed ?? 0)}
              sub={
                stats?.win_rate != null
                  ? `胜率 ${(stats.win_rate * 100).toFixed(1)}%`
                  : "暂无结算"
              }
            />
            <StatCard
              label="累计 PnL"
              value={
                stats?.total_pnl_pct != null
                  ? `${fmtSignedPct(stats.total_pnl_pct, 2)}%`
                  : "—"
              }
            />
            <StatCard
              label="平均收益"
              value={
                stats?.avg_pnl_pct != null
                  ? `${fmtSignedPct(stats.avg_pnl_pct, 2)}%`
                  : "—"
              }
            />
            <StatCard
              label="|Edge| 均值"
              value={
                stats?.avg_edge_at_entry != null
                  ? `${stats.avg_edge_at_entry.toFixed(1)}pp`
                  : "—"
              }
              sub="已平仓 |AI%−市场%| 平均幅度"
            />
            <StatCard
              label="方向 edge 均值"
              value={
                stats?.avg_directional_edge_at_entry != null
                  ? `${stats.avg_directional_edge_at_entry > 0 ? "+" : ""}${stats.avg_directional_edge_at_entry.toFixed(1)}pp`
                  : "—"
              }
              sub="YES→raw，NO→−raw"
            />
          </div>

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
                        <td className="px-4 py-2 font-mono tabular-nums">
                          {(d.win_rate * 100).toFixed(1)}%
                        </td>
                        <td className="px-4 py-2">
                          <PnlBadge pnl={d.avg_pnl} />
                        </td>
                        <td className="px-4 py-2">
                          <PnlBadge pnl={d.total_pnl} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {decisionBreakdown.length > 0 && (
            <section
              className="flex flex-col gap-2"
              data-testid="decision-performance-breakdown"
            >
              <h2 className="text-sm font-semibold">按决策类型统计</h2>
              <p className="text-xs leading-relaxed text-muted-foreground">
                {DECISION_BREAKDOWN_NOTE}
              </p>
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-secondary/50 text-left text-xs text-muted-foreground">
                      <th className="px-4 py-2">决策</th>
                      <th className="px-4 py-2">交易数</th>
                      <th className="px-4 py-2">赢</th>
                      <th className="px-4 py-2">胜率</th>
                      <th className="px-4 py-2">平均收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisionBreakdown.map(([decision, d]) => (
                      <tr
                        key={decision}
                        className="border-b border-border last:border-0"
                        data-testid={`decision-performance-${decision}`}
                      >
                        <td className="px-4 py-2 font-medium">
                          <div className="flex flex-col gap-0.5">
                            <span>{decision}</span>
                            <span className="text-[11px] font-normal text-muted-foreground">
                              {DECISION_META[decision]?.label ?? "未分类"}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-2 font-mono tabular-nums">{d.total}</td>
                        <td className="px-4 py-2 font-mono tabular-nums">{d.wins}</td>
                        <td className="px-4 py-2 font-mono tabular-nums">
                          {(d.win_rate * 100).toFixed(1)}%
                        </td>
                        <td className="px-4 py-2">
                          <PnlBadge pnl={d.avg_pnl} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {(
                [
                  { key: "open", label: "当前持仓", count: openTotal },
                  { key: "closed", label: "已平仓", count: closedTotal },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setTradeView(tab.key)}
                  className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                    tradeView === tab.key
                      ? "bg-secondary font-medium text-foreground"
                      : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
                  }`}
                >
                  {tab.label}
                  <span className="ml-1 font-mono text-xs text-muted-foreground">
                    {tab.count}
                  </span>
                </button>
              ))}
            </div>

            {tradeView === "open" ? (
              <section className="flex flex-col gap-2">
                <h2 className="text-sm font-semibold">当前持仓 ({openTotal})</h2>
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
                          <th className="px-4 py-2">市场概率</th>
                          <th className="px-4 py-2" title="raw_edge = AI% − 市场%">
                            raw edge
                          </th>
                          <th className="px-4 py-2" title="YES→raw，NO→−raw">
                            方向 edge
                          </th>
                          <th className="px-4 py-2">仓位%</th>
                          <th className="px-4 py-2">决策</th>
                          <th className="px-4 py-2">入场时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {openTrades.map((t) => (
                          <tr
                            key={t.trade_id}
                            className="border-b border-border last:border-0"
                          >
                            <td className="max-w-[280px] truncate px-4 py-2">
                              <Link
                                href={`/events?id=${encodeURIComponent(t.event_id)}`}
                                className="font-medium hover:text-primary"
                              >
                                {t.event_title || t.event_id.slice(0, 12)}
                              </Link>
                            </td>
                            <td
                              className={`px-4 py-2 font-mono font-semibold ${
                                t.direction === "YES" ? "text-pos" : "text-neg"
                              }`}
                            >
                              {t.direction}
                            </td>
                            <td className="px-4 py-2 font-mono tabular-nums">
                              {t.market_prob.toFixed(1)}%
                            </td>
                            <td className="px-4 py-2 font-mono tabular-nums">
                              {fmtSignedPct(t.entry_edge, 1)}pp
                            </td>
                            <td className="px-4 py-2 font-mono tabular-nums">
                              {fmtSignedPct(directionalEdgeOf(t), 1)}pp
                            </td>
                            <td className="px-4 py-2 font-mono tabular-nums">
                              {t.position_pct.toFixed(1)}%
                            </td>
                            <td className="px-4 py-2">
                              <span
                                className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${
                                  t.decision === "act"
                                    ? "bg-pos/10 text-pos"
                                    : t.decision === "provisional_act"
                                      ? "bg-warn/10 text-warn"
                                      : "bg-secondary text-muted-foreground"
                                }`}
                              >
                                {t.decision}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-xs text-muted-foreground">
                              {fmtDate(t.entry_time)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {openTotal > PAGE_SIZE && (
                  <PaginationControls
                    page={openPage}
                    pageSize={PAGE_SIZE}
                    total={openTotal}
                    loading={loading}
                    onPageChange={setOpenPage}
                  />
                )}
              </section>
            ) : (
              <section className="flex flex-col gap-2">
                <h2 className="text-sm font-semibold">已平仓 ({closedTotal})</h2>
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
                          <th className="px-4 py-2">市场→结算</th>
                          <th className="px-4 py-2">结果</th>
                          <th className="px-4 py-2">PnL</th>
                          <th className="px-4 py-2">入场时间</th>
                          <th className="px-4 py-2">原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {closedTrades.map((t) => (
                          <tr
                            key={t.trade_id}
                            className="border-b border-border last:border-0"
                          >
                            <td className="max-w-[280px] truncate px-4 py-2">
                              <Link
                                href={`/events?id=${encodeURIComponent(t.event_id)}`}
                                className="font-medium hover:text-primary"
                              >
                                {t.event_title || t.event_id.slice(0, 12)}
                              </Link>
                            </td>
                            <td
                              className={`px-4 py-2 font-mono font-semibold ${
                                t.direction === "YES" ? "text-pos" : "text-neg"
                              }`}
                            >
                              {t.direction}
                            </td>
                            <td className="px-4 py-2 font-mono text-xs tabular-nums">
                              {t.market_prob.toFixed(0)}% →{" "}
                              {t.actual_outcome?.toFixed(0) ?? "?"}%
                            </td>
                            <td
                              className={`px-4 py-2 font-mono tabular-nums ${
                                t.is_win === 1
                                  ? "text-pos"
                                  : t.is_win === 0
                                    ? "text-neg"
                                    : ""
                              }`}
                            >
                              {t.is_win === 1 ? "赢" : t.is_win === 0 ? "输" : "—"}
                            </td>
                            <td className="px-4 py-2">
                              <PnlBadge pnl={t.pnl_pct} />
                            </td>
                            <td className="px-4 py-2 text-xs text-muted-foreground">
                              {fmtDate(t.entry_time)}
                            </td>
                            <td className="px-4 py-2 text-xs text-muted-foreground">
                              {t.exit_reason ?? "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {closedTotal > PAGE_SIZE && (
                  <PaginationControls
                    page={closedPage}
                    pageSize={PAGE_SIZE}
                    total={closedTotal}
                    loading={loading}
                    onPageChange={setClosedPage}
                  />
                )}
              </section>
            )}
          </section>
        </>
      )}
    </main>
  );
}
