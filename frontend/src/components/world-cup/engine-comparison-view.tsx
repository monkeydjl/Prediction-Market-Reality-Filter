"use client";

import { useEffect, useState } from "react";
import { Zap, Brain, Target, TrendingUp, Award, AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getWorldCupApiBase } from "@/lib/env";

interface EngineStat {
  total_matches: number;
  exact_score_rate: number;
  outcome_accuracy: number;
  goal_diff_accuracy: number;
  avg_score_error: number;
  predictions: Array<{
    match_id: string;
    home_team: string;
    away_team: string;
    predicted_score: { home: number; away: number };
    actual_score: { home: number; away: number };
    score_error: number;
    outcome_correct: boolean;
    confidence: number;
    outcome_probability: number;
  }>;
}

interface EngineComparisonData {
  elo_odds?: EngineStat;
  hybrid?: EngineStat;
}

function percentage(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function StatCard({
  label,
  value,
  subtitle,
  color = "text-foreground"
}: {
  label: string;
  value: string;
  subtitle?: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-2xl font-bold tabular-nums", color)}>{value}</div>
      {subtitle && <div className="mt-1 text-xs text-muted-foreground">{subtitle}</div>}
    </div>
  );
}

export function EngineComparisonView() {
  const [data, setData] = useState<EngineComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadComparison() {
      try {
        setLoading(true);
        setError(null);

        const response = await fetch(
          `${getWorldCupApiBase()}/api/world-cup/predictions/engine-comparison`,
          { cache: "no-store" }
        );

        if (!response.ok) {
          throw new Error("Failed to load comparison data");
        }

        const result = await response.json();

        if (result.status === "ok") {
          setData(result.engines);
        } else {
          setError(result.message || "No data available");
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      } finally {
        setLoading(false);
      }
    }

    loadComparison();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="inline-flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
          <span>加载引擎对比数据...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-warn/40 bg-warn/10 p-6">
        <div className="flex items-center gap-2 text-warn">
          <AlertCircle className="size-5" />
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!data || (!data.elo_odds && !data.hybrid)) {
    return (
      <div className="rounded-lg border border-dashed p-12 text-center">
        <Target className="mx-auto size-12 text-muted-foreground opacity-50" />
        <p className="mt-4 text-muted-foreground">暂无已完赛的比赛数据</p>
        <p className="mt-2 text-sm text-muted-foreground">比赛结束后将自动统计引擎准确率</p>
      </div>
    );
  }

  const eloStats = data.elo_odds;
  const hybridStats = data.hybrid;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-lg border bg-secondary/30 p-4">
        <h2 className="text-lg font-semibold">引擎准确率对比</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          基于已完赛比赛的预测准确度统计
        </p>
      </div>

      {/* Comparison Grid */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* Elo+Odds Engine */}
        {eloStats && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 rounded-lg border bg-primary/5 px-4 py-3">
              <Zap className="size-5 text-primary" />
              <div>
                <h3 className="font-semibold text-primary">Elo+赔率引擎</h3>
                <p className="text-xs text-muted-foreground">
                  快速预测 · 基于Elo评级和博彩赔率
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <StatCard
                label="预测场次"
                value={eloStats.total_matches.toString()}
                subtitle="已完赛比赛"
              />
              <StatCard
                label="胜负准确率"
                value={percentage(eloStats.outcome_accuracy)}
                color={eloStats.outcome_accuracy >= 0.7 ? "text-pos" : eloStats.outcome_accuracy >= 0.6 ? "text-warn" : "text-neg"}
                subtitle="预测胜平负正确"
              />
              <StatCard
                label="完全命中率"
                value={percentage(eloStats.exact_score_rate)}
                color="text-primary"
                subtitle="比分完全正确"
              />
              <StatCard
                label="净胜球准确率"
                value={percentage(eloStats.goal_diff_accuracy)}
                subtitle="净胜球数正确"
              />
            </div>

            <StatCard
              label="平均比分误差"
              value={eloStats.avg_score_error.toFixed(2)}
              subtitle="预测分数与实际分数的平均偏差"
            />
          </div>
        )}

        {/* Hybrid Engine */}
        {hybridStats && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 rounded-lg border bg-secondary px-4 py-3">
              <Brain className="size-5 text-muted-foreground" />
              <div>
                <h3 className="font-semibold">混合引擎</h3>
                <p className="text-xs text-muted-foreground">
                  Rule+AI · 多因素综合分析
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <StatCard
                label="预测场次"
                value={hybridStats.total_matches.toString()}
                subtitle="已完赛比赛"
              />
              <StatCard
                label="胜负准确率"
                value={percentage(hybridStats.outcome_accuracy)}
                color={hybridStats.outcome_accuracy >= 0.7 ? "text-pos" : hybridStats.outcome_accuracy >= 0.6 ? "text-warn" : "text-neg"}
                subtitle="预测胜平负正确"
              />
              <StatCard
                label="完全命中率"
                value={percentage(hybridStats.exact_score_rate)}
                color="text-primary"
                subtitle="比分完全正确"
              />
              <StatCard
                label="净胜球准确率"
                value={percentage(hybridStats.goal_diff_accuracy)}
                subtitle="净胜球数正确"
              />
            </div>

            <StatCard
              label="平均比分误差"
              value={hybridStats.avg_score_error.toFixed(2)}
              subtitle="预测分数与实际分数的平均偏差"
            />
          </div>
        )}
      </div>

      {/* Winner Badge */}
      {eloStats && hybridStats && (
        <div className="rounded-lg border bg-card p-6">
          <div className="flex items-center gap-3">
            <Award className="size-6 text-primary" />
            <div>
              <h3 className="font-semibold">综合表现</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {eloStats.outcome_accuracy > hybridStats.outcome_accuracy ? (
                  <span>
                    <span className="font-medium text-primary">Elo+赔率引擎</span> 在胜负预测上表现更优（
                    {percentage(eloStats.outcome_accuracy)} vs {percentage(hybridStats.outcome_accuracy)}）
                  </span>
                ) : eloStats.outcome_accuracy < hybridStats.outcome_accuracy ? (
                  <span>
                    <span className="font-medium">混合引擎</span> 在胜负预测上表现更优（
                    {percentage(hybridStats.outcome_accuracy)} vs {percentage(eloStats.outcome_accuracy)}）
                  </span>
                ) : (
                  <span>两种引擎在胜负预测上表现相当</span>
                )}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
