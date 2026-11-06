"use client";

import { useMemo } from "react";
import { usePriceStream, type PriceUpdate } from "@/lib/use-price-stream";
import { ScrollableTable } from "@/components/ui/scrollable-table";
import { RealtimePriceIndicator } from "./RealtimePriceIndicator";

interface RealtimePriceTableProps {
  matchId: string;
}

/** Same-series key: a snapshot only compares against the previous snapshot of
 *  the same (type, outcome, bookmaker) stream — crossing series would compare
 *  prices from different books. */
function seriesKey(u: PriceUpdate): string {
  return `${u.type ?? ""}|${u.outcome ?? ""}|${u.bookmaker ?? ""}`;
}

/** Per-update implied-probability delta vs the previous snapshot of the same
 *  series (null = series first sample). Direction coloring follows the CN
 *  convention 涨红跌绿: prob rising = red (--up), falling = green (--down). */
const PROB_DEADBAND = 0.005; // 0.5pt, mirrors DeltaPill's flat band

function computeProbDeltas(updates: PriceUpdate[]): (number | null)[] {
  const lastProbBySeries = new Map<string, number>();
  return updates.map((u) => {
    const key = seriesKey(u);
    const previous = lastProbBySeries.get(key);
    lastProbBySeries.set(key, u.implied_prob);
    if (previous === undefined || !Number.isFinite(u.implied_prob)) return null;
    const delta = u.implied_prob - previous;
    return Math.abs(delta) < PROB_DEADBAND ? null : delta;
  });
}

function formatDeltaPt(delta: number | null): string | null {
  if (delta === null) return null;
  const pt = delta * 100;
  return `${pt > 0 ? "▲" : "▼"}${pt > 0 ? "+" : ""}${pt.toFixed(1)}pt`;
}

function formatTime(capturedAt: string | undefined): string {
  if (!capturedAt) return "-";
  try {
    const d = new Date(capturedAt);
    if (Number.isNaN(d.getTime())) return capturedAt;
    return d.toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    return capturedAt;
  }
}

function formatPercent(prob: number | undefined): string {
  if (prob === undefined || prob === null) return "-";
  return `${(prob * 100).toFixed(1)}%`;
}

function formatNumber(value: number | undefined, digits = 2): string {
  if (value === undefined || value === null) return "-";
  return value.toFixed(digits);
}

export function RealtimePriceTable({ matchId }: RealtimePriceTableProps) {
  const { updates, isConnected, error, disabled } = usePriceStream(matchId);

  // Latest first; deltas computed chronologically then reversed in lockstep.
  const rows = useMemo(() => {
    const deltas = computeProbDeltas(updates);
    return updates
      .map((update, i) => ({ update, delta: deltas[i] }))
      .reverse();
  }, [updates]);

  return (
    <div data-testid="realtime-price-table">
      <div className="mb-2 flex items-center">
        <span className="text-sm font-medium">实时价格</span>
        <RealtimePriceIndicator isConnected={isConnected} matchId={matchId} />
      </div>

      {disabled && (
        <div
          data-testid="ws-disabled"
          className="mb-3 rounded border border-warn/40 bg-warn/10 p-3 text-sm text-warn"
        >
          {error?.message ||
            "实时推送未启用。请设置 PHASE10_REALTIME_PUSH_ENABLED=true。"}
        </div>
      )}

      {!disabled && error && !isConnected && (
        <div
          data-testid="ws-error"
          className="mb-3 rounded border border-neg/40 bg-neg/10 p-3 text-sm text-neg"
        >
          {error.message}
        </div>
      )}

      {rows.length === 0 ? (
        <div data-testid="empty" className="text-sm text-muted-foreground">
          {disabled
            ? "推送已关闭，无实时数据"
            : isConnected
              ? "已连接，等待价格快照（调度器抓取后会推送）..."
              : "未连接到实时数据源"}
        </div>
      ) : (
        <ScrollableTable aria-label="实时价格快照">
          <table data-testid="price-table" className="w-full min-w-[44rem] text-sm">
            <thead>
              <tr className="border-b text-left">
                <th className="py-1 pr-2">时间</th>
                <th className="py-1 pr-2">类型</th>
                <th className="py-1 pr-2">结果</th>
                <th className="py-1 pr-2">隐含概率</th>
                <th className="py-1 pr-2">价格</th>
                <th className="py-1 pr-2">赔率</th>
                <th className="py-1 pr-2">来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ update: u, delta }, idx) => (
                <tr key={`${u.captured_at ?? ""}-${u.type}-${idx}`} className="border-b">
                  <td className="py-1 pr-2 tnum">{formatTime(u.captured_at)}</td>
                  <td className="py-1 pr-2">{u.type ?? "-"}</td>
                  <td className="py-1 pr-2">{u.outcome ?? "-"}</td>
                  <td className="py-1 pr-2 tnum">
                    {formatPercent(u.implied_prob)}
                    {delta !== null && (
                      <span
                        className={`ml-1.5 font-mono text-xs font-medium tabular-nums ${
                          delta > 0 ? "text-up" : "text-down"
                        }`}
                        data-testid={`prob-delta-${idx}`}
                      >
                        {formatDeltaPt(delta)}
                      </span>
                    )}
                  </td>
                  <td className="py-1 pr-2 tnum">{formatNumber(u.price)}</td>
                  <td className="py-1 pr-2 tnum">{formatNumber(u.decimal_odds)}</td>
                  <td className="py-1 pr-2">{u.bookmaker ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      )}
    </div>
  );
}
