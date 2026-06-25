"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Trophy, TrendingUp, Clock, AlertCircle, Zap, Brain, GitCompare, History, Sparkles, Loader2, ChevronDown, Lightbulb, Gauge, type LucideIcon } from "lucide-react";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup-predictions";
import { compareEngines } from "@/lib/world-cup-predictions";
import { translateTeamName } from "@/lib/team-names-zh";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface MatchPredictionCardProps {
  match: MatchFixture;
  prediction?: MatchPrediction;
  onTeamClick?: (teamName: string) => void;
  onPredictionUpdated?: () => void;
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

function qualityScoreTone(score: number): string {
  if (score >= 70) return "text-pos";
  if (score >= 45) return "text-warn";
  return "text-neg";
}

function qualityScoreLabel(score: number): string {
  if (score >= 70) return "数据充分";
  if (score >= 45) return "数据一般";
  return "数据有限";
}

function probabilityBar(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

function getEngineLabel(prediction?: MatchPrediction): { icon: LucideIcon; label: string; color: string } | null {
  if (!prediction) return null;

  const method = prediction.prediction_method || "";
  const engine = prediction.engine_used;
  const hasOdds = prediction.has_betting_odds;

  if (method === "elo_only") {
    return { icon: TrendingUp, label: "Elo评级", color: "text-muted-foreground" };
  }

  if (engine === "elo_odds" || method.includes("elo_odds") || (hasOdds && method.includes("elo"))) {
    return { icon: Zap, label: "Elo+赔率", color: "text-primary" };
  }

  return { icon: Brain, label: "混合引擎", color: "text-muted-foreground" };
}

function DialogLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center rounded-lg border bg-card p-6 text-sm text-muted-foreground">
      <Loader2 className="mr-2 size-4 animate-spin" />
      {label}
    </div>
  );
}

const EngineComparisonCard = dynamic(
  () => import("./engine-comparison-card").then((mod) => mod.EngineComparisonCard),
  { loading: () => <DialogLoading label="加载引擎对比..." /> }
);
const PredictionHistoryCard = dynamic(
  () => import("./prediction-history-card").then((mod) => mod.PredictionHistoryCard),
  { loading: () => <DialogLoading label="加载预测历史..." /> }
);
const PredictionAnalysisCard = dynamic(
  () => import("./prediction-analysis-card").then((mod) => mod.PredictionAnalysisCard),
  { loading: () => <DialogLoading label="加载 AI 分析..." /> }
);

export function MatchPredictionCard({ match, prediction, onTeamClick, onPredictionUpdated }: MatchPredictionCardProps) {
  const [showComparison, setShowComparison] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showAnalysis, setShowAnalysis] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);
  const [isLoadingComparison, setIsLoadingComparison] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [comparisonData, setComparisonData] = useState<{
    elo_odds?: MatchPrediction;
    hybrid?: MatchPrediction;
  }>({});

  const hasComparisonData = comparisonData.elo_odds != null || comparisonData.hybrid != null;

  const handleCompare = useCallback(async () => {
    if (showComparison) {
      setShowComparison(false);
      return;
    }

    setShowComparison(true);
    if (hasComparisonData || isLoadingComparison) return;

    setIsLoadingComparison(true);
    setComparisonError(null);

    try {
      const data = await compareEngines(match.match_id);
      setComparisonData(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setComparisonError(`加载对比数据失败: ${message}`);
      console.error("Failed to load comparison:", error);
    } finally {
      setIsLoadingComparison(false);
    }
  }, [hasComparisonData, isLoadingComparison, match.match_id, showComparison]);

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

  // Explanation data: top key factors, AI reasoning, and data quality score.
  const keyFactors = useMemo(
    () => (prediction?.key_factors ?? []).filter((f) => f && f.trim()).slice(0, 3),
    [prediction]
  );
  const reasoning = prediction?.ai_reasoning?.trim() || null;
  const qualityScore = prediction?.data_quality_score ?? null;
  const hasExplanation = keyFactors.length > 0 || reasoning != null || qualityScore != null;

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
            <button
              onClick={() => onTeamClick?.(match.home_team)}
              className="text-base hover:text-primary hover:underline transition-colors"
            >
              {translateTeamName(match.home_team)}
            </button>
            {isFinished && match.home_score != null ? (
              <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-foreground">
                {match.home_score}
              </div>
            ) : isPredicted ? (
              <div className={cn(
                "mt-1 font-mono text-2xl font-bold tabular-nums",
                highestOutcome === "home" ? "text-primary" : "text-muted-foreground"
              )}>
                {Math.round(prediction.predicted_score.home)}
              </div>
            ) : null}
          </div>

          {/* VS / Score Separator */}
          <div className="flex flex-col items-center gap-1">
            {isFinished && match.home_score != null ? (
              <div className="text-sm font-medium text-foreground">-</div>
            ) : isPredicted ? (
              <div className="flex flex-col items-center gap-1">
                <div className="text-xs text-primary/70">预测</div>
                <div className="text-sm font-medium text-muted-foreground">vs</div>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">待预测</div>
            )}
          </div>

          {/* Away Team */}
          <div className={cn(
            "text-left",
            highestOutcome === "away" && "font-semibold"
          )}>
            <button
              onClick={() => onTeamClick?.(match.away_team)}
              className="text-base hover:text-primary hover:underline transition-colors"
            >
              {translateTeamName(match.away_team)}
            </button>
            {isFinished && match.away_score != null ? (
              <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-foreground">
                {match.away_score}
              </div>
            ) : isPredicted ? (
              <div className={cn(
                "mt-1 font-mono text-2xl font-bold tabular-nums",
                highestOutcome === "away" ? "text-primary" : "text-muted-foreground"
              )}>
                {Math.round(prediction.predicted_score.away)}
              </div>
            ) : null}
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
          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between rounded-md border bg-secondary/50 px-3 py-2">
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

            {/* Engine Badge */}
            {isPredicted && prediction && (() => {
              const engineInfo = getEngineLabel(prediction);
              if (!engineInfo) return null;
              const Icon = engineInfo.icon;
              return (
                <div className="flex items-center justify-between rounded-md border bg-secondary/30 px-3 py-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Icon className="size-3.5" />
                    <span>预测引擎</span>
                  </div>
                  <span className={cn("text-xs font-medium", engineInfo.color)}>
                    {engineInfo.label}
                  </span>
                </div>
              );
            })()}

            {/* Elo Ratings Display */}
            {prediction.elo_ratings && (
              <div className="rounded-md border bg-secondary/30 px-3 py-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Elo评级</span>
                  <div className="flex items-center gap-3 font-mono text-xs font-medium tabular-nums">
                    <span>{Math.round(prediction.elo_ratings.home)}</span>
                    <span className="text-muted-foreground">vs</span>
                    <span>{Math.round(prediction.elo_ratings.away)}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Data Quality Badge */}
            {prediction.data_quality && prediction.data_quality !== "real" && (
              <div className={cn(
                "flex items-center gap-2 rounded-md border px-3 py-2 text-xs",
                prediction.data_quality === "mock"
                  ? "border-neg/40 bg-neg/10 text-neg"
                  : "border-warn/40 bg-warn/10 text-warn"
              )}>
                <AlertCircle className="size-3.5 shrink-0" />
                <span>
                  {prediction.data_quality === "mock"
                    ? "数据来源：模拟数据（API 不可用，预测可能不准确）"
                    : "数据来源：部分模拟（部分球队数据缺失）"}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Why this prediction — collapsible explanation */}
        {isPredicted && hasExplanation && (
          <div className="mt-3">
            <button
              onClick={() => setShowReasoning((v) => !v)}
              className="flex w-full items-center justify-between rounded-md border bg-secondary/30 px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              aria-expanded={showReasoning}
            >
              <div className="flex items-center gap-2">
                <Lightbulb className="size-3.5" />
                <span>为何这样预测？</span>
              </div>
              <ChevronDown className={cn("size-3.5 transition-transform", showReasoning && "rotate-180")} />
            </button>

            {showReasoning && (
              <div className="mt-2 space-y-3 rounded-md border bg-secondary/20 px-3 py-3">
                {/* Data quality score */}
                {qualityScore != null && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2 text-muted-foreground">
                        <Gauge className="size-3.5" />
                        <span>数据质量</span>
                      </div>
                      <span className={cn("font-mono text-xs font-semibold tabular-nums", qualityScoreTone(qualityScore))}>
                        {Math.round(qualityScore)}/100 · {qualityScoreLabel(qualityScore)}
                      </span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                      <div
                        className={cn(
                          "h-full transition-all",
                          qualityScore >= 70 ? "bg-pos" : qualityScore >= 45 ? "bg-warn" : "bg-neg"
                        )}
                        style={{ width: `${Math.max(0, Math.min(100, qualityScore))}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Key factors */}
                {keyFactors.length > 0 && (
                  <div className="space-y-1">
                    <div className="text-xs font-medium text-foreground">关键因素</div>
                    <ul className="space-y-1">
                      {keyFactors.map((factor, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                          <span className="mt-1 size-1 shrink-0 rounded-full bg-primary" />
                          <span>{factor}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* AI reasoning summary */}
                {reasoning && (
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 text-xs font-medium text-foreground">
                      <Brain className="size-3.5" />
                      <span>AI 解读</span>
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {reasoning.length > 160 ? `${reasoning.slice(0, 160)}…` : reasoning}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* No Prediction Warning */}
        {!isPredicted && !isFinished && (
          <div className="mt-4 flex items-center gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            <AlertCircle className="size-3.5" />
            <span>暂无预测数据</span>
          </div>
        )}

        {/* Action Buttons */}
        {isPredicted && (
          <div className="mt-4 grid grid-cols-3 gap-2">
            <button
              onClick={handleCompare}
              disabled={isLoadingComparison}
              className="rounded-md border bg-secondary/50 px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-wait disabled:opacity-70"
            >
              <div className="flex items-center justify-center gap-2">
                {isLoadingComparison ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <GitCompare className="size-3.5" />
                )}
                <span>{isLoadingComparison ? "加载中" : "引擎对比"}</span>
              </div>
            </button>
            <button
              onClick={() => setShowHistory(true)}
              className="rounded-md border bg-secondary/50 px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <div className="flex items-center justify-center gap-2">
                <History className="size-3.5" />
                <span>预测历史</span>
              </div>
            </button>
            <button
              onClick={() => setShowAnalysis(true)}
              className="rounded-md border bg-secondary/50 px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <div className="flex items-center justify-center gap-2">
                <Sparkles className="size-3.5" />
                <span>AI分析</span>
              </div>
            </button>
          </div>
        )}
      </div>

      {/* Engine Comparison Dialog */}
      <Dialog open={showComparison} onOpenChange={setShowComparison}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>引擎对比分析</DialogTitle>
          </DialogHeader>
          <EngineComparisonCard
            match={match}
            eloOddsPrediction={comparisonData.elo_odds}
            hybridPrediction={comparisonData.hybrid}
            isLoading={isLoadingComparison}
            onApplyPrediction={() => {
              onPredictionUpdated?.();
              setShowComparison(false);
            }}
          />
          {comparisonError && (
            <div className="mt-4 flex items-center gap-2 rounded-lg border border-neg/40 bg-neg/10 p-3 text-sm text-neg">
              <AlertCircle className="size-4 flex-shrink-0" />
              <span>{comparisonError}</span>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Prediction History Dialog */}
      <Dialog open={showHistory} onOpenChange={setShowHistory}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>预测历史记录</DialogTitle>
          </DialogHeader>
          <PredictionHistoryCard match={match} />
        </DialogContent>
      </Dialog>

      {/* AI Analysis Dialog */}
      <Dialog open={showAnalysis} onOpenChange={setShowAnalysis}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>AI 预测分析</DialogTitle>
          </DialogHeader>
          {prediction && <PredictionAnalysisCard match={match} prediction={prediction} />}
        </DialogContent>
      </Dialog>

      {/* Venue */}
      <div className="border-t bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
        <span className="opacity-70">场地:</span> {match.venue}
      </div>
    </div>
  );
}
