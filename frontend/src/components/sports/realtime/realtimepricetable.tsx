"use client";

import { usePriceStream, type PriceUpdate } from "@/lib/use-price-stream";
import { ScrollableTable } from "@/components/ui/scrollable-table";
import { RealtimePriceIndicator } from "./RealtimePriceIndicator";

interface RealtimePriceTableProps {
  matchId: string;
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

  const sortedUpdates: PriceUpdate[] = [...updates].reverse();

  return (
    <div data-testid="realtime-price-table">
      <div className="mb-2 flex items-center">
        <span className="text-sm font-medium">实时价格</span>
        <RealtimePriceIndicator isConnected={isConnected} matchId={matchId} />
      </div>

      {disabled && (
        <div
          data-testid="ws-disabled"
          className="mb-3 rounded border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900"
        >
          {error?.message ||
            "实时推送未启用。请设置 PHASE10_REALTIME_PUSH_ENABLED=true。"}
        </div>
      )}

      {!disabled && error && !isConnected && (
        <div
          data-testid="ws-error"
          className="mb-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800"
        >
          {error.message}
        </div>
      )}

      {sortedUpdates.length === 0 ? (
        <div data-testid="empty" className="text-sm text-gray-500">
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
              {sortedUpdates.map((u, idx) => (
                <tr key={`${u.captured_at ?? ""}-${u.type}-${idx}`} className="border-b">
                  <td className="py-1 pr-2 tnum">{formatTime(u.captured_at)}</td>
                  <td className="py-1 pr-2">{u.type ?? "-"}</td>
                  <td className="py-1 pr-2">{u.outcome ?? "-"}</td>
                  <td className="py-1 pr-2 tnum">{formatPercent(u.implied_prob)}</td>
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
