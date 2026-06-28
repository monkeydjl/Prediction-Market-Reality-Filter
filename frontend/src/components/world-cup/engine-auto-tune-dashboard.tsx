"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Brain, Loader2, AlertCircle, TrendingUp, Target, Zap, RefreshCw, GitCompare, BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getWorldCupApiBase } from "@/lib/env";
import { getOperatorApiKey } from "@/lib/api";

interface AutoTuneResult {
  status: string;
  message?: string;
  engine: string;
  optimization_summary?: {
    matches_processed: number;
    optimizations_generated: number;
    errors: number;
  };
  pattern_analysis?: {
    avg_home_score_adjustment: number;
    avg_away_score_adjustment: number;
    avg_home_win_prob_adjustment: number;
    avg_draw_prob_adjustment: number;
    avg_away_win_prob_adjustment: number;
    avg_confidence_adjustment: number;
  };
  calibration?: {
    calibration_id: number;
    engine: string;
    version: number;
    params: Record<string, number>;
  };
  top_blind_spots?: [string, number][];
  top_calibration_issues?: [string, number][];
}

interface CalibrationInfo {
  engine: string;
  version: number;
  params: Record<string, number>;
  based_on_matches: number;
  created_at: string;
}

interface TaskLogEntry {
  message: string;
}

interface TaskStatus {
  task_id?: string;
  status: "pending" | "running" | "completed" | "failed";
  message?: string;
  progress?: number;
  total?: number;
  current_match?: string;
  logs?: TaskLogEntry[];
  error?: string;
  result?: AutoTuneResult;
}

type EngineKey = "elo_odds" | "hybrid" | "integrated" | "gbm";

export function EngineAutoTuneDashboard() {
  const [tuning, setTuning] = useState(false);
  const [tuneResult, setTuneResult] = useState<AutoTuneResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedEngine, setSelectedEngine] = useState<EngineKey>("elo_odds");
  const [calibration, setCalibration] = useState<CalibrationInfo | null>(null);
  const [loadingCalibration, setLoadingCalibration] = useState(false);
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null);
  const [patterns, setPatterns] = useState<Record<string, unknown> | null>(null);
  const [loadingPatterns, setLoadingPatterns] = useState(false);
  const [batchOptimizing, setBatchOptimizing] = useState(false);
  const [batchResult, setBatchResult] = useState<Record<string, unknown> | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const loadCalibration = useCallback(async (engine: string) => {
    setLoadingCalibration(true);
    try {
      const response = await fetch(
        `${getWorldCupApiBase()}/api/world-cup/predictions/calibration/${engine}`,
        { cache: "no-store" }
      );

      if (response.ok) {
        const data: { status: string; calibration?: CalibrationInfo } = await response.json();
        if (data.status === "ok") {
          setCalibration(data.calibration ?? null);
        } else {
          setCalibration(null);
        }
      }
    } catch (err) {
      console.error("Failed to load calibration:", err);
    } finally {
      setLoadingCalibration(false);
    }
  }, []);

  const loadPatterns = useCallback(async (engine: string) => {
    setLoadingPatterns(true);
    try {
      const response = await fetch(
        `${getWorldCupApiBase()}/api/world-cup/predictions/calibration-patterns/${engine}`,
        { cache: "no-store" }
      );
      if (response.ok) {
        const data = await response.json();
        setPatterns(data);
      } else {
        setPatterns(null);
      }
    } catch (err) {
      console.error("Failed to load calibration patterns:", err);
      setPatterns(null);
    } finally {
      setLoadingPatterns(false);
    }
  }, []);

  useEffect(() => {
    loadCalibration(selectedEngine);
    loadPatterns(selectedEngine);
  }, [selectedEngine, loadCalibration, loadPatterns]);

  const handleBatchOptimize = async () => {
    setBatchOptimizing(true);
    setBatchResult(null);
    try {
      const headers: Record<string, string> = {};
      const key = getOperatorApiKey();
      if (key) headers["X-API-Key"] = key;
      const response = await fetch(
        `${getWorldCupApiBase()}/api/world-cup/predictions/batch-optimize?engine=${selectedEngine}&limit=10`,
        { method: "POST", headers, cache: "no-store" }
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(data.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      setBatchResult(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(`批量优化失败: ${message}`);
    } finally {
      setBatchOptimizing(false);
    }
  };

  const startPolling = useCallback((taskId: string) => {
    // Clear any existing interval before starting a new one
    stopPolling();

    const interval = setInterval(async () => {
      try {
        const response = await fetch(
          `${getWorldCupApiBase()}/api/world-cup/predictions/auto-tune/status/${taskId}`,
          { cache: "no-store" }
        );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data: { task: TaskStatus } = await response.json();
        const task = data.task;

        setTaskStatus(task);

        if (task.status === "completed") {
          setTuning(false);
          setTuneResult(task.result ?? null);
          stopPolling();
          loadCalibration(selectedEngine);
          loadPatterns(selectedEngine);
        } else if (task.status === "failed") {
          setTuning(false);
          setError(`优化失败: ${task.error ?? "未知错误"}`);
          stopPolling();
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 2000); // Poll every 2 seconds

    pollingRef.current = interval;
  }, [stopPolling, selectedEngine, loadCalibration]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const handleAutoTune = async () => {
    setTuning(true);
    setError(null);
    setTuneResult(null);
    setTaskStatus(null);

    try {
      const headers: Record<string, string> = {};
      const key = getOperatorApiKey();
      if (key) headers["X-API-Key"] = key;

      const response = await fetch(
        `${getWorldCupApiBase()}/api/world-cup/predictions/auto-tune/${selectedEngine}?background=true`,
        {
          method: "POST",
          headers,
          cache: "no-store"
        }
      );

      if (!response.ok) {
        const data = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(data.detail || `HTTP ${response.status}`);
      }

      const data: { status: string; task_id?: string; message?: string } = await response.json();

      if (data.status === "accepted" && data.task_id) {
        // Start polling for task status
        setTaskStatus({
          task_id: data.task_id,
          status: "pending",
          message: data.message
        });
        startPolling(data.task_id);
      } else {
        throw new Error("未返回任务ID");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(`自动调教失败: ${message}`);
      setTuning(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-lg border bg-card p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">引擎自动调教</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              基于 AI 优化反馈自动校准预测引擎参数
            </p>
          </div>
          <Brain className="size-8 text-primary opacity-50" />
        </div>

        {/* Engine Selection */}
        <div className="mt-6 space-y-3">
          <label className="text-sm font-medium">选择引擎</label>
          <div className="grid gap-3 md:grid-cols-4">
            <button
              onClick={() => setSelectedEngine("elo_odds")}
              className={cn(
                "rounded-lg border p-4 text-left transition-all",
                selectedEngine === "elo_odds"
                  ? "border-primary bg-primary/10"
                  : "border-border hover:border-primary/50"
              )}
            >
              <div className="flex items-center gap-2">
                <Zap className="size-4" aria-hidden="true" />
                <span className="font-medium">Elo+赔率</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                基于 Elo 评级和博彩赔率的融合引擎
              </p>
            </button>
            <button
              onClick={() => setSelectedEngine("hybrid")}
              className={cn(
                "rounded-lg border p-4 text-left transition-all",
                selectedEngine === "hybrid"
                  ? "border-primary bg-primary/10"
                  : "border-border hover:border-primary/50"
              )}
            >
              <div className="flex items-center gap-2">
                <Brain className="size-4" aria-hidden="true" />
                <span className="font-medium">混合引擎</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                结合规则模型和 AI 的混合预测引擎
              </p>
            </button>
            <button
              onClick={() => setSelectedEngine("integrated")}
              className={cn(
                "rounded-lg border p-4 text-left transition-all",
                selectedEngine === "integrated"
                  ? "border-primary bg-primary/10"
                  : "border-border hover:border-primary/50"
              )}
            >
              <div className="flex items-center gap-2">
                <GitCompare className="size-4" aria-hidden="true" />
                <span className="font-medium">集成引擎</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                融合 Elo+赔率 与混合引擎的集成预测
              </p>
            </button>
            <button
              onClick={() => setSelectedEngine("gbm")}
              className={cn(
                "rounded-lg border p-4 text-left transition-all",
                selectedEngine === "gbm"
                  ? "border-teal-500 bg-teal-500/10"
                  : "border-border hover:border-teal-500/50"
              )}
            >
              <div className="flex items-center gap-2">
                <BarChart3 className="size-4" aria-hidden="true" />
                <span className="font-medium">GBM</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                梯度提升模型预测引擎
              </p>
            </button>
          </div>

          <button
            onClick={handleAutoTune}
            disabled={tuning}
            title="基于 AI 优化反馈校准引擎参数（会写入新校准版本）"
            className="mt-4 w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {tuning ? (
              <span className="flex items-center justify-center gap-2">
                <Loader2 className="size-4 animate-spin" />
                调教中...
              </span>
            ) : (
              <span className="flex items-center justify-center gap-2">
                <RefreshCw className="size-4" />
                开始自动调教
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Error Display */}
      {/* Status Display */}
      {tuning && taskStatus && (
        <div className="mt-4 rounded-lg border bg-muted/50 p-4">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">
                {taskStatus.status === "pending" && "准备中..."}
                {taskStatus.status === "running" && "正在优化..."}
                {taskStatus.status === "completed" && "完成"}
                {taskStatus.status === "failed" && "失败"}
              </span>
              {(taskStatus.progress ?? 0) > 0 && (taskStatus.total ?? 0) > 0 && (
                <span className="text-sm text-muted-foreground">
                  {taskStatus.progress} / {taskStatus.total}
                </span>
              )}
            </div>

            {(taskStatus.progress ?? 0) > 0 && (taskStatus.total ?? 0) > 0 && (
              <div className="h-2 overflow-hidden rounded-full bg-secondary">
                <div
                  className="h-full bg-primary transition-all duration-300"
                  style={{ width: `${((taskStatus.progress ?? 0) / (taskStatus.total ?? 1)) * 100}%` }}
                />
              </div>
            )}

            {taskStatus.current_match && (
              <p className="text-sm text-muted-foreground">
                当前: {taskStatus.current_match}
              </p>
            )}

            {taskStatus.logs && taskStatus.logs.length > 0 && (
              <div className="max-h-32 overflow-y-auto rounded border bg-background p-2 text-xs font-mono">
                {taskStatus.logs.map((log: TaskLogEntry, idx: number) => (
                  <div key={idx} className="text-muted-foreground">
                    {log.message}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-neg/40 bg-neg/10 p-4">
          <div className="flex items-start gap-2 text-sm text-neg">
            <AlertCircle className="size-4 flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        </div>
      )}

      {/* Tuning Results */}
      {tuneResult && (
        <div className="space-y-4">
          {tuneResult.status === "no_data" ? (
            <div className="rounded-lg border border-muted bg-muted/20 p-6">
              <div className="flex items-start gap-3">
                <AlertCircle className="size-5 flex-shrink-0 mt-0.5 text-muted-foreground" />
                <div>
                  <h3 className="font-semibold text-foreground">无可用数据</h3>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {tuneResult.message || "当前没有使用该引擎的比赛预测，无法进行自动调优"}
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    提示：请确保已生成使用 <span className="font-mono">{selectedEngine}</span> 引擎的比赛预测
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <>
              {/* Summary */}
              <div className="rounded-lg border bg-card p-6">
                <h3 className="flex items-center gap-2 text-base font-semibold">
                  <Target className="size-4 text-primary" />
                  调教结果
                </h3>

                <div className="mt-4 grid grid-cols-3 gap-4">
                  <div className="rounded-md border bg-secondary/30 p-3">
                    <div className="text-xs text-muted-foreground">处理比赛</div>
                    <div className="mt-1 text-2xl font-bold tabular-nums">
                      {tuneResult.optimization_summary?.matches_processed || 0}
                    </div>
                  </div>
                  <div className="rounded-md border bg-secondary/30 p-3">
                    <div className="text-xs text-muted-foreground">生成优化</div>
                    <div className="mt-1 text-2xl font-bold tabular-nums">
                      {tuneResult.optimization_summary?.optimizations_generated || 0}
                    </div>
                  </div>
                  <div className="rounded-md border bg-secondary/30 p-3">
                    <div className="text-xs text-muted-foreground">错误数</div>
                    <div className="mt-1 text-2xl font-bold tabular-nums">
                      {tuneResult.optimization_summary?.errors || 0}
                </div>
              </div>
            </div>
          </div>

          {/* Pattern Analysis */}
          <div className="rounded-lg border bg-card p-6">
            <h3 className="text-base font-semibold">模式分析</h3>
            <div className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">主队比分调整</span>
                <span className="font-mono font-medium tabular-nums">
                  {(tuneResult.pattern_analysis?.avg_home_score_adjustment ?? 0) > 0 ? "+" : ""}
                  {tuneResult.pattern_analysis?.avg_home_score_adjustment?.toFixed(3) || "0.000"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">客队比分调整</span>
                <span className="font-mono font-medium tabular-nums">
                  {(tuneResult.pattern_analysis?.avg_away_score_adjustment ?? 0) > 0 ? "+" : ""}
                  {tuneResult.pattern_analysis?.avg_away_score_adjustment?.toFixed(3) || "0.000"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">主胜概率调整</span>
                <span className="font-mono font-medium tabular-nums">
                  {(tuneResult.pattern_analysis?.avg_home_win_prob_adjustment ?? 0) > 0 ? "+" : ""}
                  {((tuneResult.pattern_analysis?.avg_home_win_prob_adjustment ?? 0) * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">平局概率调整</span>
                <span className="font-mono font-medium tabular-nums">
                  {(tuneResult.pattern_analysis?.avg_draw_prob_adjustment ?? 0) > 0 ? "+" : ""}
                  {((tuneResult.pattern_analysis?.avg_draw_prob_adjustment ?? 0) * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">客胜概率调整</span>
                <span className="font-mono font-medium tabular-nums">
                  {(tuneResult.pattern_analysis?.avg_away_win_prob_adjustment ?? 0) > 0 ? "+" : ""}
                  {((tuneResult.pattern_analysis?.avg_away_win_prob_adjustment ?? 0) * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">置信度调整</span>
                <span className="font-mono font-medium tabular-nums">
                  {(tuneResult.pattern_analysis?.avg_confidence_adjustment ?? 0) > 0 ? "+" : ""}
                  {((tuneResult.pattern_analysis?.avg_confidence_adjustment ?? 0) * 100).toFixed(1)}%
                </span>
              </div>
            </div>
          </div>

          {/* Top Issues */}
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-lg border bg-card p-6">
              <h3 className="text-base font-semibold">高频数据盲点</h3>
              <div className="mt-4 space-y-2">
                {(tuneResult.top_blind_spots?.length ?? 0) > 0 ? (
                  tuneResult.top_blind_spots!.map(([spot, count], idx) => (
                    <div key={idx} className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">{spot}</span>
                      <span className="font-mono font-medium tabular-nums">{count}次</span>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-muted-foreground">无数据</p>
                )}
              </div>
            </div>

            <div className="rounded-lg border bg-card p-6">
              <h3 className="text-base font-semibold">高频校准问题</h3>
              <div className="mt-4 space-y-2">
                {(tuneResult.top_calibration_issues?.length ?? 0) > 0 ? (
                  tuneResult.top_calibration_issues!.map(([issue, count], idx) => (
                    <div key={idx} className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">{issue}</span>
                      <span className="font-mono font-medium tabular-nums">{count}次</span>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-muted-foreground">无数据</p>
                )}
              </div>
            </div>
          </div>

          {/* Calibration Version */}
          <div className="rounded-lg border border-primary/40 bg-primary/5 p-6">
            <h3 className="flex items-center gap-2 text-base font-semibold text-primary">
              <TrendingUp className="size-4" />
              新校准版本已保存
            </h3>
            <div className="mt-3 text-sm">
              <p>
                <span className="text-muted-foreground">引擎:</span>{" "}
                <span className="font-medium">{tuneResult.calibration?.engine}</span>
              </p>
              <p className="mt-1">
                <span className="text-muted-foreground">版本:</span>{" "}
                <span className="font-medium">v{tuneResult.calibration?.version}</span>
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                该校准将自动应用于后续的预测生成
              </p>
            </div>
          </div>
            </>
          )}
        </div>
      )}

      {/* Current Calibration */}
      {calibration && (
        <div className="rounded-lg border bg-card p-6">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold">当前激活校准</h3>
            <button
              onClick={() => loadCalibration(selectedEngine)}
              disabled={loadingCalibration}
              className="text-xs text-primary hover:underline"
            >
              刷新
            </button>
          </div>

          <div className="mt-4 space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">引擎</span>
              <span className="font-medium">{calibration.engine}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">版本</span>
              <span className="font-medium">v{calibration.version}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">基于比赛数</span>
              <span className="font-medium">{calibration.based_on_matches}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">创建时间</span>
              <span className="font-medium">
                {new Date(calibration.created_at).toLocaleString("zh-CN")}
              </span>
            </div>
          </div>

          <details className="mt-4">
            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
              查看参数详情
            </summary>
            <pre className="mt-2 rounded-md bg-secondary p-3 text-xs overflow-x-auto">
              {JSON.stringify(calibration.params, null, 2)}
            </pre>
          </details>
        </div>
      )}

      {/* Calibration Patterns */}
      {patterns && typeof patterns === "object" && (
        <div className="rounded-lg border bg-card p-6">
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <BarChart3 className="size-4 text-primary" />
              校准模式分析
            </h3>
            {loadingPatterns && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
          </div>
          <div className="mt-4 space-y-2 text-sm">
            {Object.entries(patterns).map(([key, value]) => (
              <div key={key} className="flex justify-between">
                <span className="text-muted-foreground">{key.replace(/_/g, " ")}</span>
                <span className="font-mono font-medium tabular-nums">
                  {typeof value === "number" ? value.toFixed(4) : String(value)}
                </span>
              </div>
            ))}
            {Object.keys(patterns).length === 0 && (
              <p className="text-muted-foreground">暂无校准模式数据</p>
            )}
          </div>
        </div>
      )}

      {/* Batch Optimize */}
      <div className="rounded-lg border bg-card p-6">
        <h3 className="text-base font-semibold">批量优化</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          对当前引擎的近期比赛执行 AI 批量优化分析
        </p>
        <button
          onClick={handleBatchOptimize}
          disabled={batchOptimizing}
          title="对当前引擎近 10 场比赛执行 AI 批量优化分析（写操作，可能消耗配额）"
          className="mt-4 w-full rounded-md bg-secondary px-4 py-2.5 text-sm font-medium transition-colors hover:bg-secondary/80 disabled:opacity-50"
        >
          {batchOptimizing ? (
            <span className="flex items-center justify-center gap-2">
              <Loader2 className="size-4 animate-spin" />
              优化中...
            </span>
          ) : (
            <span className="flex items-center justify-center gap-2">
              <BarChart3 className="size-4" />
              执行批量优化 (10场)
            </span>
          )}
        </button>

        {batchResult && (
          <div className="mt-4 rounded-md border bg-secondary/30 p-4 text-sm">
            <div className="grid grid-cols-3 gap-3">
              <div>
                <div className="text-xs text-muted-foreground">处理场次</div>
                <div className="font-mono font-medium tabular-nums">
                  {(batchResult as Record<string, unknown>).matches_processed != null
                    ? String((batchResult as Record<string, unknown>).matches_processed)
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">生成优化</div>
                <div className="font-mono font-medium tabular-nums">
                  {(batchResult as Record<string, unknown>).optimizations_generated != null
                    ? String((batchResult as Record<string, unknown>).optimizations_generated)
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">错误数</div>
                <div className="font-mono font-medium tabular-nums">
                  {(batchResult as Record<string, unknown>).errors != null
                    ? String((batchResult as Record<string, unknown>).errors)
                    : "—"}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
