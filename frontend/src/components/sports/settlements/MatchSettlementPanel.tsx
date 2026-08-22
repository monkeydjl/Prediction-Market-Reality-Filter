"use client";

import { useSettlement, type MarketSettlement } from "@/lib/sports-api";
import { ApiError } from "@/lib/api";

interface Props {
  matchId: string;
}

/**
 * One match's market-settlement rows — the read side of the button beside it.
 *
 * The match page already mounted `ProcessSettlementButton`, so an operator
 * could *trigger* settlement for a match and then had nowhere to see the
 * result: `/sport-settlements/{match_id}` and `useSettlement` both existed with
 * no caller, and the only rendering of settlement rows was the global 50-row
 * history table on `/sports/settlements`, which cannot be filtered by match.
 *
 * The 方向 column is deliberately tri-state. `direction_correct === null` means
 * the model landed on the market price (`raw_edge == 0`), so the closing line
 * had no direction to confirm — a no-call, not a miss. It is excluded from
 * `direction_accuracy` upstream (`market_settlement_service`), so showing it as
 * ✗ here would contradict the number the calibration panel publishes.
 */
export function MatchSettlementPanel({ matchId }: Props) {
  const { data, error, isLoading } = useSettlement(matchId || null);

  if (!matchId) return null;
  if (isLoading) {
    return (
      <p data-testid="match-settlement-loading" className="text-xs text-muted-foreground">
        加载结算记录…
      </p>
    );
  }

  const status = error instanceof ApiError ? error.status : null;
  // 404 is the ordinary pre-settlement state: the route raises it instead of
  // returning an empty list, so it is not an error worth alarming about.
  if (status === 404) {
    return (
      <p data-testid="match-settlement-empty" className="text-xs text-muted-foreground">
        尚无结算记录。比赛结束且有已验证市场链接后，可用右侧按钮重算。
      </p>
    );
  }
  // 503 = the phase flag is off. Kept as one muted line rather than an amber
  // banner: the flag is off by default, so every match page would carry it.
  if (status === 503) {
    return (
      <p data-testid="match-settlement-disabled" className="text-xs text-muted-foreground">
        市场结算反馈未启用（PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED）。
      </p>
    );
  }
  if (error) {
    return (
      <p data-testid="match-settlement-error" className="text-xs text-neg">
        结算记录加载失败：{error instanceof Error ? error.message : "未知错误"}
      </p>
    );
  }

  const items: MarketSettlement[] = data?.items ?? [];
  if (items.length === 0) {
    return (
      <p data-testid="match-settlement-empty" className="text-xs text-muted-foreground">
        尚无结算记录。比赛结束且有已验证市场链接后，可用右侧按钮重算。
      </p>
    );
  }

  return (
    <div className="w-full" data-testid="match-settlement-panel">
      <table className="w-full border-collapse text-xs" aria-label="本场结算记录">
        <thead>
          <tr className="border-b text-muted-foreground">
            <th className="p-1 text-left">结果</th>
            <th className="p-1 text-right">模型概率</th>
            <th className="p-1 text-right">结算概率</th>
            <th className="p-1 text-right">Brier</th>
            <th className="p-1 text-right">有向误差</th>
            <th className="p-1 text-center">方向</th>
            <th className="p-1 text-left">状态</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-b border-border/60">
              <td className="p-1">{s.mapped_outcome}</td>
              <td className="p-1 text-right font-mono">
                {s.model_prob !== null ? s.model_prob.toFixed(3) : "—"}
              </td>
              <td className="p-1 text-right font-mono">
                {s.settlement_implied_prob !== null
                  ? s.settlement_implied_prob.toFixed(3)
                  : "—"}
              </td>
              <td className="p-1 text-right font-mono">
                {s.brier_score !== null ? s.brier_score.toFixed(4) : "—"}
              </td>
              <td className="p-1 text-right font-mono">
                {s.signed_error !== null ? s.signed_error.toFixed(4) : "—"}
              </td>
              <td
                className="p-1 text-center"
                data-testid={`match-dir-${s.id}`}
                title={
                  s.direction_correct === 1
                    ? "收盘线朝 edge 方向移动"
                    : s.direction_correct === 0
                      ? "收盘线未朝 edge 方向移动"
                      : "模型与市场同价，未形成方向判断（不计入方向准确率）"
                }
              >
                {s.direction_correct === 1
                  ? "✓"
                  : s.direction_correct === 0
                    ? "✗"
                    : "—"}
              </td>
              <td className="p-1">
                {s.status}
                {s.skip_reason ? (
                  <span className="text-muted-foreground"> · {s.skip_reason}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
