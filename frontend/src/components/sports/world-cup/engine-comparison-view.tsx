"use client";

import { useEffect, useState } from "react";
import { Zap, Brain, GitCompare, Target, Award, AlertCircle, Loader2, BarChart3, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  fetchEngineComparison,
  type EngineComparisonData,
  type EngineComparisonKey,
  type EngineComparisonStat,
} from "@/lib/world-cup/predictions-api";

type EngineKey = EngineComparisonKey;

type EngineStat = EngineComparisonStat;

interface EngineConfig {
  key: EngineKey;
  label: string;
  description: string;
  icon: LucideIcon;
  headerClass: string;
  iconClass: string;
  titleClass: string;
}

const ENGINE_CONFIGS: EngineConfig[] = [
  {
    key: "elo_odds",
    label: "Elo+赔率",
    description: "基于 Elo 评级和赔率信号",
    icon: Zap,
    headerClass: "bg-primary/5",
    iconClass: "text-primary",
    titleClass: "text-primary",
  },
  {
    key: "hybrid",
    label: "混合引擎",
    description: "多因素综合分析",
    icon: Brain,
    headerClass: "bg-secondary",
    iconClass: "text-muted-foreground",
    titleClass: "text-foreground",
  },
  {
    key: "integrated",
    label: "集成引擎",
    description: "融合 Elo+赔率 与混合引擎",
    icon: GitCompare,
    headerClass: "bg-primary/5",
    iconClass: "text-primary",
    titleClass: "text-primary",
  },
  {
    key: "gbm",
    label: "GBM",
    description: "梯度提升模型预测",
    icon: BarChart3,
    headerClass: "bg-teal-500/5",
    iconClass: "text-teal-500",
    titleClass: "text-teal-500",
  },
];

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

function EngineStatsPanel({ config, stats }: { config: EngineConfig; stats?: EngineStat }) {
  const Icon = config.icon;

  return (
    <div className="space-y-4">
      <div className={cn("flex items-center gap-2 rounded-lg border px-4 py-3", config.headerClass)}>
        <Icon className={cn("size-5", config.iconClass)} />
        <div>
          <h3 className={cn("font-semibold", config.titleClass)}>{config.label}</h3>
          <p className="text-xs text-muted-foreground">{config.description}</p>
        </div>
      </div>

      {!stats ? (
        <div className="rounded-lg border border-dashed bg-secondary/30 p-6 text-center text-sm text-muted-foreground">
          暂无已完赛统计
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3">
            <StatCard
              label="预测场次"
              value={stats.total_matches.toString()}
              subtitle="已完赛比赛"
            />
            <StatCard
              label="胜负准确率"
              value={percentage(stats.outcome_accuracy)}
              color={stats.outcome_accuracy >= 0.7 ? "text-pos" : stats.outcome_accuracy >= 0.6 ? "text-warn" : "text-neg"}
              subtitle="预测胜平负正确"
            />
            <StatCard
              label="完全命中率"
              value={percentage(stats.exact_score_rate)}
              color="text-primary"
              subtitle="比分完全正确"
            />
            <StatCard
              label="净胜球准确率"
              value={percentage(stats.goal_diff_accuracy)}
              subtitle="净胜球数正确"
            />
          </div>

          <StatCard
            label="平均比分误差"
            value={Number.isFinite(stats.avg_score_error) ? stats.avg_score_error.toFixed(2) : "—"}
            subtitle="预测分数与实际分数的平均偏差"
          />
        </>
      )}
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

        setData(await fetchEngineComparison());
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

  const hasAnyStats = data != null && ENGINE_CONFIGS.some((config) => data[config.key] != null);

  if (!hasAnyStats) {
    return (
      <div className="rounded-lg border border-dashed p-12 text-center">
        <Target className="mx-auto size-12 text-muted-foreground opacity-50" />
        <p className="mt-4 text-muted-foreground">暂无已完赛的比赛数据</p>
        <p className="mt-2 text-sm text-muted-foreground">比赛结束后将自动统计引擎准确率</p>
      </div>
    );
  }

  const availableEngines = ENGINE_CONFIGS
    .map((config) => ({ config, stats: data?.[config.key] }))
    .filter((item): item is { config: EngineConfig; stats: EngineStat } => (
      item.stats != null && item.stats.total_matches > 0
    ));
  const bestOutcome = availableEngines.length > 0
    ? Math.max(...availableEngines.map((item) => item.stats.outcome_accuracy))
    : 0;
  const bestEngines = availableEngines.filter((item) => item.stats.outcome_accuracy === bestOutcome);

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
      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        {ENGINE_CONFIGS.map((config) => (
          <EngineStatsPanel
            key={config.key}
            config={config}
            stats={data?.[config.key]}
          />
        ))}
      </div>

      {/* Winner Badge */}
      {availableEngines.length >= 2 && (
        <div className="rounded-lg border bg-card p-6">
          <div className="flex items-center gap-3">
            <Award className="size-6 text-primary" />
            <div>
              <h3 className="font-semibold">综合表现</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {bestEngines.length === 1 ? (
                  <span>
                    <span className="font-medium text-primary">{bestEngines[0].config.label}</span>
                    在胜负预测上表现更优（{percentage(bestOutcome)}）
                  </span>
                ) : (
                  <span>
                    {bestEngines.map((item) => item.config.label).join("、")}
                    在胜负预测上表现相当（{percentage(bestOutcome)}）
                  </span>
                )}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
