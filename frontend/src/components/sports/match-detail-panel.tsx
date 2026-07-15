"use client";

import { ProbabilityBar } from "./probability-bar";
import { FactorBreakdownTable } from "./factor-breakdown-table";
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
}

export function MatchDetailPanel({ match, prediction, onPredict, isPredicting }: MatchDetailPanelProps) {
  const icon = SPORT_ICONS[match.sport] ?? "❓";
  const kickoff = match.kickoff_utc
    ? new Date(match.kickoff_utc).toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "时间待定";

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
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onPredict}
          disabled={isPredicting}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {isPredicting ? "预测中..." : prediction ? "重新预测" : "预测"}
        </button>
        {prediction && prediction.prediction_timestamp && (
          <span className="text-xs text-muted-foreground">
            预测时间: {new Date(prediction.prediction_timestamp).toLocaleString("zh-CN")}
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

          <div>
            <h3 className="mb-2 text-sm font-medium">因子分解</h3>
            <FactorBreakdownTable items={prediction.explanation} />
          </div>

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
