import type { PredictionResult } from "@/lib/sports-api";

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

interface SoftTotalsPanelProps {
  prediction: Pick<PredictionResult, "betting_analysis" | "predicted_scores">;
}

/** Soft O/U + BTTS from engine Poisson scores (P1-O1 scaffolding). */
export function SoftTotalsPanel({ prediction }: SoftTotalsPanelProps) {
  const ba = prediction.betting_analysis as
    | { soft_totals_btts?: Record<string, unknown> }
    | null
    | undefined;
  const soft = ba?.soft_totals_btts;
  if (!soft || soft.available !== true) {
    return null;
  }
  const line = Number(soft.line ?? 2.5);
  const pOver = Number(soft.p_over ?? 0);
  const pUnder = Number(soft.p_under ?? 0);
  const hasBtts =
    soft.p_btts_yes != null && soft.p_btts_no != null;
  const pYes = Number(soft.p_btts_yes ?? 0);
  const pNo = Number(soft.p_btts_no ?? 0);
  const exp = Number(soft.expected_total ?? 0);
  const sport = String(soft.sport ?? "football");
  const totalLabel =
    sport === "basketball"
      ? "期望总分"
      : sport === "baseball"
        ? "期望总得分"
        : sport === "hockey"
          ? "期望总进球"
          : "期望总进球";

  return (
    <div
      className="rounded-lg border border-border bg-card p-4"
      data-testid="soft-totals-btts"
    >
      <h3 className="text-sm font-semibold">
        {hasBtts ? "软大小球 / 双方进球" : "软大小分"}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        独立泊松估计（非完整多玩法盘口；P1-O1 脚手架）
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">{totalLabel}</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {exp.toFixed(2)}
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">大 {line}</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {pct(pOver)}
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">小 {line}</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {pct(pUnder)}
          </div>
        </div>
        {hasBtts && (
          <div className="rounded border border-border/70 p-3">
            <div className="text-xs text-muted-foreground">双方进球 是/否</div>
            <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
              {pct(pYes)} / {pct(pNo)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
