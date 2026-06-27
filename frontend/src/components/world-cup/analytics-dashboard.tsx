"use client";

import { useEffect, useState } from "react";
import { BarChart3, Target, Database, Activity, TrendingUp, AlertCircle, Gauge, RefreshCw, ShieldCheck, ClipboardCheck } from "lucide-react";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { cn } from "@/lib/utils";
import { analyticsApi } from "@/lib/analytics-api";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";

interface EngineStats {
  total_predictions: number;
  by_engine: Record<EngineKey, EngineUsage>;
}

type EngineKey = "elo_odds" | "hybrid" | "integrated";

interface EngineUsage {
  count: number;
  percentage: number;
  avg_confidence: number;
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

interface CalibrationBucket {
  label: string;
  count: number;
  avg_confidence: number | null;
  accuracy: number | null;
  gap: number | null;
  is_usable: boolean;
}

interface QualitySummary {
  samples: number;
  outcome_accuracy: number | null;
  exact_score_rate: number | null;
  avg_score_mae: number | null;
  avg_brier_score: number | null;
  avg_log_loss: number | null;
  avg_confidence: number | null;
  confidence_bias: number | null;
  expected_calibration_error: number | null;
  is_calibratable: boolean;
  calibration_buckets: CalibrationBucket[];
}

interface QualityTrendPoint {
  date: string;
  samples: number;
  outcome_accuracy: number | null;
  avg_brier_score: number | null;
  avg_log_loss: number | null;
  expected_calibration_error: number | null;
}

interface ConsistencyIssue {
  type: string;
  severity: "warn" | "error" | string;
  match_id: string;
  engine: EngineKey | string;
  timestamp: string;
  rows: number;
  variant_count: number;
  variants: Array<{
    predicted_score: { home: number; away: number };
    count: number;
    history_ids?: number[];
    triggers: string[];
    methods: string[];
  }>;
  has_unknown_method?: boolean;
  message: string;
}

interface QualityLoopReport {
  status: string;
  sample_policy: string;
  counters: {
    finished_matches: number;
    matches_without_history: number;
    history_rows_excluded_after_kickoff: number;
    history_rows_excluded_comparison: number;
  };
  overall: QualitySummary;
  by_engine: Record<EngineKey, QualitySummary>;
  trends?: {
    overall: QualityTrendPoint[];
    by_engine: Record<EngineKey, QualityTrendPoint[]>;
  };
  consistency_issues?: ConsistencyIssue[];
  integrated_weight_suggestion: {
    elo_weight: number;
    hybrid_weight: number;
    source: "rule_default" | "historical_brier";
    reason?: string;
    samples: Record<"elo_odds" | "hybrid", number>;
    brier: Record<"elo_odds" | "hybrid", number | null>;
    learned_elo_weight?: number;
    blend?: number;
  };
  recommendations: Array<{
    level: "ok" | "info" | "warn";
    title: string;
    message: string;
  }>;
}

interface ConsistencyRepairItem {
  match_id: string;
  engine: string;
  timestamp: string;
  history_ids: number[];
  methods: string[];
  variant_count: number;
  can_autofix: boolean;
  recommended_action: string;
  rationale: string;
}

interface ConsistencyRepairPlan {
  status: string;
  dry_run: boolean;
  issue_count: number;
  auto_fixable: number;
  manual_review: number;
  items: ConsistencyRepairItem[];
}

interface ConsistencyRepairPreviewItem {
  history_id: number;
  status: string;
  match_id?: string;
  timestamp?: string | null;
  predicted_score?: { home: number; away: number };
  current_method?: string | null;
  inferred_method?: string | null;
  can_apply: boolean;
  reason: string;
  source_history_ids?: number[];
  action?: string;
  applied_method?: string | null;
}

interface ConsistencyRepairPreview {
  status: string;
  dry_run: boolean;
  requested: number;
  inferable: number;
  manual_review: number;
  items: ConsistencyRepairPreviewItem[];
}

interface ConsistencyRepairApplyResult extends ConsistencyRepairPreview {
  confirm: boolean;
  protected?: boolean;
  updated: number;
  skipped: number;
  run_id?: string;
}

interface PostMatchBackfillResult {
  status: string;
  run_id?: string;
  dry_run: boolean;
  source: string;
  candidate_count: number;
  candidates: Array<{
    match_id: string;
    home_team: string;
    away_team: string;
    kickoff_utc: string | null;
    actual_score: { home: number | null; away: number | null };
  }>;
  scoring: {
    scored: number;
    skipped: number;
    errors: number;
  };
  quality?: {
    samples: number;
    outcome_accuracy: number | null;
    avg_brier_score: number | null;
    expected_calibration_error: number | null;
    trend_days: number;
    consistency_issues: number;
  };
  error?: string;
}

interface PostMatchBackfillRun {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  dry_run: boolean | null;
  source: string | null;
  sync_status: string | null;
  candidate_count: number;
  scored: number;
  skipped: number;
  errors: number;
  quality_samples: number | null;
  audit_metadata?: AuditMetadata | null;
}

interface PostMatchBackfillRunsResponse {
  status: string;
  job_name: string;
  count: number;
  runs: PostMatchBackfillRun[];
}

interface ResultConsistencyScore {
  home: number;
  away: number;
}

interface ResultConsistencyFactSnapshot {
  fact_id: string;
  source: string;
  observed_at: string;
  status: string;
  score: ResultConsistencyScore | null;
}

interface ResultConsistencyFixtureSnapshot {
  match_id: string;
  fixture_id: string;
  home_team: string;
  away_team: string;
  status: string;
  score: ResultConsistencyScore | null;
  updated_at: string | null;
}

interface ResultConsistencyIssue {
  type: string;
  severity: "warn" | "error" | string;
  match_id: string;
  fact: ResultConsistencyFactSnapshot | null;
  fixture: ResultConsistencyFixtureSnapshot | null;
  message: string;
}

interface ResultConsistencyFactStore {
  configured_path: string;
  exists: boolean;
  count: number;
  by_kind: Record<string, number>;
  updated_at?: string;
  file_mtime?: string | null;
}

interface ResultConsistencyReport {
  status: string;
  dry_run: boolean;
  generated_at: string;
  source: string;
  fact_store: ResultConsistencyFactStore | null;
  fact_count: number;
  fixture_count: number;
  checked: number;
  issue_count: number;
  returned_issue_count: number;
  issues: ResultConsistencyIssue[];
}

interface ResultFactBackfillItem {
  match_id: string;
  fixture_id: string;
  home_team: string;
  away_team: string;
  score: ResultConsistencyScore;
  action: string;
  fact: ResultConsistencyFactSnapshot & {
    kind: "match_result";
    tournament: string;
    match_id: string;
    home_team: string;
    away_team: string;
    confidence: number;
  };
}

interface ResultFactBackfillResult {
  status: string;
  dry_run: boolean;
  confirm: boolean;
  protected?: boolean;
  finished_fixture_count: number;
  existing_fact_matches: number;
  candidate_count: number;
  imported: number;
  skipped_existing: number;
  run_id?: string;
  items: ResultFactBackfillItem[];
}

interface ResultFactBackfillRun {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  dry_run: boolean | null;
  confirm: boolean | null;
  protected?: boolean | null;
  finished_fixture_count: number;
  existing_fact_matches: number;
  candidate_count: number;
  imported: number;
  audit_metadata?: AuditMetadata | null;
}

interface AuditMetadata {
  trigger_source?: string;
  operator?: string;
  request_path?: string;
}

interface ResultFactBackfillRunsResponse {
  status: string;
  job_name: string;
  count: number;
  runs: ResultFactBackfillRun[];
}

const ENGINES: Array<{ key: EngineKey; label: string; barClass: string }> = [
  { key: "elo_odds", label: "Elo+赔率", barClass: "bg-amber-500" },
  { key: "hybrid", label: "混合引擎", barClass: "bg-purple-500" },
  { key: "integrated", label: "集成引擎", barClass: "bg-blue-500" },
];

function engineLabel(engine: string): string {
  if (engine === "unknown") return "未知方法";
  return ENGINES.find((item) => item.key === engine)?.label ?? engine;
}

function methodLabel(method: string): string {
  return method === "unknown" ? "未知方法" : method;
}

function consistencyIssueMessage(issue: ConsistencyIssue): string {
  if (issue.engine === "unknown") {
    return "缺失方法的历史行同一秒出现不同预测比分。";
  }
  return issue.message;
}

function percent(value: number | null | undefined): string {
  if (value == null) return "--";
  return `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number | null | undefined, digits = 3): string {
  if (value == null) return "--";
  return value.toFixed(digits);
}

function shortDate(value: string): string {
  return value.slice(5).replace("-", "/");
}

function biasTone(value: number | null | undefined): string {
  if (value == null) return "text-muted-foreground";
  if (Math.abs(value) <= 0.08) return "text-pos";
  if (Math.abs(value) <= 0.15) return "text-warn";
  return "text-neg";
}

function buildTrendChartData(points: QualityTrendPoint[]) {
  return points.map((point) => ({
    date: shortDate(point.date),
    samples: point.samples,
    accuracyPct: point.outcome_accuracy == null ? null : point.outcome_accuracy * 100,
    ecePct: point.expected_calibration_error == null ? null : point.expected_calibration_error * 100,
    brier: point.avg_brier_score,
    logLoss: point.avg_log_loss,
  }));
}

function scoreLabel(score: { home: number; away: number }): string {
  return `${score.home}-${score.away}`;
}

function backfillRunMode(run: PostMatchBackfillRun): string {
  if (run.dry_run === true) return "检查";
  if (run.dry_run === false) return "写入";
  return "未知";
}

function backfillRunStatus(run: PostMatchBackfillRun): string {
  if (run.status === "success") return "成功";
  if (run.status === "failed") return "失败";
  return "运行中";
}

function backfillRunTone(run: PostMatchBackfillRun): string {
  if (run.status === "success") return "text-pos";
  if (run.status === "failed") return "text-neg";
  return "text-muted-foreground";
}

function formatDurationMs(value: number | null | undefined): string {
  if (value == null) return "--";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}

function auditMetadataLabel(metadata: AuditMetadata | null | undefined): string {
  const source = metadata?.trigger_source;
  const operator = metadata?.operator;
  if (source && operator) return `${source} / ${operator}`;
  return source || operator || "unknown";
}

function compactDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  return value.slice(5, 16).replace("T", " ");
}

function resultScoreLabel(score: ResultConsistencyScore | null | undefined): string {
  if (!score) return "--";
  return `${score.home}-${score.away}`;
}

function resultIssueTone(issue: ResultConsistencyIssue): string {
  return issue.severity === "error" ? "text-neg" : "text-warn";
}

function resultIssueBadgeClass(issue: ResultConsistencyIssue): string {
  return issue.severity === "error"
    ? "border-neg/40 bg-neg/10 text-neg"
    : "border-warn/40 bg-warn/10 text-warn";
}

function resultIssueTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    fixture_missing_in_prediction_db: "Fixture missing",
    result_fact_missing_for_finished_fixture: "Fact missing",
    status_mismatch: "Status mismatch",
    score_mismatch: "Score mismatch",
  };
  return labels[type] ?? type.split("_").join(" ");
}

function QualityTrendCharts({ points }: { points: QualityTrendPoint[] }) {
  const data = buildTrendChartData(points);

  return (
    <div className="mt-4 rounded-md border bg-background p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-medium text-muted-foreground">历史趋势</div>
        <div className="font-mono text-xs text-muted-foreground tabular-nums">{data.length} 天</div>
      </div>

      {data.length === 0 ? (
        <div className="mt-3 rounded-md bg-secondary/40 px-3 py-6 text-center text-xs text-muted-foreground">
          暂无趋势样本
        </div>
      ) : (
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-2 flex items-center gap-4 text-[11px] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <span className="h-0.5 w-4 bg-chart-3" />
                命中率
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="h-0.5 w-4 bg-chart-4" />
                ECE
              </span>
            </div>
            <ChartFrame height={180}>
              <LineChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--border)" />
                <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={12} />
                <YAxis
                  width={36}
                  domain={[0, 100]}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => `${Number(value).toFixed(0)}%`}
                />
                <DarkTooltip
                  formatter={(value, name, payload) => (
                    <span className="font-mono">
                      {String(name)} {Number(value).toFixed(1)}%
                      {typeof payload.samples === "number" ? ` · n=${payload.samples}` : ""}
                    </span>
                  )}
                />
                <Line
                  name="命中率"
                  dataKey="accuracyPct"
                  type="monotone"
                  stroke="var(--chart-3)"
                  strokeWidth={2}
                  dot={data.length <= 12}
                  connectNulls
                />
                <Line
                  name="ECE"
                  dataKey="ecePct"
                  type="monotone"
                  stroke="var(--chart-4)"
                  strokeWidth={2}
                  dot={data.length <= 12}
                  connectNulls
                />
              </LineChart>
            </ChartFrame>
          </div>

          <div>
            <div className="mb-2 flex items-center gap-4 text-[11px] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <span className="h-0.5 w-4 bg-chart-1" />
                Brier
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="h-0.5 w-4 bg-chart-2" />
                LogLoss
              </span>
            </div>
            <ChartFrame height={180}>
              <LineChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--border)" />
                <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={12} />
                <YAxis
                  width={42}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => Number(value).toFixed(1)}
                />
                <DarkTooltip
                  formatter={(value, name, payload) => (
                    <span className="font-mono">
                      {String(name)} {Number(value).toFixed(4)}
                      {typeof payload.samples === "number" ? ` · n=${payload.samples}` : ""}
                    </span>
                  )}
                />
                <Line
                  name="Brier"
                  dataKey="brier"
                  type="monotone"
                  stroke="var(--chart-1)"
                  strokeWidth={2}
                  dot={data.length <= 12}
                  connectNulls
                />
                <Line
                  name="LogLoss"
                  dataKey="logLoss"
                  type="monotone"
                  stroke="var(--chart-2)"
                  strokeWidth={2}
                  dot={data.length <= 12}
                  connectNulls
                />
              </LineChart>
            </ChartFrame>
          </div>
        </div>
      )}
    </div>
  );
}

function ConsistencyIssuesPanel({
  issues,
  repairPlan,
  repairPreview,
  onQualityRefresh,
}: {
  issues: ConsistencyIssue[];
  repairPlan: ConsistencyRepairPlan | null;
  repairPreview: ConsistencyRepairPreview | null;
  onQualityRefresh: (quality: QualityLoopReport) => void;
}) {
  const [repairResult, setRepairResult] = useState<ConsistencyRepairApplyResult | null>(null);
  const [repairRunning, setRepairRunning] = useState<"dry" | "write" | null>(null);
  const [repairConfirmed, setRepairConfirmed] = useState(false);
  const [repairError, setRepairError] = useState<string | null>(null);
  const repairIds = Array.from(new Set(
    repairPreview?.items.map((item) => item.history_id)
      ?? repairPlan?.items?.[0]?.history_ids
      ?? []
  ));
  const repairInferable = repairResult?.inferable ?? repairPreview?.inferable ?? 0;
  const canCheckRepair = repairIds.length > 0 && repairRunning == null;
  const canApplyRepair = canCheckRepair && repairConfirmed && repairInferable > 0;

  async function runConsistencyRepair(dryRun: boolean) {
    if (repairIds.length === 0) return;
    setRepairRunning(dryRun ? "dry" : "write");
    setRepairError(null);
    try {
      const data = await analyticsApi.runConsistencyRepair<ConsistencyRepairApplyResult>(
        repairIds,
        dryRun,
        !dryRun && repairConfirmed,
      );
      setRepairResult(data);

      if (!dryRun && data.status === "ok" && data.updated > 0) {
        try {
          onQualityRefresh(await analyticsApi.qualityLoop<QualityLoopReport>());
        } catch {
          // Best-effort refresh; the write itself already succeeded.
        }
        setRepairConfirmed(false);
      }
    } catch (err) {
      setRepairError(err instanceof Error ? err.message : String(err));
    } finally {
      setRepairRunning(null);
    }
  }

  if (issues.length === 0) return null;

  return (
    <div className="mt-4 rounded-md border border-warn/40 bg-warn/10 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-medium text-warn">
          <AlertCircle className="size-3.5" />
          <span>数据一致性问题</span>
        </div>
        <div className="font-mono text-xs text-warn tabular-nums">{issues.length} 项</div>
      </div>
      <div className="mt-3 space-y-2">
        {issues.slice(0, 5).map((issue) => (
          <div key={`${issue.match_id}-${issue.engine}-${issue.timestamp}`} className="rounded-md bg-background/70 px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium text-foreground">
                {issue.match_id} · {engineLabel(issue.engine)}
              </span>
              <span className="font-mono text-muted-foreground tabular-nums">{issue.timestamp}</span>
            </div>
            <div className="mt-1 text-muted-foreground">{consistencyIssueMessage(issue)}</div>
            <div className="mt-1 font-mono text-warn tabular-nums">
              {issue.variants.map((variant) => scoreLabel(variant.predicted_score)).join(" / ")}
            </div>
            <div className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
              {issue.variants.map((variant) => (
                <div key={`${variant.predicted_score.home}-${variant.predicted_score.away}`}>
                  <span className="font-mono tabular-nums">{scoreLabel(variant.predicted_score)}</span>
                  <span className="mx-1">·</span>
                  <span>方法 {(variant.methods.length ? variant.methods : ["unknown"]).map(methodLabel).join(", ")}</span>
                  <span className="mx-1">·</span>
                  <span>触发 {(variant.triggers.length ? variant.triggers : ["unknown"]).join(", ")}</span>
                  {variant.history_ids && variant.history_ids.length > 0 && (
                    <>
                      <span className="mx-1">·</span>
                      <span>行 {variant.history_ids.join(", ")}</span>
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {repairPlan && (
        <div className="mt-3 rounded-md bg-background/70 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-warn">修复建议</span>
            <span className="font-mono text-muted-foreground tabular-nums">
              自动 {repairPlan.auto_fixable} / 人工 {repairPlan.manual_review}
            </span>
          </div>
          {repairPlan.items[0] && (
            <div className="mt-1 text-muted-foreground">
              <span>{repairPlan.items[0].rationale}</span>
              {repairPlan.items[0].history_ids.length > 0 && (
                <span className="ml-1 font-mono tabular-nums">
                  行 {repairPlan.items[0].history_ids.join(", ")}
                </span>
              )}
            </div>
          )}
          {repairPreview && (
            <div className="mt-2 rounded-md bg-secondary/40 px-3 py-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-muted-foreground">预览</span>
                <span className="font-mono text-muted-foreground tabular-nums">
                  可推断 {repairPreview.inferable} / {repairPreview.requested}
                </span>
              </div>
              <div className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
                {repairPreview.items.slice(0, 3).map((item) => (
                  <div key={item.history_id} className="flex flex-wrap items-center gap-x-1">
                    <span className="font-mono tabular-nums">行 {item.history_id}</span>
                    <span>→</span>
                    <span className={cn(item.can_apply ? "text-foreground" : "text-warn")}>
                      {item.inferred_method ?? item.reason}
                    </span>
                    {item.source_history_ids && item.source_history_ids.length > 0 && (
                      <span className="font-mono tabular-nums">
                        来源 {item.source_history_ids.join(", ")}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => runConsistencyRepair(true)}
              disabled={!canCheckRepair}
              className="inline-flex items-center gap-1.5 rounded-md border bg-secondary px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary/80 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={cn("size-3.5", repairRunning === "dry" && "animate-spin")} />
              Repair dry-run
            </button>
            <label className="inline-flex items-center gap-1.5 rounded-md border bg-secondary/50 px-2.5 py-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={repairConfirmed}
                onChange={(event) => setRepairConfirmed(event.currentTarget.checked)}
                className="size-3.5 accent-current"
              />
              Confirm method write
            </label>
            <button
              type="button"
              onClick={() => runConsistencyRepair(false)}
              disabled={!canApplyRepair}
              className="inline-flex items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs font-medium text-warn transition-colors hover:bg-warn/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ShieldCheck className={cn("size-3.5", repairRunning === "write" && "animate-pulse")} />
              Apply repair
            </button>
          </div>
          {repairError && (
            <div className="mt-2 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
              Repair error: {repairError}
            </div>
          )}
          {repairResult && (
            <div className="mt-2 rounded-md bg-secondary/40 px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-muted-foreground">Repair result</span>
                {repairResult.run_id && (
                  <span className="font-mono text-muted-foreground tabular-nums">{repairResult.run_id}</span>
                )}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground tabular-nums">
                <span>requested {repairResult.requested}</span>
                <span>inferable {repairResult.inferable}</span>
                <span>updated {repairResult.updated}</span>
                <span>skipped {repairResult.skipped}</span>
                <span>manual {repairResult.manual_review}</span>
              </div>
              {repairResult.protected && (
                <div className="mt-1 text-[11px] text-warn">confirmation required</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PostMatchBackfillPanel({ onQualityRefresh }: { onQualityRefresh: (quality: QualityLoopReport) => void }) {
  const [result, setResult] = useState<PostMatchBackfillResult | null>(null);
  const [auditRuns, setAuditRuns] = useState<PostMatchBackfillRun[]>([]);
  const [isRunning, setIsRunning] = useState<"dry" | "write" | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);

  async function loadAuditRuns() {
    try {
      const data = await analyticsApi.postMatchBackfillRuns<PostMatchBackfillRunsResponse>(5);
      setAuditRuns(data.runs ?? []);
      setRunsError(null);
    } catch (err) {
      setRunsError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    void loadAuditRuns();
  }, []);

  async function runBackfill(dryRun: boolean) {
    setIsRunning(dryRun ? "dry" : "write");
    setError(null);
    try {
      const data = await analyticsApi.runPostMatchBackfill<PostMatchBackfillResult>(dryRun);
      setResult(data);
      await loadAuditRuns();

      if (!dryRun && data.status === "ok") {
        try {
          onQualityRefresh(await analyticsApi.qualityLoop<QualityLoopReport>());
        } catch {
          // Best-effort refresh; the backfill itself already succeeded.
        }
        setConfirmed(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsRunning(null);
    }
  }

  const canWrite = confirmed && isRunning == null;

  return (
    <div className="mt-4 rounded-md border bg-background p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <RefreshCw className="size-3.5 text-primary" />
          <div className="text-xs font-medium text-muted-foreground">赛后回填</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => runBackfill(true)}
            disabled={isRunning != null}
            className="inline-flex items-center gap-1.5 rounded-md border bg-secondary px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary/80 disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw className={cn("size-3.5", isRunning === "dry" && "animate-spin")} />
            检查回填
          </button>
          <label className="inline-flex items-center gap-1.5 rounded-md border bg-secondary/50 px-2.5 py-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.currentTarget.checked)}
              className="size-3.5 accent-current"
            />
            确认写入评分
          </label>
          <button
            type="button"
            onClick={() => runBackfill(false)}
            disabled={!canWrite}
            className="inline-flex items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs font-medium text-warn transition-colors hover:bg-warn/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ShieldCheck className={cn("size-3.5", isRunning === "write" && "animate-pulse")} />
            执行回填
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-3 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-3 space-y-2">
          <div className="grid gap-2 text-xs md:grid-cols-5">
            <div className="rounded-md bg-secondary/40 px-3 py-2">
              <div className="text-muted-foreground">候选</div>
              <div className="font-mono text-base font-semibold tabular-nums">{result.candidate_count}</div>
            </div>
            <div className="rounded-md bg-secondary/40 px-3 py-2">
              <div className="text-muted-foreground">已评分</div>
              <div className="font-mono text-base font-semibold tabular-nums">{result.scoring.scored}</div>
            </div>
            <div className="rounded-md bg-secondary/40 px-3 py-2">
              <div className="text-muted-foreground">跳过</div>
              <div className="font-mono text-base font-semibold tabular-nums">{result.scoring.skipped}</div>
            </div>
            <div className="rounded-md bg-secondary/40 px-3 py-2">
              <div className="text-muted-foreground">错误</div>
              <div className="font-mono text-base font-semibold tabular-nums">{result.scoring.errors}</div>
            </div>
            <div className="rounded-md bg-secondary/40 px-3 py-2">
              <div className="text-muted-foreground">质量样本</div>
              <div className="font-mono text-base font-semibold tabular-nums">{result.quality?.samples ?? "--"}</div>
            </div>
          </div>
          {result.run_id && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>审计编号</span>
              <span className="break-all font-mono tabular-nums">{result.run_id}</span>
            </div>
          )}
          {result.candidates.length > 0 && (
            <div className="space-y-1">
              {result.candidates.slice(0, 5).map((candidate) => (
                <div key={candidate.match_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-secondary/30 px-3 py-2 text-xs">
                  <span className="font-medium">{candidate.home_team} vs {candidate.away_team}</span>
                  <span className="font-mono text-muted-foreground tabular-nums">
                    {candidate.actual_score.home}-{candidate.actual_score.away}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="mt-3 rounded-md bg-secondary/20 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="font-medium text-muted-foreground">最近审计</span>
          <span className="font-mono text-muted-foreground tabular-nums">{auditRuns.length} 条</span>
        </div>
        {runsError ? (
          <div className="mt-2 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
            {runsError}
          </div>
        ) : auditRuns.length === 0 ? (
          <div className="mt-2 rounded-md bg-background/70 px-3 py-3 text-center text-xs text-muted-foreground">
            暂无审计记录
          </div>
        ) : (
          <div className="mt-2 space-y-1">
            {auditRuns.map((run) => (
              <div key={run.id} className="grid gap-2 rounded-md bg-background/70 px-3 py-2 text-xs md:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn("font-medium", backfillRunTone(run))}>{backfillRunStatus(run)}</span>
                    <span className="text-muted-foreground">{backfillRunMode(run)}</span>
                    <span className="font-mono text-muted-foreground tabular-nums">{compactDateTime(run.started_at)}</span>
                  </div>
                  <div className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{run.id}</div>
                  <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                    via {auditMetadataLabel(run.audit_metadata)}
                  </div>
                  {run.error && <div className="mt-1 break-all text-neg">{run.error}</div>}
                </div>
                <div className="grid grid-cols-2 gap-2 font-mono text-[11px] text-muted-foreground tabular-nums md:grid-cols-4 md:text-right">
                  <span>候选 {run.candidate_count}</span>
                  <span>写入 {run.scored}</span>
                  <span>跳过 {run.skipped}</span>
                  <span>{run.error ? "错误" : "耗时"} {run.error ? run.errors : formatDurationMs(run.duration_ms)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ResultConsistencyPanel({
  report,
  onResultRefresh,
}: {
  report: ResultConsistencyReport;
  onResultRefresh: (report: ResultConsistencyReport) => void;
}) {
  const [backfillResult, setBackfillResult] = useState<ResultFactBackfillResult | null>(null);
  const [backfillRunning, setBackfillRunning] = useState<"dry" | "write" | null>(null);
  const [backfillConfirmed, setBackfillConfirmed] = useState(false);
  const [backfillError, setBackfillError] = useState<string | null>(null);
  const [backfillRuns, setBackfillRuns] = useState<ResultFactBackfillRun[]>([]);
  const [backfillRunsError, setBackfillRunsError] = useState<string | null>(null);
  const hasIssues = report.issue_count > 0;
  const hasErrors = report.issues.some((issue) => issue.severity === "error");
  const issueTypeCounts = report.issues.reduce<Record<string, number>>((counts, issue) => {
    counts[issue.type] = (counts[issue.type] ?? 0) + 1;
    return counts;
  }, {});
  const canRunFactBackfill = backfillRunning == null;
  const canApplyFactBackfill = canRunFactBackfill && backfillConfirmed;

  async function loadResultFactBackfillRuns() {
    try {
      const data = await analyticsApi.resultFactBackfillRuns<ResultFactBackfillRunsResponse>(5);
      setBackfillRuns(data.runs ?? []);
      setBackfillRunsError(null);
    } catch (err) {
      setBackfillRunsError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    void loadResultFactBackfillRuns();
  }, []);

  async function runResultFactBackfill(dryRun: boolean) {
    setBackfillRunning(dryRun ? "dry" : "write");
    setBackfillError(null);
    try {
      const data = await analyticsApi.runResultFactBackfill<ResultFactBackfillResult>(
        25,
        dryRun,
        !dryRun && backfillConfirmed,
      );
      setBackfillResult(data);

      if (!dryRun && data.status === "ok") {
        await loadResultFactBackfillRuns();
        if (data.imported > 0) {
          try {
            onResultRefresh(await analyticsApi.resultConsistency<ResultConsistencyReport>(25));
          } catch {
            // Best-effort refresh; the backfill itself already succeeded.
          }
        }
        setBackfillConfirmed(false);
      }
    } catch (err) {
      setBackfillError(err instanceof Error ? err.message : String(err));
    } finally {
      setBackfillRunning(null);
    }
  }

  return (
    <div className={cn(
      "mt-4 rounded-md border bg-background p-3",
      hasErrors ? "border-neg/40" : hasIssues ? "border-warn/40" : "border-pos/40"
    )}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ClipboardCheck className={cn("size-3.5", hasIssues ? "text-warn" : "text-pos")} />
          <div className="text-xs font-medium text-muted-foreground">Result consistency</div>
        </div>
        <div className={cn(
          "rounded-md border px-2 py-1 text-xs font-medium",
          hasErrors ? "border-neg/40 bg-neg/10 text-neg" : hasIssues ? "border-warn/40 bg-warn/10 text-warn" : "border-pos/40 bg-pos/10 text-pos"
        )}>
          {hasIssues ? "Review" : "OK"}
        </div>
      </div>

      <div className="mt-3 grid gap-2 text-xs md:grid-cols-4">
        <div className="rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-muted-foreground">Facts</div>
          <div className="font-mono text-base font-semibold tabular-nums">{report.fact_count}</div>
        </div>
        <div className="rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-muted-foreground">Fixtures</div>
          <div className="font-mono text-base font-semibold tabular-nums">{report.fixture_count}</div>
        </div>
        <div className="rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-muted-foreground">Checked</div>
          <div className="font-mono text-base font-semibold tabular-nums">{report.checked}</div>
        </div>
        <div className="rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-muted-foreground">Issues</div>
          <div className={cn("font-mono text-base font-semibold tabular-nums", hasIssues ? "text-warn" : "text-pos")}>
            {report.issue_count}
          </div>
        </div>
      </div>

      {report.fact_store && (
        <div className="mt-3 rounded-md bg-secondary/20 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-muted-foreground">Fact store</span>
            <span className={cn("font-medium", report.fact_store.exists ? "text-pos" : "text-warn")}>
              {report.fact_store.exists ? "Present" : "Missing fact file"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground tabular-nums">
            <span>source {report.source}</span>
            <span>stored {report.fact_store.count}</span>
            <span>match_result {report.fact_store.by_kind?.match_result ?? 0}</span>
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
            {report.fact_store.configured_path}
          </div>
        </div>
      )}

      <div className="mt-3 rounded-md bg-secondary/20 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs font-medium text-muted-foreground">Result fact backfill</div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => runResultFactBackfill(true)}
              disabled={!canRunFactBackfill}
              className="inline-flex items-center gap-1.5 rounded-md border bg-secondary px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary/80 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw className={cn("size-3.5", backfillRunning === "dry" && "animate-spin")} />
              Fact dry-run
            </button>
            <label className="inline-flex items-center gap-1.5 rounded-md border bg-secondary/50 px-2.5 py-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={backfillConfirmed}
                onChange={(event) => setBackfillConfirmed(event.currentTarget.checked)}
                className="size-3.5 accent-current"
              />
              Confirm fact write
            </label>
            <button
              type="button"
              onClick={() => runResultFactBackfill(false)}
              disabled={!canApplyFactBackfill}
              className="inline-flex items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs font-medium text-warn transition-colors hover:bg-warn/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ShieldCheck className={cn("size-3.5", backfillRunning === "write" && "animate-pulse")} />
              Import facts
            </button>
          </div>
        </div>
        {backfillError && (
          <div className="mt-2 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
            Fact backfill error: {backfillError}
          </div>
        )}
        {backfillResult && (
          <div className="mt-2 rounded-md bg-background/70 px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium text-muted-foreground">Fact backfill result</span>
              {backfillResult.run_id && (
                <span className="break-all font-mono text-muted-foreground tabular-nums">{backfillResult.run_id}</span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground tabular-nums">
              <span>finished {backfillResult.finished_fixture_count}</span>
              <span>existing {backfillResult.existing_fact_matches}</span>
              <span>candidates {backfillResult.candidate_count}</span>
              <span>imported {backfillResult.imported}</span>
            </div>
            {backfillResult.protected && (
              <div className="mt-1 text-[11px] text-warn">confirmation required</div>
            )}
            {backfillResult.items.length > 0 && (
              <div className="mt-2 space-y-1">
                {backfillResult.items.slice(0, 3).map((item) => (
                  <div key={item.match_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-secondary/30 px-2 py-1.5 text-[11px]">
                    <span className="font-medium">{item.home_team} vs {item.away_team}</span>
                    <span className="font-mono text-muted-foreground tabular-nums">
                      {resultScoreLabel(item.score)} · {item.action}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="mt-2 rounded-md bg-background/70 px-3 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-medium text-muted-foreground">Recent fact imports</span>
            <span className="font-mono text-muted-foreground tabular-nums">{backfillRuns.length} runs</span>
          </div>
          {backfillRunsError ? (
            <div className="mt-2 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
              {backfillRunsError}
            </div>
          ) : backfillRuns.length === 0 ? (
            <div className="mt-2 rounded-md bg-secondary/30 px-3 py-3 text-center text-xs text-muted-foreground">
              No confirmed fact imports
            </div>
          ) : (
            <div className="mt-2 space-y-1">
              {backfillRuns.map((run) => (
                <div key={run.id} className="grid gap-2 rounded-md bg-secondary/30 px-3 py-2 text-xs md:grid-cols-[minmax(0,1fr)_auto]">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cn("font-medium", run.status === "success" ? "text-pos" : "text-neg")}>
                        {run.status}
                      </span>
                      <span className="font-mono text-muted-foreground tabular-nums">{compactDateTime(run.started_at)}</span>
                    </div>
                    <div className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{run.id}</div>
                    <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                      via {auditMetadataLabel(run.audit_metadata)}
                    </div>
                    {run.error && <div className="mt-1 break-all text-neg">{run.error}</div>}
                  </div>
                  <div className="grid grid-cols-3 gap-2 font-mono text-[11px] text-muted-foreground tabular-nums md:text-right">
                    <span>candidates {run.candidate_count}</span>
                    <span>imported {run.imported}</span>
                    <span>{run.error ? "errors" : "duration"} {run.error ? "1" : formatDurationMs(run.duration_ms)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {hasIssues ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2">
            {Object.entries(issueTypeCounts).map(([type, count]) => (
              <span key={type} className="rounded-md bg-secondary/50 px-2 py-1 text-[11px] text-muted-foreground">
                {resultIssueTypeLabel(type)} <span className="font-mono tabular-nums">{count}</span>
              </span>
            ))}
          </div>
          <div className="space-y-1">
            {report.issues.slice(0, 5).map((issue, index) => (
              <div key={`${issue.type}-${issue.match_id}-${index}`} className="rounded-md bg-secondary/30 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", resultIssueBadgeClass(issue))}>
                      {resultIssueTypeLabel(issue.type)}
                    </span>
                    <span className="font-mono font-medium tabular-nums">{issue.match_id}</span>
                  </div>
                  <span className={cn("font-medium", resultIssueTone(issue))}>{issue.severity}</span>
                </div>
                <div className="mt-2 grid gap-2 text-[11px] md:grid-cols-2">
                  <div className="rounded-md bg-background/70 px-2 py-1.5">
                    <span className="text-muted-foreground">Fact</span>
                    <span className="ml-2 font-mono tabular-nums">
                      {issue.fact?.status ?? "--"} {resultScoreLabel(issue.fact?.score)}
                    </span>
                  </div>
                  <div className="rounded-md bg-background/70 px-2 py-1.5">
                    <span className="text-muted-foreground">Fixture</span>
                    <span className="ml-2 font-mono tabular-nums">
                      {issue.fixture?.status ?? "--"} {resultScoreLabel(issue.fixture?.score)}
                    </span>
                  </div>
                </div>
                <div className="mt-2 text-[11px] text-muted-foreground">{issue.message}</div>
              </div>
            ))}
          </div>
          {report.issue_count > report.issues.length && (
            <div className="font-mono text-[11px] text-muted-foreground tabular-nums">
              showing {report.issues.length} / {report.issue_count}
            </div>
          )}
        </div>
      ) : (
        <div className="mt-3 rounded-md bg-pos/10 px-3 py-2 text-xs text-pos">
          No result drift detected
        </div>
      )}
    </div>
  );
}

export function AnalyticsDashboard() {
  const [engineStats, setEngineStats] = useState<EngineStats | null>(null);
  const [accuracyStats, setAccuracyStats] = useState<AccuracyStats | null>(null);
  const [cacheStats, setCacheStats] = useState<OddsCacheStats | null>(null);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [qualityLoop, setQualityLoop] = useState<QualityLoopReport | null>(null);
  const [resultConsistency, setResultConsistency] = useState<ResultConsistencyReport | null>(null);
  const [repairPlan, setRepairPlan] = useState<ConsistencyRepairPlan | null>(null);
  const [repairPreview, setRepairPreview] = useState<ConsistencyRepairPreview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchAnalytics() {
      try {
        setIsLoading(true);
        setError(null);

        const [engine, accuracy, cache, health, quality, result, repair] = await Promise.all([
          analyticsApi.engineStats<EngineStats>(),
          analyticsApi.accuracyStats<AccuracyStats>(),
          analyticsApi.oddsCacheStats<OddsCacheStats>(),
          analyticsApi.systemHealth<SystemHealth>(),
          analyticsApi.qualityLoop<QualityLoopReport>(),
          analyticsApi.resultConsistency<ResultConsistencyReport>(25),
          analyticsApi.consistencyRepairPlan<ConsistencyRepairPlan>(25),
        ]);

        setEngineStats(engine);
        setAccuracyStats(accuracy);
        setCacheStats(cache);
        setSystemHealth(health);
        setQualityLoop(quality);
        setResultConsistency(result);
        setRepairPlan(repair);
        const previewIds = repair?.items?.[0]?.history_ids?.slice(0, 10) ?? [];
        if (previewIds.length > 0) {
          try {
            setRepairPreview(
              await analyticsApi.consistencyRepairPreview<ConsistencyRepairPreview>(previewIds),
            );
          } catch {
            setRepairPreview(null);
          }
        } else {
          setRepairPreview(null);
        }
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

      {/* Quality Calibration Loop */}
      {qualityLoop && (
        <div className="rounded-lg border bg-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
            <div className="flex items-center gap-2">
              <Gauge className="size-4 text-primary" />
              <h3 className="text-sm font-semibold">预测质量与置信度校准</h3>
            </div>
            <div className="text-xs text-muted-foreground">
              样本 {qualityLoop.overall.samples} / 完赛 {qualityLoop.counters.finished_matches}
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <div className="rounded-md bg-secondary/50 p-3">
              <div className="text-xs text-muted-foreground">胜平负准确率</div>
              <div className="mt-1 font-mono text-xl font-bold tabular-nums">
                {percent(qualityLoop.overall.outcome_accuracy)}
              </div>
            </div>
            <div className="rounded-md bg-secondary/50 p-3">
              <div className="text-xs text-muted-foreground">Brier</div>
              <div className="mt-1 font-mono text-xl font-bold tabular-nums">
                {decimal(qualityLoop.overall.avg_brier_score, 4)}
              </div>
            </div>
            <div className="rounded-md bg-secondary/50 p-3">
              <div className="text-xs text-muted-foreground">Log loss</div>
              <div className="mt-1 font-mono text-xl font-bold tabular-nums">
                {decimal(qualityLoop.overall.avg_log_loss, 4)}
              </div>
            </div>
            <div className="rounded-md bg-secondary/50 p-3">
              <div className="text-xs text-muted-foreground">ECE</div>
              <div className={cn("mt-1 font-mono text-xl font-bold tabular-nums", biasTone(qualityLoop.overall.expected_calibration_error))}>
                {decimal(qualityLoop.overall.expected_calibration_error, 3)}
              </div>
            </div>
          </div>

          <QualityTrendCharts points={qualityLoop.trends?.overall ?? []} />
          <ConsistencyIssuesPanel
            issues={qualityLoop.consistency_issues ?? []}
            repairPlan={repairPlan}
            repairPreview={repairPreview}
            onQualityRefresh={setQualityLoop}
          />
          <PostMatchBackfillPanel onQualityRefresh={setQualityLoop} />
          {resultConsistency && (
            <ResultConsistencyPanel
              report={resultConsistency}
              onResultRefresh={setResultConsistency}
            />
          )}

          <div className="mt-4 grid gap-3 lg:grid-cols-3">
            {ENGINES.map((engine) => {
              const stats = qualityLoop.by_engine[engine.key];
              return (
                <div key={engine.key} className="rounded-md border bg-background p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{engine.label}</span>
                    <span className="font-mono text-xs text-muted-foreground tabular-nums">
                      {stats.samples} 样本
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <div className="text-muted-foreground">准确率</div>
                      <div className="font-mono font-semibold tabular-nums">{percent(stats.outcome_accuracy)}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Brier</div>
                      <div className="font-mono font-semibold tabular-nums">{decimal(stats.avg_brier_score, 4)}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">平均置信度</div>
                      <div className="font-mono font-semibold tabular-nums">{percent(stats.avg_confidence)}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">置信偏差</div>
                      <div className={cn("font-mono font-semibold tabular-nums", biasTone(stats.confidence_bias))}>
                        {stats.confidence_bias == null ? "--" : `${(stats.confidence_bias * 100).toFixed(1)}pp`}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-4 rounded-md border bg-background p-3">
            <div className="mb-3 text-xs font-medium text-muted-foreground">总体置信度桶</div>
            <div className="grid gap-2 md:grid-cols-5">
              {qualityLoop.overall.calibration_buckets.map((bucket) => (
                <div key={bucket.label} className="rounded-md bg-secondary/40 p-2">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-medium">{bucket.label}</span>
                    <span className="font-mono text-muted-foreground tabular-nums">{bucket.count}</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-secondary">
                    <div
                      className="h-full bg-primary"
                      style={{ width: `${Math.max(0, Math.min(100, (bucket.accuracy ?? 0) * 100))}%` }}
                    />
                  </div>
                  <div className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
                    <div>命中 {percent(bucket.accuracy)}</div>
                    <div>置信 {percent(bucket.avg_confidence)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-4 rounded-md border bg-background p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-medium text-muted-foreground">集成引擎权重建议</div>
                <div className="mt-1 text-sm font-semibold">
                  Elo+赔率 {percent(qualityLoop.integrated_weight_suggestion.elo_weight)}
                  <span className="mx-2 text-muted-foreground">/</span>
                  混合引擎 {percent(qualityLoop.integrated_weight_suggestion.hybrid_weight)}
                </div>
              </div>
              <div className={cn(
                "rounded-md px-2 py-1 text-xs",
                qualityLoop.integrated_weight_suggestion.source === "historical_brier"
                  ? "bg-pos/10 text-pos"
                  : "bg-secondary text-muted-foreground"
              )}>
                {qualityLoop.integrated_weight_suggestion.source === "historical_brier"
                  ? "历史 Brier 调整"
                  : "规则默认"}
              </div>
            </div>
            <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
              <div className="rounded-md bg-secondary/40 px-3 py-2">
                <span className="text-muted-foreground">Elo+赔率样本/Brier</span>
                <span className="ml-2 font-mono font-medium tabular-nums">
                  {qualityLoop.integrated_weight_suggestion.samples.elo_odds} / {decimal(qualityLoop.integrated_weight_suggestion.brier.elo_odds, 4)}
                </span>
              </div>
              <div className="rounded-md bg-secondary/40 px-3 py-2">
                <span className="text-muted-foreground">混合样本/Brier</span>
                <span className="ml-2 font-mono font-medium tabular-nums">
                  {qualityLoop.integrated_weight_suggestion.samples.hybrid} / {decimal(qualityLoop.integrated_weight_suggestion.brier.hybrid, 4)}
                </span>
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-2 md:grid-cols-2">
            {qualityLoop.recommendations.map((item) => (
              <div
                key={`${item.title}-${item.message}`}
                className={cn(
                  "rounded-md border px-3 py-2 text-xs",
                  item.level === "warn" && "border-warn/40 bg-warn/10",
                  item.level === "ok" && "border-pos/40 bg-pos/10",
                  item.level === "info" && "bg-secondary/40"
                )}
              >
                <div className="font-medium">{item.title}</div>
                <div className="mt-1 text-muted-foreground">{item.message}</div>
              </div>
            ))}
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
                {ENGINES.map((engine) => {
                  const stats = engineStats.by_engine[engine.key];
                  return (
                    <div key={engine.key} className="rounded-md bg-secondary/50 p-3">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium">{engine.label}</span>
                        <span className="text-muted-foreground">{stats.count} 次</span>
                      </div>
                      <div className="mt-2 flex items-center gap-2">
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                          <div
                            className={cn("h-full transition-all", engine.barClass)}
                            style={{ width: `${stats.percentage}%` }}
                          />
                        </div>
                        <span className="font-mono text-xs tabular-nums">
                          {stats.percentage.toFixed(1)}%
                        </span>
                      </div>
                      <div className="mt-2 text-xs text-muted-foreground">
                        平均置信度: <span className="font-mono font-medium tabular-nums">
                          {(stats.avg_confidence * 100).toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  );
                })}
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
