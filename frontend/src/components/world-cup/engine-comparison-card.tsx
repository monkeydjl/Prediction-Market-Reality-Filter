"use client";

import { useState } from "react";
import { Zap, Brain, TrendingUp, AlertCircle, ChevronDown, ChevronUp } from "lucide-react";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup-predictions";
import { cn } from "@/lib/utils";

interface EngineComparisonCardProps {
  match: MatchFixture;
  eloOddsPrediction?: MatchPrediction;
  hybridPrediction?: MatchPrediction;
  isLoading?: boolean;
}

function probabilityBar(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

function PredictionColumn({
  label,
  icon: Icon,
  color,
  prediction
}: {
  label: string;
  icon: any;
  color: string;
  prediction?: MatchPrediction;
}) {
  if (!prediction) {
    return (
      <div className="flex-1 rounded-lg border border-dashed bg-secondary/30 p-4 text-center">
        <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Icon className="size-4" />
          <span>{label}</span>
        </div>
        <div className="mt-4 text-xs text-muted-foreground">暂无预测</div>
      </div>
    );
  }

  const highestOutcome = (() => {
    const probs = prediction.outcome_probabilities;
    if (probs.home_win >= probs.draw && probs.home_win >= probs.away_win) return "home";
    if (probs.away_win >= probs.draw) return "away";
    return "draw";
  })();

  return (
    <div className="flex-1 rounded-lg border bg-card p-4">
      {/* Engine Header */}
      <div className="flex items-center justify-between border-b pb-2">
        <div className={cn("flex items-center gap-2 text-sm font-medium", color)}>
          <Icon className="size-4" />
          <span>{label}</span>
        </div>
        <span className="text-xs text-muted-foreground">
          {probabilityBar(prediction.confidence)}
        </span>
      </div>

      {/* Scores */}
      <div className="mt-3 grid grid-cols-3 items-center gap-2 text-center">
        <div className={cn(
          "font-mono text-xl font-bold tabular-nums",
          highestOutcome === "home" ? "text-primary" : "text-muted-foreground"
        )}>
          {prediction.predicted_score.home.toFixed(1)}
        </div>
        <div className="text-xs text-muted-foreground">vs</div>
        <div className={cn(
          "font-mono text-xl font-bold tabular-nums",
          highestOutcome === "away" ? "text-primary" : "text-muted-foreground"
        )}>
          {prediction.predicted_score.away.toFixed(1)}
        </div>
      </div>

      {/* Outcome Probabilities - Compact */}
      <div className="mt-3 space-y-1.5">
        <div className="flex items-center justify-between text-xs">
          <span className="w-8 text-muted-foreground">主</span>
          <div className="h-1 flex-1 mx-2 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: probabilityBar(prediction.outcome_probabilities.home_win) }}
            />
          </div>
          <span className="w-10 text-right font-mono text-xs tabular-nums">
            {probabilityBar(prediction.outcome_probabilities.home_win)}
          </span>
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="w-8 text-muted-foreground">平</span>
          <div className="h-1 flex-1 mx-2 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full bg-muted-foreground transition-all"
              style={{ width: probabilityBar(prediction.outcome_probabilities.draw) }}
            />
          </div>
          <span className="w-10 text-right font-mono text-xs tabular-nums">
            {probabilityBar(prediction.outcome_probabilities.draw)}
          </span>
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="w-8 text-muted-foreground">客</span>
          <div className="h-1 flex-1 mx-2 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: probabilityBar(prediction.outcome_probabilities.away_win) }}
            />
          </div>
          <span className="w-10 text-right font-mono text-xs tabular-nums">
            {probabilityBar(prediction.outcome_probabilities.away_win)}
          </span>
        </div>
      </div>

      {/* Elo Ratings (if available) */}
      {prediction.elo_ratings && (
        <div className="mt-3 rounded border bg-secondary/30 px-2 py-1.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Elo</span>
            <span className="font-mono font-medium tabular-nums">
              {Math.round(prediction.elo_ratings.home)} - {Math.round(prediction.elo_ratings.away)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export function EngineComparisonCard({
  match,
  eloOddsPrediction,
  hybridPrediction,
  isLoading
}: EngineComparisonCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // Calculate agreement level
  const agreement = (() => {
    if (!eloOddsPrediction || !hybridPrediction) return null;

    const elo_winner = eloOddsPrediction.outcome_probabilities.home_win >= eloOddsPrediction.outcome_probabilities.away_win ? "home" : "away";
    const hybrid_winner = hybridPrediction.outcome_probabilities.home_win >= hybridPrediction.outcome_probabilities.away_win ? "home" : "away";

    const score_diff = Math.abs(
      (eloOddsPrediction.predicted_score.home - eloOddsPrediction.predicted_score.away) -
      (hybridPrediction.predicted_score.home - hybridPrediction.predicted_score.away)
    );

    if (elo_winner === hybrid_winner && score_diff < 0.5) return "high";
    if (elo_winner === hybrid_winner) return "medium";
    return "low";
  })();

  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b bg-secondary px-4 py-2">
        <div className="flex items-center gap-2 text-xs font-medium">
          <TrendingUp className="size-3.5" />
          <span>引擎对比</span>
        </div>
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {isExpanded ? "收起" : "展开"}
          {isExpanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
        </button>
      </div>

      {/* Match Info */}
      <div className="px-4 py-3 border-b">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">{match.home_team}</span>
          <span className="text-xs text-muted-foreground">vs</span>
          <span className="font-medium">{match.away_team}</span>
        </div>
      </div>

      {/* Comparison Grid */}
      {isExpanded && (
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <PredictionColumn
              label="Elo+赔率"
              icon={Zap}
              color="text-primary"
              prediction={eloOddsPrediction}
            />
            <PredictionColumn
              label="混合引擎"
              icon={Brain}
              color="text-muted-foreground"
              prediction={hybridPrediction}
            />
          </div>

          {/* Agreement Indicator */}
          {agreement && (
            <div className={cn(
              "flex items-center gap-2 rounded-md border px-3 py-2 text-xs",
              agreement === "high" && "border-pos/40 bg-pos/10 text-pos",
              agreement === "medium" && "border-warn/40 bg-warn/10 text-warn",
              agreement === "low" && "border-neg/40 bg-neg/10 text-neg"
            )}>
              <AlertCircle className="size-3.5" />
              <span>
                {agreement === "high" && "两种引擎高度一致"}
                {agreement === "medium" && "两种引擎基本一致"}
                {agreement === "low" && "两种引擎存在分歧"}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="px-4 py-6 text-center">
          <div className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <div className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            <span>加载对比数据...</span>
          </div>
        </div>
      )}
    </div>
  );
}
