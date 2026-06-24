"use client";

import { useEffect, useState } from "react";
import { BarChart3, Target, Database, Activity, TrendingUp, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { getWorldCupApiBase } from "@/lib/env";

const API_BASE = getWorldCupApiBase();

interface EngineStats {
  total_predictions: number;
  by_engine: {
    elo_odds: {
      count: number;
      percentage: number;
      avg_confidence: number;
    };
    hybrid: {
      count: number;
      percentage: number;
      avg_confidence: number;
    };
  };
}

interface AccuracyStats {
  total_matches: number;
  outcome_accuracy: number;
  avg_score_mae: number;
  avg_brier_score: number;
  exact_score_correct: number;
}

interface OddsCacheStats {
  total_entries: number;
  fresh_count: number;
  stale_count: number;
  estimated_api_calls_saved: number;
  cache_hit_rate: number;
}

interface SystemHealth {
  status: "healthy" | "stale";
  recent_predictions_24h: number;
  cache_entries: number;
  data_freshness_hours: number;
  last_update: string | null;
}

export function AnalyticsDashboard() {
  const [engineStats, setEngineStats] = useState<EngineStats | null>(null);
  const [accuracyStats, setAccuracyStats] = useState<AccuracyStats | null>(null);
  const [cacheStats, setCacheStats] = useState<OddsCacheStats | null>(null);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchAnalytics() {
      try {
        setIsLoading(true);
        setError(null);

        const [engineRes, accuracyRes, cacheRes, healthRes] = await Promise.all([
          fetch(`${API_BASE}/api/analytics/engine-stats`),
          fetch(`${API_BASE}/api/analytics/accuracy-stats`),
          fetch(`${API_BASE}/api/analytics/odds-cache-stats`),
          fetch(`${API_BASE}/api/analytics/system-health`)
        ]);

        if (!engineRes.ok || !accuracyRes.ok || !cacheRes.ok || !healthRes.ok) {
          throw new Error("Failed to fetch analytics data");
        }

        const [engine, accuracy, cache, health] = await Promise.all([
          engineRes.json(),
          accuracyRes.json(),
          cacheRes.json(),
          healthRes.json()
        ]);

        setEngineStats(engine);
        setAccuracyStats(accuracy);
        setCacheStats(cache);
        setSystemHealth(health);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setIsLoading(false);
      }
    }

    fetchAnalytics();
  }, []);

  if (isLoading) {
    return (
      <div className="rounded-lg border bg-card p-8 text-center">
        <div className="inline-flex items-center gap-2 text-muted-foreground">
          <div className="size-5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          <span>加载分析数据...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-neg/40 bg-neg/10 p-4">
        <div className="flex items-center gap-2 text-neg">
          <AlertCircle className="size-4" />
          <span className="text-sm font-medium">加载失败: {error}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* System Health Banner */}
      {systemHealth && (
        <div className={cn(
          "rounded-lg border p-4",
          systemHealth.status === "healthy" ? "border-pos/40 bg-pos/10" : "border-warn/40 bg-warn/10"
        )}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className={cn(
                "size-4",
                systemHealth.status === "healthy" ? "text-pos" : "text-warn"
              )} />
              <span className={cn(
                "text-sm font-medium",
                systemHealth.status === "healthy" ? "text-pos" : "text-warn"
              )}>
                系统状态: {systemHealth.status === "healthy" ? "正常" : "数据陈旧"}
              </span>
            </div>
            <div className="text-xs text-muted-foreground">
              最近24小时预测: {systemHealth.recent_predictions_24h} 次
            </div>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-4 text-xs">
            <div>
              <span className="text-muted-foreground">缓存条目:</span>{" "}
              <span className="font-mono font-medium tabular-nums">{systemHealth.cache_entries}</span>
            </div>
            <div>
              <span className="text-muted-foreground">数据新鲜度:</span>{" "}
              <span className="font-mono font-medium tabular-nums">{systemHealth.data_freshness_hours.toFixed(1)}小时</span>
            </div>
            <div>
              <span className="text-muted-foreground">最后更新:</span>{" "}
              <span className="font-mono text-xs tabular-nums">
                {systemHealth.last_update ? new Date(systemHealth.last_update).toLocaleString("zh-CN") : "无"}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* Engine Statistics */}
        {engineStats && (
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center gap-2 border-b pb-3">
              <BarChart3 className="size-4 text-primary" />
              <h3 className="text-sm font-semibold">引擎使用统计</h3>
            </div>
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">总预测数</span>
                <span className="font-mono text-lg font-bold tabular-nums">{engineStats.total_predictions}</span>
              </div>

              <div className="space-y-2">
                <div className="rounded-md bg-secondary/50 p-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">Elo+赔率引擎</span>
                    <span className="text-muted-foreground">{engineStats.by_engine.elo_odds.count} 次</span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                      <div
                        className="h-full bg-primary transition-all"
                        style={{ width: `${engineStats.by_engine.elo_odds.percentage}%` }}
                      />
                    </div>
                    <span className="font-mono text-xs tabular-nums">
                      {engineStats.by_engine.elo_odds.percentage.toFixed(1)}%
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">
                    平均置信度: <span className="font-mono font-medium tabular-nums">
                      {(engineStats.by_engine.elo_odds.avg_confidence * 100).toFixed(1)}%
                    </span>
                  </div>
                </div>

                <div className="rounded-md bg-secondary/50 p-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">混合引擎</span>
                    <span className="text-muted-foreground">{engineStats.by_engine.hybrid.count} 次</span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                      <div
                        className="h-full bg-muted-foreground transition-all"
                        style={{ width: `${engineStats.by_engine.hybrid.percentage}%` }}
                      />
                    </div>
                    <span className="font-mono text-xs tabular-nums">
                      {engineStats.by_engine.hybrid.percentage.toFixed(1)}%
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">
                    平均置信度: <span className="font-mono font-medium tabular-nums">
                      {(engineStats.by_engine.hybrid.avg_confidence * 100).toFixed(1)}%
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Accuracy Statistics */}
        {accuracyStats && (
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center gap-2 border-b pb-3">
              <Target className="size-4 text-primary" />
              <h3 className="text-sm font-semibold">预测准确率</h3>
            </div>
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">已验证比赛</span>
                <span className="font-mono text-lg font-bold tabular-nums">{accuracyStats.total_matches}</span>
              </div>

              <div className="space-y-2">
                <div className="rounded-md bg-secondary/50 p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">结果准确率</span>
                    <span className={cn(
                      "font-mono text-lg font-bold tabular-nums",
                      accuracyStats.outcome_accuracy >= 0.6 ? "text-pos" : "text-warn"
                    )}>
                      {(accuracyStats.outcome_accuracy * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-secondary">
                    <div
                      className={cn(
                        "h-full transition-all",
                        accuracyStats.outcome_accuracy >= 0.6 ? "bg-pos" : "bg-warn"
                      )}
                      style={{ width: `${accuracyStats.outcome_accuracy * 100}%` }}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-md bg-secondary/50 p-3">
                    <div className="text-xs text-muted-foreground">比分MAE</div>
                    <div className="mt-1 font-mono text-lg font-bold tabular-nums">
                      {accuracyStats.avg_score_mae.toFixed(2)}
                    </div>
                  </div>
                  <div className="rounded-md bg-secondary/50 p-3">
                    <div className="text-xs text-muted-foreground">Brier得分</div>
                    <div className="mt-1 font-mono text-lg font-bold tabular-nums">
                      {accuracyStats.avg_brier_score.toFixed(3)}
                    </div>
                  </div>
                </div>

                <div className="rounded-md border bg-secondary/30 px-3 py-2 text-xs">
                  <span className="text-muted-foreground">完全命中:</span>{" "}
                  <span className="font-mono font-medium tabular-nums">{accuracyStats.exact_score_correct}</span> 场
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Cache Statistics */}
        {cacheStats && (
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center gap-2 border-b pb-3">
              <Database className="size-4 text-primary" />
              <h3 className="text-sm font-semibold">赔率缓存统计</h3>
            </div>
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">缓存条目</span>
                <span className="font-mono text-lg font-bold tabular-nums">{cacheStats.total_entries}</span>
              </div>

              <div className="space-y-2">
                <div className="rounded-md bg-secondary/50 p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">缓存命中率</span>
                    <span className={cn(
                      "font-mono text-lg font-bold tabular-nums",
                      cacheStats.cache_hit_rate >= 0.7 ? "text-pos" : "text-warn"
                    )}>
                      {(cacheStats.cache_hit_rate * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-secondary">
                    <div
                      className={cn(
                        "h-full transition-all",
                        cacheStats.cache_hit_rate >= 0.7 ? "bg-pos" : "bg-warn"
                      )}
                      style={{ width: `${cacheStats.cache_hit_rate * 100}%` }}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-md bg-secondary/50 p-3">
                    <div className="text-xs text-muted-foreground">新鲜</div>
                    <div className="mt-1 font-mono text-lg font-bold tabular-nums text-pos">
                      {cacheStats.fresh_count}
                    </div>
                  </div>
                  <div className="rounded-md bg-secondary/50 p-3">
                    <div className="text-xs text-muted-foreground">过期</div>
                    <div className="mt-1 font-mono text-lg font-bold tabular-nums text-muted-foreground">
                      {cacheStats.stale_count}
                    </div>
                  </div>
                </div>

                <div className="rounded-md border border-pos/40 bg-pos/10 px-3 py-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-pos">节省API调用</span>
                    <span className="font-mono text-sm font-bold tabular-nums text-pos">
                      ~{cacheStats.estimated_api_calls_saved} 次
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Key Metrics Summary */}
        <div className="rounded-lg border bg-card p-4">
          <div className="flex items-center gap-2 border-b pb-3">
            <TrendingUp className="size-4 text-primary" />
            <h3 className="text-sm font-semibold">关键指标</h3>
          </div>
          <div className="mt-4 space-y-3">
            {accuracyStats && (
              <div className="rounded-md bg-secondary/50 p-3">
                <div className="text-xs text-muted-foreground">预测质量评级</div>
                <div className={cn(
                  "mt-2 text-2xl font-bold",
                  accuracyStats.outcome_accuracy >= 0.7 ? "text-pos" :
                  accuracyStats.outcome_accuracy >= 0.5 ? "text-warn" : "text-neg"
                )}>
                  {accuracyStats.outcome_accuracy >= 0.7 ? "优秀" :
                   accuracyStats.outcome_accuracy >= 0.5 ? "良好" : "需改进"}
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  基于 {accuracyStats.total_matches} 场已验证比赛
                </div>
              </div>
            )}

            {engineStats && cacheStats && (
              <div className="space-y-2">
                <div className="flex items-center justify-between rounded-md bg-secondary/30 px-3 py-2 text-xs">
                  <span className="text-muted-foreground">总预测量</span>
                  <span className="font-mono font-medium tabular-nums">{engineStats.total_predictions}</span>
                </div>
                <div className="flex items-center justify-between rounded-md bg-secondary/30 px-3 py-2 text-xs">
                  <span className="text-muted-foreground">API效率提升</span>
                  <span className="font-mono font-medium tabular-nums text-pos">
                    {cacheStats.total_entries > 0
                      ? `${((cacheStats.estimated_api_calls_saved / cacheStats.total_entries) * 100).toFixed(0)}%`
                      : "N/A"}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
