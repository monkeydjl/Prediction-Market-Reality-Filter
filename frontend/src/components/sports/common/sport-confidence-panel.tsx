import type { ContributionItem, PredictionResult } from "@/lib/sports-api";

const OUTCOME_ZH: Record<string, string> = {
  home_win: "主胜",
  away_win: "客胜",
  draw: "平局",
};

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function decisionStrength(probs: Record<string, number>): number {
  const vals = Object.values(probs).filter((v) => Number.isFinite(v));
  if (vals.length === 0) return 0;
  const total = vals.reduce((a, b) => a + b, 0) || 1;
  const peak = Math.max(...vals.map((v) => v / total));
  return Math.max(0, Math.min(1, (peak - 1 / 3) / (2 / 3)));
}

function completeness(items: ContributionItem[]): number {
  if (!items.length) return 0.5;
  return items.filter((i) => i.available).length / items.length;
}

function agreement(items: ContributionItem[], final: string | null): number {
  const votes = items.filter((i) => i.available && i.predicted_outcome);
  if (!votes.length) return 0.5;
  if (!final) return 0.5;
  return votes.filter((i) => i.predicted_outcome === final).length / votes.length;
}

type ConfidenceBreakdown = {
  total?: number;
  decision_strength?: number;
  data_completeness?: number;
  factor_agreement?: number;
  market_damp?: number;
  factors_available?: number;
  factors_total?: number;
  final_outcome?: string | null;
};

function readBreakdown(
  prediction: Pick<PredictionResult, "betting_analysis">,
): ConfidenceBreakdown | null {
  const ba = prediction.betting_analysis;
  if (!ba || typeof ba !== "object") return null;
  const raw = (ba as Record<string, unknown>).confidence_breakdown;
  if (!raw || typeof raw !== "object") return null;
  return raw as ConfidenceBreakdown;
}

interface SportConfidencePanelProps {
  prediction: Pick<
    PredictionResult,
    "confidence" | "outcome_probabilities" | "explanation" | "betting_analysis"
  >;
}

/** Kernel confidence diagnostics (P1-X3) — prefers API confidence_breakdown. */
export function SportConfidencePanel({ prediction }: SportConfidencePanelProps) {
  const probs = prediction.outcome_probabilities ?? {};
  const items = prediction.explanation ?? [];
  const api = readBreakdown(prediction);

  const finalFromProbs =
    Object.keys(probs).length > 0
      ? Object.entries(probs).sort((a, b) => b[1] - a[1])[0][0]
      : null;
  const final = api?.final_outcome ?? finalFromProbs;
  const strength = api?.decision_strength ?? decisionStrength(probs);
  const complete = api?.data_completeness ?? completeness(items);
  const agree = api?.factor_agreement ?? agreement(items, final);
  const available = api?.factors_available ?? items.filter((i) => i.available).length;
  const total = api?.factors_total ?? items.length;
  const damp = api?.market_damp;

  return (
    <div
      className="rounded-lg border border-border bg-card p-4"
      data-testid="sport-confidence-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">置信度分解</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            决策强度、因子完整度与因子一致性（Kernel soft 合成）
          </p>
        </div>
        <span className="rounded bg-secondary px-2 py-1 font-mono text-xs font-medium tabular-nums">
          总置信度 {pct(prediction.confidence)}
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">决策强度</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {pct(strength)}
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">因子完整度</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {pct(complete)}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {available}/{total || 0} 可用
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">因子一致性</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {pct(agree)}
          </div>
          {damp != null && damp < 0.999 && (
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              市场阻尼 ×{damp.toFixed(2)}
            </div>
          )}
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">主预测</div>
          <div className="mt-1 text-lg font-semibold">
            {final ? OUTCOME_ZH[final] ?? final : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
