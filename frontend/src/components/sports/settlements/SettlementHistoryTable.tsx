"use client";
import { useState } from "react";
import Link from "next/link";
import {
  useSettlementHistory,
  type MarketSettlement,
} from "@/lib/sports-api";
import { ProcessSettlementButton } from "./processsettlementbutton";
import { matchDetailHref } from "@/lib/sports-routes";
import {
  FeatureDisabledBanner,
  isServiceUnavailable,
} from "@/components/sports/common/feature-disabled-banner";

export function SettlementHistoryTable() {
  const [engineFilter, setEngineFilter] = useState<string>("");
  const [manualMatchId, setManualMatchId] = useState("");
  const { data, error, isLoading, mutate } = useSettlementHistory(
    50,
    engineFilter || undefined,
  );
  const items: MarketSettlement[] = data?.items ?? [];
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;
  const disabled = isServiceUnavailable(error);

  if (isLoading) return <div data-testid="loading">加载中...</div>;

  if (disabled) {
    return (
      <FeatureDisabledBanner
        flag="PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED=true"
        title="市场结算反馈未启用"
        testId="settlements-disabled"
      />
    );
  }

  if (errorMessage) return <div data-testid="error">错误: {errorMessage}</div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2 rounded border p-3">
        <div>
          <label className="block text-xs text-muted-foreground">
            按 match_id 手动触发结算
          </label>
          <input
            value={manualMatchId}
            onChange={(e) => setManualMatchId(e.target.value)}
            placeholder="例如 epl-123"
            data-testid="manual-match-id"
            className="mt-1 border px-2 py-1 text-sm"
          />
        </div>
        <ProcessSettlementButton
          matchId={manualMatchId.trim()}
          onDone={() => mutate()}
        />
        <p className="w-full text-xs text-muted-foreground">
          需 write key；比赛需有可结算的市场链接与结果。
        </p>
      </div>

      <div className="mb-2 flex gap-2">
        <input
          value={engineFilter}
          onChange={(e) => setEngineFilter(e.target.value)}
          placeholder="按引擎过滤"
          data-testid="engine-filter"
          className="border px-2 py-1"
        />
      </div>

      {items.length === 0 ? (
        <div data-testid="empty" className="text-sm text-muted-foreground">
          暂无结算记录。可用上方表单按 match_id 触发，或等待调度自动结算。
        </div>
      ) : (
        <table
          data-testid="settlements-table"
          className="w-full border-collapse text-sm"
        >
          <thead>
            <tr className="border-b">
              <th className="p-1 text-left">比赛</th>
              <th className="p-1 text-left">引擎</th>
              <th className="p-1 text-left">赛事</th>
              <th className="p-1 text-left">结果</th>
              <th className="p-1 text-right">模型概率</th>
              <th className="p-1 text-right">结算概率</th>
              <th className="p-1 text-right">Brier</th>
              <th className="p-1 text-center">方向</th>
              <th className="p-1 text-left">状态</th>
              <th className="p-1 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id} className="border-b">
                <td className="p-1">
                  <Link
                    href={matchDetailHref(s.match_id)}
                    className="text-primary hover:underline"
                  >
                    {s.match_id}
                  </Link>
                </td>
                <td className="p-1">{s.engine}</td>
                <td className="p-1">{s.competition}</td>
                <td className="p-1">{s.mapped_outcome}</td>
                <td className="p-1 text-right">
                  {s.model_prob !== null ? s.model_prob.toFixed(3) : "—"}
                </td>
                <td className="p-1 text-right">
                  {s.settlement_implied_prob !== null
                    ? s.settlement_implied_prob.toFixed(3)
                    : "—"}
                </td>
                <td className="p-1 text-right">
                  {s.brier_score !== null ? s.brier_score.toFixed(4) : "—"}
                </td>
                <td className="p-1 text-center" data-testid={`dir-${s.id}`}>
                  {s.direction_correct === 1
                    ? "✓"
                    : s.direction_correct === 0
                      ? "✗"
                      : "—"}
                </td>
                <td className="p-1">{s.status}</td>
                <td className="p-1">
                  <ProcessSettlementButton
                    matchId={s.match_id}
                    onDone={() => mutate()}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
