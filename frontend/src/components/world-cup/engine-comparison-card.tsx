"use client";

import { useState } from "react";
import { Zap, Brain, AlertCircle, Check, type LucideIcon } from "lucide-react";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup-predictions";
import { triggerPrediction } from "@/lib/world-cup-predictions";
import { cn } from "@/lib/utils";

interface EngineComparisonCardProps {
  match: MatchFixture;
  eloOddsPrediction?: MatchPrediction;
  hybridPrediction?: MatchPrediction;
  isLoading?: boolean;
  onApplyPrediction?: () => void;
}

function probabilityBar(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

function PredictionColumn({
  label,
  icon: Icon,
  color,
  prediction,
  engine,
  matchId,
  onApply
}: {
  label: string;
  icon: LucideIcon;
  color: string;
  prediction?: MatchPrediction;
  engine: "elo_odds" | "hybrid";
  matchId: string;
  onApply?: () => void;
}) {
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  const handleApply = async () => {
    setApplying(true);
    setApplyError(null);
    try {
      await triggerPrediction(matchId, engine);
      setApplied(true);
      setTimeout(() => setApplied(false), 2000);

      // Notify parent to reload
      onApply?.();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setApplyError(`应用失败: ${message}`);
      console.error("[EngineCompare] Failed to apply prediction:", error);
    } finally {
      setApplying(false);
    }
  };

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
    <div className="flex-1 rounded-lg border bg-card p-4 flex flex-col">
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
          {Math.round(prediction.predicted_score.home)}
        </div>
        <div className="text-xs text-muted-foreground">vs</div>
        <div className={cn(
          "font-mono text-xl font-bold tabular-nums",
          highestOutcome === "away" ? "text-primary" : "text-muted-foreground"
        )}>
          {Math.round(prediction.predicted_score.away)}
        </div>
      </div>

      {/* Outcome Probabilities - Compact */}
      <div className="mt-3 space-y-1.5 flex-1">
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
      {prediction.elo_ratings ? (
        <div className="mt-3 rounded border bg-secondary/30 px-2 py-1.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Elo</span>
            <span className="font-mono font-medium tabular-nums">
              {Math.round(prediction.elo_ratings.home)} - {Math.round(prediction.elo_ratings.away)}
            </span>
          </div>
        </div>
      ) : (
        <div className="mt-3 h-[34px]" />
      )}

      {/* Apply Button */}
      {applyError && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-neg">
          <AlertCircle className="size-3 flex-shrink-0" />
          <span>{applyError}</span>
        </div>
      )}
      <button
        onClick={handleApply}
        disabled={applying || applied}
        className={cn(
          "mt-3 w-full rounded-md border px-3 py-2 text-xs font-medium transition-colors",
          applied
            ? "border-pos/40 bg-pos/10 text-pos cursor-default"
            : "bg-secondary/50 text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed"
        )}
      >
        {applied ? (
          <div className="flex items-center justify-center gap-1.5">
            <Check className="size-3.5" />
            <span>已应用</span>
          </div>
        ) : applying ? (
          <div className="flex items-center justify-center gap-1.5">
            <div className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            <span>应用中...</span>
          </div>
        ) : (
          <span>应用此预测</span>
        )}
      </button>
    </div>
  );
}

export function EngineComparisonCard({
  match,
  eloOddsPrediction,
  hybridPrediction,
  isLoading,
  onApplyPrediction
}: EngineComparisonCardProps) {
  // Calculate agreement level
  const agreement = (() => {
    if (!eloOddsPrediction || !hybridPrediction) return null;

    const getOutcome = (probs: { home_win: number; draw: number; away_win: number }) => {
      if (probs.home_win >= probs.draw && probs.home_win >= probs.away_win) return "home";
      if (probs.away_win >= probs.draw) return "away";
      return "draw";
    };

    const elo_outcome = getOutcome(eloOddsPrediction.outcome_probabilities);
    const hybrid_outcome = getOutcome(hybridPrediction.outcome_probabilities);

    // If outcomes differ, low agreement
    if (elo_outcome !== hybrid_outcome) return "low";

    // Same outcome - check probability similarity
    const prob_diff = Math.abs(
      eloOddsPrediction.outcome_probabilities.home_win - hybridPrediction.outcome_probabilities.home_win
    ) + Math.abs(
      eloOddsPrediction.outcome_probabilities.draw - hybridPrediction.outcome_probabilities.draw
    ) + Math.abs(
      eloOddsPrediction.outcome_probabilities.away_win - hybridPrediction.outcome_probabilities.away_win
    );

    // prob_diff: 0-3 (sum of absolute differences)
    // < 0.3: high agreement, 0.3-0.6: medium, > 0.6: low
    if (prob_diff < 0.3) return "high";
    if (prob_diff < 0.6) return "medium";
    return "low";
  })();

  if (isLoading) {
    return (
      <div className="px-4 py-12 text-center">
        <div className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <div className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
          <span>加载对比数据...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Match Info */}
      <div className="rounded-lg border bg-secondary/30 px-4 py-3">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">{match.home_team}</span>
          <span className="text-xs text-muted-foreground">vs</span>
          <span className="font-medium">{match.away_team}</span>
        </div>
      </div>

      {/* Comparison Grid */}
      <div className="grid grid-cols-2 gap-3">
        <PredictionColumn
          label="Elo+赔率"
          icon={Zap}
          color="text-primary"
          prediction={eloOddsPrediction}
          engine="elo_odds"
          matchId={match.match_id}
          onApply={onApplyPrediction}
        />
        <PredictionColumn
          label="混合引擎"
          icon={Brain}
          color="text-muted-foreground"
          prediction={hybridPrediction}
          engine="hybrid"
          matchId={match.match_id}
          onApply={onApplyPrediction}
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
  );
}
