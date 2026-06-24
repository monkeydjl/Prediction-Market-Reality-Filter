"use client";

import { useState, useEffect } from "react";
import { Brain, Loader2, AlertCircle, TrendingUp, Clock, Sparkles } from "lucide-react";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup-predictions";
import { analyzePrediction, postHeaders } from "@/lib/world-cup-predictions";
import { translateTeamName } from "@/lib/team-names-zh";
import { cn } from "@/lib/utils";
import { getWorldCupApiBase } from "@/lib/env";

interface PredictionAnalysisCardProps {
  match: MatchFixture;
  prediction: MatchPrediction;
}

interface AnalysisHistoryEntry {
  id: number;
  analysis: string;
  predicted_score: { home: number; away: number };
  confidence: number;
  prediction_method?: string;
  created_at: string;
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

export function PredictionAnalysisCard({ match, prediction }: PredictionAnalysisCardProps) {
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [history, setHistory] = useState<AnalysisHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cached, setCached] = useState(false);

  // Load analysis history on mount
  useEffect(() => {
    async function loadHistory() {
      try {
        setLoadingHistory(true);
        const response = await fetch(
          `${getWorldCupApiBase()}/api/world-cup/predictions/matches/${match.match_id}/analysis-history`,
          { cache: "no-store" }
        );

        if (response.ok) {
          const data = await response.json();
          setHistory(data.history || []);

          // If there's a recent analysis, show it by default
          if (data.history && data.history.length > 0) {
            setAnalysis(data.history[0].analysis);
            setCached(true);
          }
        }
      } catch (err) {
        console.error("Failed to load analysis history:", err);
      } finally {
        setLoadingHistory(false);
      }
    }

    loadHistory();
  }, [match.match_id]);

  const handleAnalyze = async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await analyzePrediction(match.match_id);
      setAnalysis(result);
      setCached(false);

      // Reload history to include the new analysis
      const response = await fetch(
        `${getWorldCupApiBase()}/api/world-cup/predictions/matches/${match.match_id}/analysis-history`,
        { cache: "no-store" }
      );

      if (response.ok) {
        const data = await response.json();
        setHistory(data.history || []);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "分析失败";

      // Check for quota errors
      if (errorMessage.includes("403") || errorMessage.includes("quota") || errorMessage.includes("FreeTierOnly")) {
        setError("AI 分析服务配额已用尽，请联系管理员升级 API 套餐");
      } else {
        setError(errorMessage);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleOptimize = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        `${getWorldCupApiBase()}/api/world-cup/predictions/matches/${match.match_id}/optimize`,
        {
          method: "POST",
          headers: postHeaders(),
          cache: "no-store"
        }
      );

      if (!response.ok) {
        const data = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(data.detail || `HTTP ${response.status}`);
      }

      const data = await response.json();
      if (data.status === "ok") {
        const opt = data.optimization;

        // Format optimization result as analysis text
        let optimizationText = "# AI 优化建议\n\n";

        if (opt.blind_spots && opt.blind_spots.length > 0) {
          optimizationText += "## 数据盲点识别\n";
          opt.blind_spots.forEach((spot: string) => {
            optimizationText += `- ${spot}\n`;
          });
          optimizationText += "\n";
        }

        if (opt.calibration_issues && opt.calibration_issues.length > 0) {
          optimizationText += "## 概率校准建议\n";
          opt.calibration_issues.forEach((issue: string) => {
            optimizationText += `- ${issue}\n`;
          });
          optimizationText += "\n";
        }

        if (opt.optimized_prediction) {
          const op = opt.optimized_prediction;
          const orig = data.original_prediction;

          optimizationText += "## 优化后预测\n\n";
          optimizationText += "**原始预测 → 优化预测**\n";
          optimizationText += `- 比分: ${orig.predicted_score.home.toFixed(1)}-${orig.predicted_score.away.toFixed(1)} → ${op.predicted_score.home.toFixed(1)}-${op.predicted_score.away.toFixed(1)}\n`;
          optimizationText += `- 主胜率: ${(orig.outcome_probabilities.home_win * 100).toFixed(1)}% → ${(op.outcome_probabilities.home_win * 100).toFixed(1)}%\n`;
          optimizationText += `- 平局率: ${(orig.outcome_probabilities.draw * 100).toFixed(1)}% → ${(op.outcome_probabilities.draw * 100).toFixed(1)}%\n`;
          optimizationText += `- 客胜率: ${(orig.outcome_probabilities.away_win * 100).toFixed(1)}% → ${(op.outcome_probabilities.away_win * 100).toFixed(1)}%\n`;
          optimizationText += `- 置信度: ${(orig.confidence * 100).toFixed(1)}% → ${(op.confidence * 100).toFixed(1)}%\n`;

          if (op.reasoning) {
            optimizationText += `\n**调整理由**\n${op.reasoning}\n`;
          }
        } else if (opt.raw_text) {
          optimizationText += opt.raw_text;
        }

        setAnalysis(optimizationText);
        setCached(false);
      } else {
        throw new Error(data.message || "优化失败");
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "优化失败";

      if (errorMessage.includes("403") || errorMessage.includes("quota")) {
        setError("AI 优化配额已用完，请稍后再试");
      } else if (errorMessage.includes("503") || errorMessage.includes("unavailable") || errorMessage.includes("OPENAI_API_KEY")) {
        setError("AI 优化功能需要配置 OpenAI API Key");
      } else {
        setError(`AI优化失败: ${errorMessage}`);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Current Analysis */}
      <div className="rounded-lg border bg-card overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b bg-secondary px-4 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="size-4 text-primary" />
            <span className="text-sm font-medium">当前分析</span>
            {cached && (
              <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                缓存
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleAnalyze}
              disabled={loading}
              className="rounded-md border bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-50"
            >
              {loading ? "处理中..." : "AI分析"}
            </button>
            <button
              onClick={handleOptimize}
              disabled={loading}
              className="rounded-md border bg-secondary px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary/80 disabled:opacity-50"
            >
              AI优化
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="p-4">
          {/* Prediction Summary */}
          <div className="mb-4 rounded-md border bg-secondary/30 p-3">
            <div className="grid grid-cols-3 items-center gap-2 text-center">
              <div>
                <div className="text-xs text-muted-foreground">
                  {translateTeamName(match.home_team)}
                </div>
                <div className="mt-1 font-mono text-xl font-bold tabular-nums">
                  {Math.round(prediction.predicted_score.home)}
                </div>
              </div>
              <div className="text-xs text-muted-foreground">预测</div>
              <div>
                <div className="text-xs text-muted-foreground">
                  {translateTeamName(match.away_team)}
                </div>
                <div className="mt-1 font-mono text-xl font-bold tabular-nums">
                  {Math.round(prediction.predicted_score.away)}
                </div>
              </div>
            </div>

            <div className="mt-3 flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <TrendingUp className="size-3" />
                <span>置信度</span>
              </div>
              <span className="font-mono font-medium tabular-nums">
                {Math.round(prediction.confidence * 100)}%
              </span>
            </div>
          </div>

          {/* Loading State */}
          {loading && (
            <div className="py-8 text-center">
              <div className="inline-flex flex-col items-center gap-3">
                <Loader2 className="size-8 animate-spin text-primary" />
                <div className="text-sm text-muted-foreground">
                  AI正在分析预测结果...
                </div>
              </div>
            </div>
          )}

          {/* Error State */}
          {error && !loading && (
            <div className="rounded-md border border-neg/40 bg-neg/10 p-3">
              <div className="flex items-start gap-2 text-sm text-neg">
                <AlertCircle className="size-4 flex-shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            </div>
          )}

          {/* Analysis Result */}
          {analysis && !loading && !error && (
            <div className="prose prose-sm max-w-none">
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                {analysis}
              </div>
            </div>
          )}

          {/* No Analysis Yet */}
          {!analysis && !loading && !error && !loadingHistory && (
            <div className="py-8 text-center">
              <Brain className="mx-auto size-12 text-muted-foreground opacity-50" />
              <p className="mt-3 text-sm text-muted-foreground">
                点击"AI分析"查看预测评估，或点击"AI优化"获取改进建议
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Analysis History */}
      {history.length > 0 && (
        <div className="rounded-lg border bg-card overflow-hidden">
          <div className="border-b bg-secondary px-4 py-3">
            <div className="flex items-center gap-2">
              <Clock className="size-4 text-muted-foreground" />
              <span className="text-sm font-medium">历史分析</span>
              <span className="text-xs text-muted-foreground">({history.length})</span>
            </div>
          </div>

          <div className={cn(
            "divide-y",
            history.length > 3 && "max-h-[400px] overflow-y-auto"
          )}>
            {history.map((entry, idx) => (
              <button
                key={entry.id}
                onClick={() => {
                  setAnalysis(entry.analysis);
                  setCached(true);
                }}
                className="w-full px-4 py-3 text-left transition-colors hover:bg-secondary/50"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">
                        {formatTimestamp(entry.created_at)}
                      </span>
                      {idx === 0 && (
                        <span className="rounded bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground">
                          最新
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground truncate">
                      {entry.analysis.substring(0, 60)}...
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1 text-xs">
                    <span className="font-mono tabular-nums">
                      {Math.round(entry.predicted_score.home)}-{Math.round(entry.predicted_score.away)}
                    </span>
                    <span className="text-muted-foreground">
                      {Math.round(entry.confidence * 100)}%
                    </span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
