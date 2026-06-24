"use client";

import { useState, useEffect } from "react";
import { Clock, TrendingUp, Target, AlertCircle, Zap, Brain } from "lucide-react";
import type { MatchFixture, PredictionHistoryEntry } from "@/lib/world-cup-predictions";
import { fetchPredictionHistory } from "@/lib/world-cup-predictions";
import { translateTeamName } from "@/lib/team-names-zh";
import { cn } from "@/lib/utils";

interface PredictionHistoryCardProps {
  match: MatchFixture;
}

function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getEngineLabel(method?: string): { label: string; icon: typeof Zap; color: string } {
  if (!method) return { label: "未知", icon: Brain, color: "text-muted-foreground" };

  if (method.includes("elo_odds") || method.includes("elo") && method.includes("odds")) {
    return { label: "Elo+赔率", icon: Zap, color: "text-primary" };
  }

  return { label: "混合引擎", icon: Brain, color: "text-muted-foreground" };
}

function calculateScoreDiff(
  predicted: { home: number; away: number },
  actual: { home: number; away: number }
): number {
  return Math.abs(predicted.home - actual.home) + Math.abs(predicted.away - actual.away);
}

export function PredictionHistoryCard({ match }: PredictionHistoryCardProps) {
  const [history, setHistory] = useState<PredictionHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadHistory() {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchPredictionHistory(match.match_id);
        setHistory(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      } finally {
        setLoading(false);
      }
    }

    loadHistory();
  }, [match.match_id]);

  const isFinished = match.status === "finished" && match.home_score != null && match.away_score != null;
  const hasChanges = history.length > 1;

  // Group consecutive identical predictions (by score AND engine)
  const dedupedHistory = history.reduce((acc, curr, idx) => {
    if (idx === 0) return [curr];

    const prev = acc[acc.length - 1]; // Compare with last kept entry, not previous in original array
    const scoreChanged =
      curr.predicted_score.home !== prev.predicted_score.home ||
      curr.predicted_score.away !== prev.predicted_score.away;
    const engineChanged = curr.prediction_method !== prev.prediction_method;

    // Keep if score OR engine changed
    if (scoreChanged || engineChanged) {
      acc.push(curr);
    }

    return acc;
  }, [] as PredictionHistoryEntry[]);

  if (loading) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center">
        <div className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <div className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
          <span>加载历史记录...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-neg/40 bg-neg/10 p-4">
        <div className="flex items-center gap-2 text-sm text-neg">
          <AlertCircle className="size-4" />
          <span>{error}</span>
        </div>
      </div>
    );
  }

  if (history.length === 0) {
    return (
      <div className="rounded-lg border border-dashed bg-secondary/30 p-6 text-center">
        <Clock className="mx-auto size-8 text-muted-foreground opacity-50" />
        <p className="mt-2 text-sm text-muted-foreground">暂无预测历史</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b bg-secondary px-4 py-3">
        <div className="flex items-center gap-2">
          <Clock className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">预测历史</span>
          <span className="text-xs text-muted-foreground">
            ({dedupedHistory.length} 次预测{hasChanges ? "变化" : ""})
          </span>
        </div>
        {isFinished && (
          <div className="flex items-center gap-2 text-xs">
            <Target className="size-3.5 text-primary" />
            <span className="text-muted-foreground">实际比分</span>
            <span className="font-mono font-bold tabular-nums">
              {match.home_score} - {match.away_score}
            </span>
          </div>
        )}
      </div>

      {/* Timeline */}
      <div className={cn(
        "divide-y",
        dedupedHistory.length > 5 && "max-h-[400px] overflow-y-auto"
      )}>
        {dedupedHistory.map((entry, idx) => {
          const isLatest = idx === dedupedHistory.length - 1;
          const predictedHome = Math.round(entry.predicted_score.home);
          const predictedAway = Math.round(entry.predicted_score.away);

          let accuracy: "exact" | "close" | "wrong" | null = null;
          let scoreDiff: number | null = null;

          if (isFinished) {
            scoreDiff = calculateScoreDiff(
              { home: predictedHome, away: predictedAway },
              { home: match.home_score!, away: match.away_score! }
            );

            if (scoreDiff === 0) {
              accuracy = "exact";
            } else if (scoreDiff <= 2) {
              accuracy = "close";
            } else {
              accuracy = "wrong";
            }
          }

          return (
            <div
              key={idx}
              className={cn(
                "px-4 py-3 transition-colors",
                isLatest && "bg-primary/5"
              )}
            >
              <div className="flex items-start justify-between gap-4">
                {/* Timestamp & Badge */}
                <div className="flex items-center gap-3">
                  <div className="flex flex-col items-end gap-1">
                    <span className="text-xs text-muted-foreground">
                      {formatTimestamp(entry.timestamp)}
                    </span>
                    {isLatest && (
                      <span className="rounded bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground">
                        最新
                      </span>
                    )}
                  </div>

                  {/* Score Prediction */}
                  <div className="flex items-center gap-2">
                    <div className="text-right">
                      <div className="text-xs text-muted-foreground">
                        {translateTeamName(match.home_team)}
                      </div>
                      <div className="mt-0.5 font-mono text-lg font-bold tabular-nums">
                        {predictedHome}
                      </div>
                    </div>
                    <div className="text-xs text-muted-foreground">-</div>
                    <div className="text-left">
                      <div className="text-xs text-muted-foreground">
                        {translateTeamName(match.away_team)}
                      </div>
                      <div className="mt-0.5 font-mono text-lg font-bold tabular-nums">
                        {predictedAway}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Right side: Engine + Accuracy/Confidence */}
                <div className="flex flex-col items-end gap-2">
                  {/* Engine Badge */}
                  {(() => {
                    const engineInfo = getEngineLabel(entry.prediction_method);
                    const Icon = engineInfo.icon;
                    return (
                      <div className="flex items-center gap-1 text-xs">
                        <Icon className={cn("size-3", engineInfo.color)} />
                        <span className={cn("font-medium", engineInfo.color)}>
                          {engineInfo.label}
                        </span>
                      </div>
                    );
                  })()}

                  {/* Accuracy Badge (if finished) */}
                  {accuracy && (
                    <div className="flex flex-col items-end gap-1">
                      <div
                        className={cn(
                          "rounded-md px-2 py-1 text-xs font-medium",
                          accuracy === "exact" && "bg-pos/20 text-pos",
                          accuracy === "close" && "bg-warn/20 text-warn",
                          accuracy === "wrong" && "bg-neg/20 text-neg"
                        )}
                      >
                        {accuracy === "exact" && "完全正确"}
                        {accuracy === "close" && "接近"}
                        {accuracy === "wrong" && "偏差较大"}
                      </div>
                      <span className="text-[10px] text-muted-foreground">
                        误差 {scoreDiff}
                      </span>
                    </div>
                  )}

                  {/* Confidence (if not finished) */}
                  {!isFinished && (
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <TrendingUp className="size-3" />
                      <span className="font-mono tabular-nums">
                        {Math.round(entry.confidence * 100)}%
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* Change Indicator */}
              {idx > 0 && (
                <div className="mt-2 text-xs text-muted-foreground">
                  <span className="opacity-60">预测调整：</span>
                  <span className="ml-1 font-mono">
                    {Math.round(dedupedHistory[idx - 1].predicted_score.home)}-
                    {Math.round(dedupedHistory[idx - 1].predicted_score.away)}
                  </span>
                  <span className="mx-1">→</span>
                  <span className="font-mono font-medium">
                    {predictedHome}-{predictedAway}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Summary Footer (if finished) */}
      {isFinished && dedupedHistory.length > 0 && (
        <div className="border-t bg-secondary/50 px-4 py-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              共 {dedupedHistory.length} 次预测
              {hasChanges && `，${dedupedHistory.length - 1} 次调整`}
            </span>
            {(() => {
              const latestPrediction = dedupedHistory[dedupedHistory.length - 1];
              const finalDiff = calculateScoreDiff(
                {
                  home: Math.round(latestPrediction.predicted_score.home),
                  away: Math.round(latestPrediction.predicted_score.away),
                },
                { home: match.home_score!, away: match.away_score! }
              );

              return (
                <span className="font-medium">
                  最终预测误差：
                  <span
                    className={cn(
                      "ml-1 font-mono tabular-nums",
                      finalDiff === 0 && "text-pos",
                      finalDiff <= 2 && finalDiff > 0 && "text-warn",
                      finalDiff > 2 && "text-neg"
                    )}
                  >
                    {finalDiff}
                  </span>
                </span>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
}
