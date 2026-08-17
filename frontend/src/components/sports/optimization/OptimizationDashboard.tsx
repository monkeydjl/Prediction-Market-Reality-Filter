"use client";
import { Fragment, useMemo, useState } from "react";
import Link from "next/link";
import {
  useOptimizationParams,
  useAppliedParams,
  useLiveEvidence,
  triggerOptimization,
  triggerIngest,
  useTaskStatus,
  applyParams,
  backfillAndSeed,
  type BackfillSeedResult,
  type LiveEvidenceReport,
} from "@/lib/sports-api";
import type { ApplyParamsResult, OptimizedParams } from "@/lib/sports-api/types";
import {
  buildWeightDiff,
  parseWeightMap,
} from "@/lib/sports-api/param-weights";
import { inputCls, selectCls } from "@/lib/ui-classes";
import { ScrollableTable } from "@/components/ui/scrollable-table";
import {
  parseOptimizationTaskResult,
  toCandidateBarPoints,
} from "@/lib/sports-api/backtest-results";
import { BacktestResultsPanel } from "./BacktestResultsPanel";
import { WeightDiffTable } from "./WeightDiffTable";

/** Phase 9 optimizer sports only (football backtest is out of scope). */
const SPORTS = ["nba", "mlb", "nhl", "all"];

function candidateWeightRows(p: OptimizedParams) {
  const after = parseWeightMap(p.factor_weights);
  return buildWeightDiff({}, after);
}

function fmtMetric(value: number | null) {
  return value == null ? "—" : value.toFixed(4);
}

/**
 * Settled live-prediction coverage, read-only. The candidate table above
 * reports *historical* holdout metrics; a strong backtest says nothing about
 * whether enough live predictions have settled for online calibration. The
 * backend keeps that threshold per sport/competition/engine, so this renders
 * the backend's own grouping rather than re-deriving a global number.
 */
function LiveEvidencePanel({
  report,
  error,
}: {
  report: LiveEvidenceReport | undefined;
  error: unknown;
}) {
  if (error) {
    return (
      <div
        data-testid="live-evidence-unavailable"
        className="rounded border border-border bg-card p-4 text-sm text-muted-foreground"
      >
        在线证据不可用：{error instanceof Error ? error.message : "接口加载失败"}
        。未取得证据时不代表学习条件已满足。
      </div>
    );
  }
  if (!report) return null;

  const ready = report.learning_ready;
  return (
    <div data-testid="live-evidence" className="space-y-3 rounded border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">在线证据（已结算实盘预测）</h3>
        <span
          data-testid="live-evidence-readiness"
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            ready ? "bg-pos/10 text-pos" : "bg-warn/10 text-warn"
          }`}
        >
          {ready ? "可评估" : "样本不足"}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        上方候选指标来自历史回测；这里统计线上已结算的预测。校准阈值按
        运动 / 赛事 / 引擎分组计算，阈值 {report.threshold}，达标分组{" "}
        {report.ready_group_count}/{report.group_count}，已结算{" "}
        {report.total_settled}/{report.total_predictions}。只读视图，不会开启学习或改动参数。
      </p>

      {report.groups.length === 0 ? (
        <div data-testid="live-evidence-empty" className="text-sm text-muted-foreground">
          暂无实盘预测记录。
        </div>
      ) : (
        <ScrollableTable aria-label="在线证据分组列表">
          <table className="w-full min-w-[44rem] border-collapse border text-sm">
            <thead>
              <tr className="bg-muted">
                <th scope="col" className="border p-2 text-left">分组</th>
                <th scope="col" className="border p-2 text-left">引擎</th>
                <th scope="col" className="border p-2 text-left">已结算/预测</th>
                <th scope="col" className="border p-2 text-left">还需</th>
                <th scope="col" className="border p-2 text-left">准确率</th>
                <th scope="col" className="border p-2 text-left">Brier</th>
                <th scope="col" className="border p-2 text-left">状态</th>
              </tr>
            </thead>
            <tbody>
              {report.groups.map((g) => (
                <tr key={`${g.sport}-${g.competition}-${g.engine}`}>
                  <td className="border p-2">
                    {g.sport} / {g.competition}
                  </td>
                  <td className="border p-2">{g.engine}</td>
                  <td className="border p-2 font-mono tabular-nums">
                    {g.settled_count}/{g.prediction_count}
                  </td>
                  <td className="border p-2 font-mono tabular-nums">
                    {g.remaining_samples}
                  </td>
                  <td className="border p-2 font-mono tabular-nums">
                    {fmtMetric(g.accuracy)}
                  </td>
                  <td className="border p-2 font-mono tabular-nums">
                    {fmtMetric(g.avg_brier_score)}
                  </td>
                  <td className="border p-2">
                    {g.readiness === "ready" ? "可评估" : "样本不足"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      )}
    </div>
  );
}

export function OptimizationDashboard() {
  const { data: params, error, isLoading } = useOptimizationParams();
  const { data: liveEvidence, error: liveEvidenceError } = useLiveEvidence();

  const [runSport, setRunSport] = useState("nba");
  const [ingestSport, setIngestSport] = useState("nba");
  const [seasonsInput, setSeasonsInput] = useState("2024,2023");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [lastApply, setLastApply] = useState<ApplyParamsResult | null>(null);
  const [backfillResult, setBackfillResult] = useState<BackfillSeedResult | null>(null);

  const { data: taskStatus } = useTaskStatus(taskId);
  // The applied set for the sport being optimized — what a new candidate would replace.
  const { data: appliedParams } = useAppliedParams(
    runSport === "all" ? null : runSport,
  );

  const lastApplyRows = useMemo(() => {
    if (!lastApply?.weight_diff?.length) return [];
    return lastApply.weight_diff.map((d) => ({
      factor: d.factor,
      before: d.before,
      after: d.after,
      delta:
        d.before != null && d.after != null
          ? d.after - d.before
          : d.after != null
            ? d.after
            : null,
    }));
  }, [lastApply]);

  // Hoisted so the memo's inferred dependency matches the declared one; with
  // `taskStatus?.result` inline the compiler infers all of `taskStatus` and
  // skips optimizing the component.
  const taskResult = taskStatus?.result;
  const taskBacktest = useMemo(() => {
    if (!taskResult) return null;
    return parseOptimizationTaskResult(taskResult);
  }, [taskResult]);

  const candidateChartPoints = useMemo(() => {
    if (!params?.length) return [];
    return toCandidateBarPoints(
      params.map((p) => ({
        id: p.id,
        sport: p.sport,
        accuracy: p.accuracy,
        brier_score: p.brier_score,
        mae: p.mae,
        score: p.score,
        status: p.status,
      })),
    );
  }, [params]);

  const candidateBacktestRows = useMemo(() => {
    if (!params?.length) return [];
    return params.map((p) => ({
      sport: `${p.sport}-${p.id}`,
      best_score: p.score,
      accuracy: p.accuracy,
      brier_score: p.brier_score,
      mae: p.mae,
      sample_count: p.sample_count,
      train_count: null as number | null,
      test_count: null as number | null,
      trials: p.trial_number,
      factor_weights: null,
      elo_params: null,
      score_formula: null,
      error: null,
      match_count: null as number | null,
      saved_candidate_id: p.id,
    }));
  }, [params]);

  const fetchError = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  const phase9Disabled =
    fetchError != null &&
    (fetchError.includes("503") ||
      fetchError.toLowerCase().includes("phase 9") ||
      fetchError.toLowerCase().includes("accuracy sprint"));

  if (isLoading) return <div data-testid="loading">加载中...</div>;

  async function handleRunOptimization() {
    setMutationError(null);
    setBusy(true);
    try {
      const result = await triggerOptimization(runSport);
      setTaskId(result.task_id);
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "运行优化失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleIngest() {
    setMutationError(null);
    setBusy(true);
    try {
      const seasons = seasonsInput
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      await triggerIngest(ingestSport, seasons);
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "数据导入失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleBackfillSeed() {
    setMutationError(null);
    setBusy(true);
    try {
      setBackfillResult(await backfillAndSeed(ingestSport));
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "回填与初始化失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleApply(paramsId: number) {
    setMutationError(null);
    setBusy(true);
    try {
      const result = await applyParams(paramsId);
      setLastApply(result);
      setExpandedId(paramsId);
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "应用参数失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="optimization-dashboard" className="space-y-6">
      <h2 className="text-xl font-bold">参数优化面板</h2>
      <p className="text-sm text-muted-foreground">
        仅支持 NBA / MLB / NHL。流程：导入历史赛季 → 运行优化 → 审核后应用参数。
        需开启{" "}
        <code className="rounded bg-muted px-1">PHASE9_ACCURACY_SPRINT_ENABLED</code>
        。应用后权重会写入 FactorRegistry；学习页「已应用权重」可复查当前生效集，见{" "}
        <Link href="/sports/learning?tab=weights" className="text-primary underline">
          学习 → 已应用权重
        </Link>
        。
      </p>

      {phase9Disabled && (
        <div
          data-testid="phase9-disabled"
          className="rounded border border-warn/40 bg-warn/10 p-3 text-warn"
        >
          Phase 9 精度冲刺未启用或接口返回 503。请在后端配置{" "}
          <code className="rounded bg-muted px-1">PHASE9_ACCURACY_SPRINT_ENABLED=true</code>
          {" "}后重试。
        </div>
      )}

      {mutationError && (
        <div
          data-testid="mutation-error"
          className="rounded border border-neg/40 bg-neg/10 p-3 text-neg"
        >
          操作错误: {mutationError}
        </div>
      )}
      {fetchError && !phase9Disabled && (
        <div
          data-testid="error"
          className="rounded border border-neg/40 bg-neg/10 p-3 text-neg"
        >
          加载错误: {fetchError}
        </div>
      )}

      {lastApply && (
        <div
          className="space-y-2 rounded border border-border bg-card p-4"
          data-testid="last-apply-diff"
        >
          <h3 className="text-sm font-semibold">
            最近一次 Apply 前后权重
            {lastApply.applied && (
              <span className="ml-2 font-normal text-xs text-muted-foreground">
                {lastApply.applied.sport} · id={lastApply.applied.id}
                {lastApply.previous_applied
                  ? `（替换 id=${lastApply.previous_applied.id}）`
                  : "（首次应用）"}
              </span>
            )}
          </h3>
          <WeightDiffTable rows={lastApplyRows} title="" testId="last-apply-weights" />
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2 rounded border p-4">
          <h3 className="font-semibold">运行优化</h3>
          <label className="block text-xs text-muted-foreground">
            运动
            <select
              data-testid="run-sport-select"
              value={runSport}
              onChange={(e) => setRunSport(e.target.value)}
              className={selectCls("mt-1 block w-full")}
              aria-label="运行优化的运动项目"
            >
              {SPORTS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            data-testid="run-optimization-button"
            onClick={handleRunOptimization}
            disabled={busy}
            aria-label="运行参数优化"
            className="rounded bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50"
          >
            运行优化
          </button>
          <p data-testid="applied-params" className="text-xs text-muted-foreground">
            {runSport === "all"
              ? "选择单一运动可查看其当前生效参数。"
              : appliedParams
                ? `当前生效：id=${appliedParams.id} · score ${appliedParams.score.toFixed(4)} · 样本 ${appliedParams.sample_count}`
                : `${runSport} 暂无生效参数，优化后需手动应用。`}
          </p>
        </div>

        <div className="space-y-2 rounded border p-4">
          <h3 className="font-semibold">数据导入</h3>
          <label className="block text-xs text-muted-foreground">
            运动
            <select
              data-testid="ingest-sport-select"
              value={ingestSport}
              onChange={(e) => setIngestSport(e.target.value)}
              className={selectCls("mt-1 block w-full")}
              aria-label="数据导入的运动项目"
            >
              {SPORTS.filter((s) => s !== "all").map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-muted-foreground">
            赛季
            <input
              data-testid="seasons-input"
              type="text"
              value={seasonsInput}
              onChange={(e) => setSeasonsInput(e.target.value)}
              placeholder="逗号分隔的赛季，如 2024,2023"
              className={inputCls("mt-1")}
              aria-label="逗号分隔的赛季列表"
            />
          </label>
          <button
            type="button"
            data-testid="ingest-button"
            onClick={handleIngest}
            disabled={busy}
            aria-label="导入历史赛季数据"
            className="rounded bg-pos px-4 py-2 text-pos-foreground disabled:opacity-50"
          >
            数据导入
          </button>
          <button
            type="button"
            data-testid="backfill-seed-button"
            onClick={handleBackfillSeed}
            disabled={busy}
            aria-label="回填比赛结果并初始化 Elo"
            className="ml-2 rounded border border-border px-4 py-2 text-sm disabled:opacity-50"
          >
            回填结果 + 初始化 Elo
          </button>
          <p className="text-xs text-muted-foreground">
            赛程已同步但结果表或 Elo 表为空时使用；该操作可重复执行。
          </p>
          {backfillResult && (
            <pre
              data-testid="backfill-seed-result"
              className="max-h-40 overflow-auto rounded bg-muted p-2 text-xs"
            >
              {JSON.stringify(backfillResult, null, 2)}
            </pre>
          )}
        </div>
      </div>

      {taskStatus && (
        <div
          data-testid="task-progress"
          className="space-y-2 rounded border p-4"
        >
          <h3 className="font-semibold">任务进度</h3>
          <div>状态: {taskStatus.status}</div>
          <div>
            进度:{" "}
            {taskStatus.total
              ? `${taskStatus.progress}/${taskStatus.total}`
              : `${taskStatus.progress}%`}
            {taskStatus.current_match
              ? ` · ${taskStatus.current_match}`
              : ""}
          </div>
          <div className="h-2 w-full rounded bg-muted">
            <div
              className="h-2 rounded bg-primary transition-all"
              style={{
                width: `${
                  taskStatus.total && taskStatus.total > 0
                    ? Math.min(
                        100,
                        (taskStatus.progress / taskStatus.total) * 100,
                      )
                    : Math.min(100, taskStatus.progress)
                }%`,
              }}
            />
          </div>
          {taskStatus.error && (
            <div className="text-neg">错误: {taskStatus.error}</div>
          )}
        </div>
      )}

      {taskBacktest && taskStatus?.status === "completed" && (
        <BacktestResultsPanel
          title="本次优化回测结果"
          rows={taskBacktest.sports}
          testId="task-backtest-results"
        />
      )}

      {candidateBacktestRows.length > 0 && (
        <BacktestResultsPanel
          title="候选参数回测指标对比"
          rows={candidateBacktestRows}
          chartPoints={candidateChartPoints}
          testId="candidates-backtest-results"
        />
      )}

      <LiveEvidencePanel report={liveEvidence} error={liveEvidenceError} />

      {!params || params.length === 0 ? (
        <div data-testid="empty">暂无优化参数</div>
      ) : (
        <div data-testid="params-table" className="space-y-4">
          <h3 className="font-semibold">参数列表</h3>
          <ScrollableTable aria-label="优化参数候选列表">
            <table className="w-full min-w-[48rem] border-collapse border text-sm">
              <thead>
                <tr className="bg-muted">
                  <th scope="col" className="border p-2 text-left">
                    Sport
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    Score
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    Accuracy
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    Brier
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    MAE
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    Samples
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    Status
                  </th>
                  <th scope="col" className="border p-2 text-left">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {params.map((p) => (
                  <Fragment key={p.id}>
                    <tr>
                      <td className="border p-2">{p.sport}</td>
                      <td className="border p-2 font-mono tabular-nums">
                        {p.score.toFixed(4)}
                      </td>
                      <td className="border p-2 font-mono tabular-nums">
                        {p.accuracy.toFixed(4)}
                      </td>
                      <td className="border p-2 font-mono tabular-nums">
                        {p.brier_score.toFixed(4)}
                      </td>
                      <td className="border p-2 font-mono tabular-nums">
                        {p.mae.toFixed(4)}
                      </td>
                      <td className="border p-2 font-mono tabular-nums">
                        {p.sample_count}
                      </td>
                      <td className="border p-2">{p.status}</td>
                      <td className="space-x-1 border p-2">
                        <button
                          type="button"
                          data-testid={`apply-button-${p.id}`}
                          onClick={() => handleApply(p.id)}
                          disabled={busy}
                          aria-label={`应用参数集 ${p.id}`}
                          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
                        >
                          应用
                        </button>
                        <button
                          type="button"
                          data-testid={`expand-weights-${p.id}`}
                          onClick={() =>
                            setExpandedId((id) => (id === p.id ? null : p.id))
                          }
                          aria-label={
                            expandedId === p.id
                              ? `收起参数 ${p.id} 权重`
                              : `展开参数 ${p.id} 权重`
                          }
                          className="rounded border px-2 py-1 text-xs"
                        >
                          {expandedId === p.id ? "收起权重" : "权重"}
                        </button>
                      </td>
                    </tr>
                    {expandedId === p.id && (
                      <tr>
                        <td colSpan={8} className="border bg-muted/30 p-3">
                          <WeightDiffTable
                            rows={candidateWeightRows(p)}
                            title={`候选权重预览（${p.status}）`}
                            testId={`preview-weights-${p.id}`}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </ScrollableTable>
        </div>
      )}
    </div>
  );
}
