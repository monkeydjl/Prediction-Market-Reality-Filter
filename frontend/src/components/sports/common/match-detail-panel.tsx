"use client";

import { ProbabilityBar } from "./probability-bar";
import { FactorBreakdownTable } from "./factor-breakdown-table";
import { SportConfidencePanel } from "./sport-confidence-panel";
import { SoftTotalsPanel } from "./soft-totals-panel";
import { MarketPriceAuditPanel } from "@/components/sports/markets/market-price-audit-panel";
import type { MatchDetail, PredictionResult } from "@/lib/sports-api";

const SPORT_ICONS: Record<string, string> = {
  football: "⚽",
  basketball: "🏀",
  baseball: "⚾",
  hockey: "🏒",
};

interface MatchDetailPanelProps {
  match: MatchDetail;
  prediction: PredictionResult | null;
  onPredict: () => void;
  isPredicting: boolean;
  engines?: string[];
  selectedEngine?: string;
  onEngineChange?: (engine: string) => void;
}

export function MatchDetailPanel({
  match,
  prediction,
  onPredict,
  isPredicting,
  engines,
  selectedEngine = "auto",
  onEngineChange,
}: MatchDetailPanelProps) {
  const icon = SPORT_ICONS[match.sport] ?? "❓";
  const kickoff = match.kickoff_utc
    ? new Date(match.kickoff_utc).toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "时间待定";

  const engineOptions = engines && engines.length > 0
    ? ["auto", ...engines.filter((e) => e !== "auto")]
    : ["auto"];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="text-3xl" aria-hidden="true">{icon}</span>
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl font-semibold">{match.home_team}</span>
            <span className="text-muted-foreground">vs</span>
            <span className="text-xl font-semibold">{match.away_team}</span>
          </div>
          <div className="text-sm text-muted-foreground">
            <span className="rounded bg-secondary px-1.5 py-0.5 text-xs">{match.competition}</span>
            {" "}
            {kickoff}
          </div>
        </div>
      </div>

      {/* Action area */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          引擎
          <select
            data-testid="engine-select"
            value={selectedEngine}
            onChange={(e) => onEngineChange?.(e.target.value)}
            className="rounded border bg-background px-2 py-1 text-foreground"
            aria-label="选择预测引擎"
          >
            {engineOptions.map((eng) => (
              <option key={eng} value={eng}>
                {eng}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={onPredict}
          disabled={isPredicting}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {isPredicting ? "预测中..." : prediction ? "重新预测" : "预测"}
        </button>
        {prediction && (
          <span className="text-xs text-muted-foreground">
            {prediction.engine ? `引擎: ${prediction.engine}` : null}
            {prediction.prediction_timestamp
              ? ` · ${new Date(prediction.prediction_timestamp).toLocaleString("zh-CN")}`
              : null}
          </span>
        )}
      </div>

      {/* Prediction result area */}
      {prediction && (
        <div className="space-y-4">
          <div>
            <h3 className="mb-2 text-sm font-medium">胜率概率</h3>
            <ProbabilityBar
              probabilities={prediction.outcome_probabilities}
              homeTeam={match.home_team}
              awayTeam={match.away_team}
            />
          </div>

          {Object.keys(prediction.predicted_scores).length > 0 && (
            <div className="flex items-center gap-4">
              <div>
                <span className="text-xs text-muted-foreground">预测比分</span>
                <div className="font-mono text-lg">
                  {Object.entries(prediction.predicted_scores).map(([k, v]) => `${k}: ${v.toFixed(1)}`).join(" | ")}
                </div>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">置信度</span>
                <div className="font-mono text-lg">{(prediction.confidence * 100).toFixed(1)}%</div>
              </div>
            </div>
          )}

          <SportConfidencePanel prediction={prediction} />

          <SoftTotalsPanel prediction={prediction} />

          <MarketPriceAuditPanel matchId={match.match_id} />

          <div>
            <h3 className="mb-2 text-sm font-medium">因子分解</h3>
            <FactorBreakdownTable items={prediction.explanation} />
          </div>

          {prediction.betting_analysis &&
            typeof prediction.betting_analysis === "object" &&
            (prediction.betting_analysis as { situational_applied?: boolean })
              .situational_applied && (
              <div className="rounded-md border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground">
                情境调整已应用
                {Array.isArray(
                  (prediction.betting_analysis as { situational_notes?: string[] })
                    .situational_notes,
                )
                  ? `：${(
                      (prediction.betting_analysis as { situational_notes?: string[] })
                        .situational_notes ?? []
                    ).join(" · ")}`
                  : null}
              </div>
            )}

          <div>
            <span className="rounded bg-secondary px-2 py-1 text-xs font-mono">
              {prediction.feature_version}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
