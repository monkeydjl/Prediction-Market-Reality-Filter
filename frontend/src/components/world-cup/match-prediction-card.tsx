"use client";

import { useMemo } from "react";
import { Trophy, TrendingUp, Clock, AlertCircle } from "lucide-react";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup-predictions";
import { cn } from "@/lib/utils";

interface MatchPredictionCardProps {
  match: MatchFixture;
  prediction?: MatchPrediction;
}

function formatKickoff(kickoffUtc: string): string {
  const date = new Date(kickoffUtc);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    GROUP_STAGE: "小组赛",
    ROUND_OF_16: "1/8决赛",
    QUARTER_FINAL: "1/4决赛",
    SEMI_FINAL: "半决赛",
    THIRD_PLACE: "季军赛",
    FINAL: "决赛",
  };
  return labels[stage] || stage;
}

function confidenceTone(confidence: number): string {
  if (confidence >= 0.8) return "text-pos";
  if (confidence >= 0.6) return "text-warn";
  return "text-neg";
}

function probabilityBar(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

export function MatchPredictionCard({ match, prediction }: MatchPredictionCardProps) {
  const isPredicted = prediction != null;
  const isFinished = match.status === "finished";
  const isLive = match.status === "in_play";

  const highestOutcome = useMemo(() => {
    if (!prediction) return null;
    const probs = prediction.outcome_probabilities;
    if (probs.home_win >= probs.draw && probs.home_win >= probs.away_win) return "home";
    if (probs.away_win >= probs.draw) return "away";
    return "draw";
  }, [prediction]);

  return (
    <div className={cn(
      "rounded-lg border bg-card overflow-hidden transition-colors",
      isLive && "border-warn/50 bg-warn/5",
      isFinished && "opacity-60"
    )}>
      {/* Header */}
      <div className="flex items-center justify-between border-b bg-secondary px-4 py-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Trophy className="size-3.5" />
          <span>{stageLabel(match.stage)}</span>
          {match.group && <span className="opacity-60">· {match.group}组</span>}
        </div>
        <div className="flex items-center gap-2 text-xs">
          {isLive && (
            <span className="flex items-center gap-1 rounded-md bg-warn px-2 py-0.5 font-medium text-warn-foreground">
              <span className="size-1.5 animate-pulse rounded-full bg-warn-foreground" />
              进行中
            </span>
          )}
          <span className="text-muted-foreground">
            <Clock className="inline size-3 mr-1" />
            {formatKickoff(match.kickoff_utc)}
          </span>
        </div>
      </div>

      {/* Match Teams and Score */}
      <div className="p-4">
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4">
          {/* Home Team */}
          <div className={cn(
            "text-right",
            highestOutcome === "home" && "font-semibold"
          )}>
            <div className="text-base">{match.home_team}</div>
            {isPredicted && (
              <div className={cn(
                "mt-1 font-mono text-2xl font-bold tabular-nums",
                highestOutcome === "home" ? "text-primary" : "text-muted-foreground"
              )}>
                {prediction.predicted_score.home.toFixed(1)}
              </div>
            )}
          </div>

          {/* VS / Score Separator */}
          <div className="flex flex-col items-center gap-1">
            {isPredicted ? (
              <div className="text-sm font-medium text-muted-foreground">vs</div>
            ) : (
              <div className="text-xs text-muted-foreground">待预测</div>
            )}
          </div>

          {/* Away Team */}
          <div className={cn(
            "text-left",
            highestOutcome === "away" && "font-semibold"
          )}>
            <div className="text-base">{match.away_team}</div>
            {isPredicted && (
              <div className={cn(
                "mt-1 font-mono text-2xl font-bold tabular-nums",
                highestOutcome === "away" ? "text-primary" : "text-muted-foreground"
              )}>
                {prediction.predicted_score.away.toFixed(1)}
              </div>
            )}
          </div>
        </div>

        {/* Outcome Probabilities */}
        {isPredicted && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">主胜</span>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-24 overflow-hidden rounded-full bg-secondary">
                  <div
                    className="h-full bg-primary transition-all"
                    style={{ width: probabilityBar(prediction.outcome_probabilities.home_win) }}
                  />
                </div>
                <span className="w-10 text-right font-mono text-xs font-medium tabular-nums">
                  {probabilityBar(prediction.outcome_probabilities.home_win)}
                </span>
              </div>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">平局</span>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-24 overflow-hidden rounded-full bg-secondary">
                  <div
                    className="h-full bg-muted-foreground transition-all"
                    style={{ width: probabilityBar(prediction.outcome_probabilities.draw) }}
                  />
                </div>
                <span className="w-10 text-right font-mono text-xs font-medium tabular-nums">
                  {probabilityBar(prediction.outcome_probabilities.draw)}
                </span>
              </div>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">客胜</span>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-24 overflow-hidden rounded-full bg-secondary">
                  <div
                    className="h-full bg-primary transition-all"
                    style={{ width: probabilityBar(prediction.outcome_probabilities.away_win) }}
                  />
                </div>
                <span className="w-10 text-right font-mono text-xs font-medium tabular-nums">
                  {probabilityBar(prediction.outcome_probabilities.away_win)}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Confidence */}
        {isPredicted && (
          <div className="mt-3 flex items-center justify-between rounded-md border bg-secondary/50 px-3 py-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <TrendingUp className="size-3.5" />
              <span>预测置信度</span>
            </div>
            <span className={cn(
              "font-mono text-sm font-semibold tabular-nums",
              confidenceTone(prediction.confidence)
            )}>
              {probabilityBar(prediction.confidence)}
            </span>
          </div>
        )}

        {/* No Prediction Warning */}
        {!isPredicted && !isFinished && (
          <div className="mt-4 flex items-center gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            <AlertCircle className="size-3.5" />
            <span>暂无预测数据</span>
          </div>
        )}
      </div>

      {/* Venue */}
      <div className="border-t bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
        <span className="opacity-70">场地:</span> {match.venue}
      </div>
    </div>
  );
}
