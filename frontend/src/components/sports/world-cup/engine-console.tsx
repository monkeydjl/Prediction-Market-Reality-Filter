"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  engineApi,
  streamBatchSwitchEngine,
  type AutoTuneTask,
  type BatchSummary,
  type CalibrationPatterns,
  type EngineCalibration,
  type EngineName,
  type TunableEngine,
} from "@/lib/world-cup/engine-api";

const ENGINES: { value: EngineName; label: string }[] = [
  { value: "elo_odds", label: "ELO + 赔率" },
  { value: "hybrid", label: "混合（规则 + AI）" },
  { value: "integrated", label: "融合" },
  { value: "high_confidence", label: "高置信自动选择" },
];

const TUNABLE_ENGINES: { value: TunableEngine; label: string }[] = [
  { value: "elo_odds", label: "ELO + 赔率" },
  { value: "hybrid", label: "混合（规则 + AI）" },
  { value: "integrated", label: "融合" },
];

const STATUS_FILTERS = ["scheduled", "live", "finished"];

/** Terminal auto-tune states — polling stops once one of these is reached. */
const TERMINAL_TASK_STATES = new Set(["completed", "failed", "error", "cancelled"]);
const TASK_POLL_MS = 3_000;

function summaryLine(summary: BatchSummary): string {
  if (summary.message && !summary.total) return summary.message;
  const parts = [
    `共 ${summary.total ?? 0}`,
    `成功 ${summary.succeeded ?? 0}`,
    `失败 ${summary.failed ?? 0}`,
    `跳过 ${summary.skipped ?? 0}`,
  ];
  const byEngine = [
    summary.elo_odds_count ? `ELO ${summary.elo_odds_count}` : "",
    summary.hybrid_count ? `混合 ${summary.hybrid_count}` : "",
    summary.integrated_count ? `融合 ${summary.integrated_count}` : "",
  ].filter(Boolean);
  return byEngine.length ? `${parts.join(" / ")}（${byEngine.join("，")}）` : parts.join(" / ");
}

const BTN =
  "rounded-md px-3 py-1.5 text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50";
const SELECT =
  "rounded-md border border-input bg-card px-2 py-1.5 text-sm text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/**
 * Operator console for the World Cup prediction engines: batch runs, engine
 * switching (streamed), AI optimization, auto-tuning, and calibration reads.
 *
 * Per OQ-5 the high-risk batch operations (anything that rewrites predictions
 * for every scheduled match) require a second explicit confirmation on top of
 * the existing operator API key.
 */
export function EngineConsole() {
  const [engine, setEngine] = useState<EngineName>("elo_odds");
  const [statusFilter, setStatusFilter] = useState("scheduled");
  const [tuneEngine, setTuneEngine] = useState<TunableEngine>("elo_odds");
  const [optimizeLimit, setOptimizeLimit] = useState(10);

  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [progress, setProgress] = useState<{ current: number; total: number; matchId: string } | null>(
    null,
  );
  const [confirming, setConfirming] = useState<string | null>(null);

  const [task, setTask] = useState<AutoTuneTask | null>(null);
  const [calibration, setCalibration] = useState<EngineCalibration | null>(null);
  const [patterns, setPatterns] = useState<CalibrationPatterns | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const taskIdRef = useRef<string | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(
    async <T,>(key: string, op: () => Promise<T>, onDone: (result: T) => void) => {
      setPending(key);
      setError(null);
      try {
        onDone(await op());
      } catch (e) {
        setError(e instanceof Error ? e.message : "操作失败");
      } finally {
        setPending(null);
      }
    },
    [],
  );

  // Poll the auto-tune task until it reaches a terminal state.
  useEffect(() => {
    const taskId = task?.task_id;
    if (!taskId || (task?.status && TERMINAL_TASK_STATES.has(task.status))) return;
    taskIdRef.current = taskId;

    let cancelled = false;
    const timer = globalThis.setInterval(async () => {
      if (document.hidden) return;
      try {
        const res = await engineApi.autoTuneStatus(taskId);
        if (!cancelled && res.task) setTask(res.task);
      } catch {
        // Transient poll failure: keep the last known task state and retry.
      }
    }, TASK_POLL_MS);

    return () => {
      cancelled = true;
      globalThis.clearInterval(timer);
    };
  }, [task?.task_id, task?.status]);

  const startStreamedSwitch = useCallback(() => {
    setConfirming(null);
    setPending("switch");
    setError(null);
    setSummary(null);
    setProgress(null);

    const controller = new AbortController();
    abortRef.current = controller;

    void streamBatchSwitchEngine(engine, statusFilter, {
      signal: controller.signal,
      onStart: ({ total }) => setProgress({ current: 0, total, matchId: "" }),
      onProgress: (p) =>
        setProgress({ current: p.current, total: p.total, matchId: p.match_id }),
      onComplete: (result) => {
        setSummary(result);
        setPending(null);
      },
      onError: (message) => {
        setError(message);
        setPending(null);
      },
    }).finally(() => {
      abortRef.current = null;
      setPending((current) => (current === "switch" ? null : current));
    });
  }, [engine, statusFilter]);

  const busy = pending !== null;

  return (
    <section data-testid="engine-console" className="space-y-6">
      {error && (
        <p
          data-testid="engine-console-error"
          role="alert"
          className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-sm text-neg"
        >
          {error}
        </p>
      )}

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold">批量引擎操作</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          批量操作会覆盖所选状态下所有比赛的预测结果，需二次确认。
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            引擎
            <select
              data-testid="engine-select"
              value={engine}
              onChange={(e) => setEngine(e.target.value as EngineName)}
              className={SELECT}
            >
              {ENGINES.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            状态过滤
            <select
              data-testid="engine-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className={SELECT}
            >
              {STATUS_FILTERS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            data-testid="batch-switch-button"
            disabled={busy}
            onClick={() => setConfirming("switch")}
            className={`${BTN} bg-primary text-primary-foreground hover:opacity-90`}
          >
            {pending === "switch" ? "切换中…" : "批量切换引擎（流式）"}
          </button>
          <button
            type="button"
            data-testid="batch-predict-button"
            disabled={busy}
            onClick={() => setConfirming("predict")}
            className={`${BTN} border border-border text-foreground hover:bg-muted`}
          >
            {pending === "predict" ? "预测中…" : "批量预测"}
          </button>
        </div>

        {confirming && (
          <div
            data-testid="engine-confirm"
            role="alertdialog"
            aria-label="确认批量操作"
            className="mt-3 rounded-md border border-warn/40 bg-warn/10 p-3"
          >
            <p className="text-sm text-foreground">
              {confirming === "switch"
                ? `确认将状态为 ${statusFilter} 的全部比赛切换到「${
                    ENGINES.find((e) => e.value === engine)?.label ?? engine
                  }」？此操作会覆盖现有预测。`
                : `确认使用「${
                    ENGINES.find((e) => e.value === engine)?.label ?? engine
                  }」重新预测全部剩余比赛？`}
            </p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                data-testid="engine-confirm-yes"
                onClick={() => {
                  if (confirming === "switch") {
                    startStreamedSwitch();
                  } else {
                    setConfirming(null);
                    setProgress(null);
                    void run("predict", () => engineApi.batchPredict(engine), setSummary);
                  }
                }}
                className={`${BTN} bg-neg text-neg-foreground hover:opacity-90`}
              >
                确认执行
              </button>
              <button
                type="button"
                data-testid="engine-confirm-no"
                onClick={() => setConfirming(null)}
                className={`${BTN} border border-border text-foreground hover:bg-muted`}
              >
                取消
              </button>
            </div>
          </div>
        )}

        {progress && (
          <div data-testid="engine-progress" className="mt-3 space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                {progress.current} / {progress.total}
                {progress.matchId ? ` · ${progress.matchId}` : ""}
              </span>
              <span>
                {progress.total > 0
                  ? `${Math.round((progress.current / progress.total) * 100)}%`
                  : "0%"}
              </span>
            </div>
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={progress.total}
              aria-valuenow={progress.current}
              aria-label="批量切换进度"
              className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            >
              <div
                className="h-full bg-primary transition-all"
                style={{
                  width: progress.total > 0 ? `${(progress.current / progress.total) * 100}%` : "0%",
                }}
              />
            </div>
          </div>
        )}

        {summary && (
          <p data-testid="engine-summary" className="mt-3 text-sm text-foreground">
            {summaryLine(summary)}
          </p>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold">调优与标定</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            调优引擎
            <select
              data-testid="tune-engine-select"
              value={tuneEngine}
              onChange={(e) => {
                setTuneEngine(e.target.value as TunableEngine);
                setCalibration(null);
                setPatterns(null);
              }}
              className={SELECT}
            >
              {TUNABLE_ENGINES.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            优化条数
            <input
              type="number"
              min={1}
              max={100}
              data-testid="optimize-limit"
              value={optimizeLimit}
              onChange={(e) => setOptimizeLimit(Number(e.target.value) || 1)}
              className={`${SELECT} w-24`}
            />
          </label>
          <button
            type="button"
            data-testid="auto-tune-button"
            disabled={busy}
            onClick={() =>
              void run("tune", () => engineApi.autoTune(tuneEngine), (accepted) =>
                setTask({
                  task_id: accepted.task_id,
                  engine: tuneEngine,
                  status: accepted.status === "accepted" ? "running" : accepted.status,
                  message: accepted.message,
                }),
              )
            }
            className={`${BTN} border border-border text-foreground hover:bg-muted`}
          >
            {pending === "tune" ? "启动中…" : "启动自动调优"}
          </button>
          <button
            type="button"
            data-testid="batch-optimize-button"
            disabled={busy}
            onClick={() =>
              void run(
                "optimize",
                () => engineApi.batchOptimize(tuneEngine, optimizeLimit),
                setSummary,
              )
            }
            className={`${BTN} border border-border text-foreground hover:bg-muted`}
          >
            {pending === "optimize" ? "优化中…" : "批量 AI 优化"}
          </button>
          <button
            type="button"
            data-testid="calibration-button"
            disabled={busy}
            onClick={() =>
              void run("calibration", () => engineApi.calibration(tuneEngine), setCalibration)
            }
            className={`${BTN} border border-border text-foreground hover:bg-muted`}
          >
            查看标定参数
          </button>
          <button
            type="button"
            data-testid="patterns-button"
            disabled={busy}
            onClick={() =>
              void run("patterns", () => engineApi.calibrationPatterns(tuneEngine), setPatterns)
            }
            className={`${BTN} border border-border text-foreground hover:bg-muted`}
          >
            分析优化模式
          </button>
        </div>

        {task && (
          <div data-testid="auto-tune-task" className="mt-3 rounded-md border border-border p-3 text-sm">
            <p className="text-foreground">
              任务 {task.task_id ?? "-"} · 状态 {task.status ?? "-"}
              {task.stage ? ` · 阶段 ${task.stage}` : ""}
              {typeof task.progress === "number" ? ` · ${Math.round(task.progress * 100)}%` : ""}
            </p>
            {(task.message || task.error) && (
              <p className={`mt-1 text-xs ${task.error ? "text-neg" : "text-muted-foreground"}`}>
                {task.error ?? task.message}
              </p>
            )}
          </div>
        )}

        {calibration && (
          <div data-testid="calibration-result" className="mt-3 text-sm">
            {calibration.status === "ok" && calibration.calibration ? (
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs text-foreground">
                {JSON.stringify(calibration.calibration, null, 2)}
              </pre>
            ) : (
              <p className="text-muted-foreground">
                {calibration.message ?? "该引擎暂无启用中的标定参数。"}
              </p>
            )}
          </div>
        )}

        {patterns && (
          <div data-testid="patterns-result" className="mt-3 text-sm">
            {patterns.patterns || patterns.suggestions ? (
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs text-foreground">
                {JSON.stringify(patterns.suggestions ?? patterns.patterns, null, 2)}
              </pre>
            ) : (
              <p className="text-muted-foreground">
                {patterns.message ?? "样本不足，暂无可用的优化模式。"}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
