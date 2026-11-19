"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, ChevronDown, RefreshCw } from "lucide-react";
import { eventsApi, type ApiHealth, type ApiOverview, type LlmDiagnostics, type LoopStatus } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const JOB_LABELS: Record<string, string> = {
  event_discover: "事件发现",
  event_auto_resolve: "自动结算",
  loop_db_maintenance: "数据库维护",
  world_cup_scoring_reconcile: "世界杯积分对账",
  translate_titles: "标题翻译",
};

const RUN_STATUS_META: Record<string, { label: string; cls: string }> = {
  success: { label: "成功", cls: "border-pos/40 bg-pos/10 text-pos" },
  failed: { label: "失败", cls: "border-neg/40 bg-neg/10 text-neg" },
  running: { label: "运行中", cls: "border-primary/40 bg-primary/10 text-primary" },
};

const API_STATUS_META: Record<string, { label: string; cls: string; degraded: boolean }> = {
  ok: { label: "正常", cls: "border-pos/40 bg-pos/10 text-pos", degraded: false },
  degraded: { label: "降级", cls: "border-neg/40 bg-neg/10 text-neg", degraded: true },
};

const LLM_TASK_LABELS: Record<string, string> = {
  default: "默认",
  probability_analysis: "概率分析",
  translation: "标题翻译",
  open_web_extraction: "网页提取",
  cross_validation: "交叉验证",
  world_cup: "世界杯",
  startup_check: "启动检查",
  embedding: "向量嵌入",
};

const LLM_ROUTE_SOURCE_LABELS: Record<string, string> = {
  task: "专用路由",
  default: "默认路由",
  indexed_openai: "编号 OpenAI",
  legacy_openai: "旧 OpenAI",
  legacy_embedding: "旧 Embedding",
  none: "未配置",
};

/**
 * The four statuses a `predictions` row can be in (audit 53.128). Read off
 * `prediction_store`'s own UPDATE statements, not guessed: open → `scored` for
 * an `act` decision, → `observed` for watch/skip, → `voided` for a non-genuine
 * resolution. The map is `status -> count`, so an unknown key renders as itself
 * rather than disappearing -- the vocabulary can grow without this panel
 * silently dropping the row.
 *
 * The scored/observed split is the one that confuses people: both mean "the
 * event resolved", but only `act` rows enter the calibration signal (V2
 * invariant "only act rows are scored"), which is why the tooltips say so.
 */
const PREDICTION_STATUS_LABELS: Record<string, { label: string; hint: string }> = {
  open: { label: "进行中", hint: "还没结算，等事件结果" },
  scored: { label: "已评分", hint: "act 决策 → 进了校准（Brier）" },
  observed: { label: "已观测", hint: "watch/skip 决策 → 记下了，但不进校准" },
  voided: { label: "已作废", hint: "非真实结算（身份冲突 / 市场作废），不算 Brier" },
};

function latestRun(status: LoopStatus) {
  const runs = Object.values(status.runs ?? {}).filter(Boolean);
  return runs.sort((a, b) => {
    const at = new Date(a?.finished_at ?? a?.started_at ?? 0).getTime();
    const bt = new Date(b?.finished_at ?? b?.started_at ?? 0).getTime();
    return bt - at;
  })[0];
}

function runEntries(status: LoopStatus) {
  return Object.entries(status.runs ?? {}).filter((entry): entry is [string, NonNullable<typeof entry[1]>] =>
    Boolean(entry[1]),
  );
}

function recentRunEntries(status: LoopStatus) {
  const recent = (status.recent_runs ?? []).filter(Boolean);
  if (recent.length > 0) return recent;
  return Object.values(status.runs ?? {})
    .filter((run): run is NonNullable<typeof run> => Boolean(run))
    .sort((a, b) => {
      const at = new Date(a.finished_at ?? a.started_at ?? 0).getTime();
      const bt = new Date(b.finished_at ?? b.started_at ?? 0).getTime();
      return bt - at;
    });
}

function jobLabel(key: string, run: NonNullable<LoopStatus["runs"]>[string]) {
  // The producer names runs by job_name only (the old hand-written type also
  // claimed `job`, a key nothing on the wire ever carried).
  const name = run?.job_name ?? key;
  return JOB_LABELS[name] ?? name;
}

function statusMeta(status: string) {
  return RUN_STATUS_META[status] ?? { label: status || "未知", cls: "border-border bg-secondary text-muted-foreground" };
}

function formatDurationMs(ms: number | null | undefined) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function runDurationMs(run: NonNullable<LoopStatus["runs"]>[string]) {
  // duration_ms is the only duration the producer writes; the old type also
  // claimed duration_seconds, a key nothing on the wire ever carried.
  return run?.duration_ms ?? null;
}

function runDuration(run: NonNullable<LoopStatus["runs"]>[string]) {
  return formatDurationMs(runDurationMs(run));
}

function formatResultValue(value: unknown, depth = 0): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("zh-CN") : String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    if (depth > 0) return `${entries.length} 项`;
    return entries
      .slice(0, 3)
      .map(([k, v]) => `${k}:${formatResultValue(v, depth + 1)}`)
      .join(", ");
  }
  return String(value);
}

function resultEntries(run: NonNullable<LoopStatus["runs"]>[string]) {
  // The wire key is `result` (_row_to_dict parses result_json); the old type
  // also claimed `details`, a key nothing on the wire ever carried -- the
  // `?? run?.details` fallback was dead code.
  const result = run?.result;
  if (!result) return [];
  return Object.entries(result)
    .filter(([, value]) => value != null && value !== "" && (!Array.isArray(value) || value.length > 0))
    .slice(0, 8);
}

function apiStatusMeta(status: string | null | undefined) {
  if (!status) return { label: "未知", cls: "border-border bg-secondary text-muted-foreground", degraded: false };
  return API_STATUS_META[status] ?? { label: status, cls: "border-border bg-secondary text-muted-foreground", degraded: false };
}

const BADGE = "rounded bg-secondary px-2 py-1";
/** A read that failed: neutral grey would read as "nothing to see" (§5). */
const BADGE_UNREAD = "rounded border border-warn/40 bg-warn/10 px-2 py-1 text-warn";
/**
 * A number that is zero because the producer never ran -- not a defect.
 *
 * `opacity-60`, not `text-muted-foreground`: the badge row itself already sets
 * that colour (line 432), so naming it here rendered the "off" badge exactly
 * like every other one. Found by looking at the served page (§53.131), not by a
 * test -- a class string is not a judgement any assertion was making.
 */
const BADGE_OFF = "rounded bg-secondary px-2 py-1 text-muted-foreground opacity-60";

type CountBadge = { text: string; cls: string; title?: string };

/**
 * 53.126: a zero is only a zero when the producer says the read worked.
 *
 * `predictions` / `pending_reviews` / `breached_reviews` are counted from stores
 * that can fail to open, and the producer wrote a plain `0` either way -- so
 * this panel showed "0" for a broken query and for an empty table alike. The
 * producer now says which zero it is (§53.116–§53.119); these badges are the
 * reader those switches were written for, and until this they had none.
 *
 * Three states, three renderings, and only an **explicit** flag changes what is
 * shown (§5: 缺省 = 未知, 不是假 0) -- a backend that predates the key still
 * renders the number, exactly like `dangling_refs` above.
 *
 * `enabled === false` outranks `available === false`: with the queue switch off
 * nothing is ever enqueued, so those zeros are correct by construction and a
 * failed read of an empty queue is not the operator's problem. The tooltip
 * keeps both facts instead of dropping one.
 */
function countBadge(
  label: string,
  value: number | undefined,
  flags: { available?: boolean; enabled?: boolean },
  title?: string,
): CountBadge {
  if (flags.enabled === false) {
    const off = "功能未启用（开关关闭），这些数字恒为 0";
    return {
      text: `${label} 未启用`,
      cls: BADGE_OFF,
      title: `${title ? `${title} · ` : ""}${off}${flags.available === false ? "；本次读取也失败了" : ""}`,
    };
  }
  if (flags.available === false) {
    return {
      text: `${label} 读取失败`,
      cls: BADGE_UNREAD,
      title: "读数不可用：生产端这次没读到，0 不是「确实没有」——看后端日志里那条警告",
    };
  }
  return { text: `${label} ${value ?? "—"}`, cls: BADGE, title };
}

function historyTone(status: string) {
  if (status === "success") return "bg-pos";
  if (status === "failed") return "bg-neg";
  if (status === "running") return "bg-primary";
  return "bg-muted-foreground";
}

function llmTaskLabel(task: string) {
  return LLM_TASK_LABELS[task] ?? task;
}

function llmRouteSourceLabel(source: string) {
  return LLM_ROUTE_SOURCE_LABELS[source] ?? source;
}

function llmRouteMeta(configured: boolean) {
  return configured
    ? { label: "已配置", cls: "border-pos/40 bg-pos/10 text-pos" }
    : { label: "未配置", cls: "border-neg/40 bg-neg/10 text-neg" };
}

/**
 * Badge for the daily LLM cost cap. "disabled" is neutral, not green: no cap is
 * a deliberate configuration, and painting it as healthy would be the same
 * mistake as the discovery panel's green badge for a source that was never
 * asked. "unknown" is a missing measurement, so it is a warning rather than a
 * pass — the operator has a cap and cannot see their spend against it.
 */
function llmCostCapMeta(status: string) {
  switch (status) {
    case "ok":
      return { label: "额度充足", cls: "border-pos/40 bg-pos/10 text-pos" };
    case "warning":
      return { label: "接近上限", cls: "border-warn/40 bg-warn/10 text-warn" };
    case "exceeded":
      return { label: "已达上限", cls: "border-neg/40 bg-neg/10 text-neg" };
    case "unknown":
      return { label: "读数不可用", cls: "border-warn/40 bg-warn/10 text-warn" };
    default:
      return { label: "未启用", cls: "border-border bg-secondary text-muted-foreground" };
  }
}

function fmtUsd(value: number | null | undefined) {
  return typeof value === "number" ? `$${value.toFixed(4)}` : "—";
}

/**
 * 53.133: read a key that the *type* says is always there but an older backend
 * may not have written. The generated types describe today's producer, not
 * every producer the client has to survive, so the check has to happen at
 * runtime -- `counts.dangling_refs ?? 0` cannot tell "absent" from "zero".
 */
function numeric(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/**
 * 53.133: three readings, not two.
 *
 * - no `counts` at all  -> `undefined` ("not asked yet" or "nothing to read")
 * - `dangling_refs`      -> the backend's own total over every watched table
 * - one or both legacy   -> their sum, for a backend that predates the total
 *
 * Anything else is `undefined`, which the badge prints as "—". The previous
 * version ended both fallbacks in `?? 0` and so printed "引用异常 0" on the
 * first frame, before the health call had answered -- the confident zero §5
 * exists to remove.
 */
function resolveDanglingRefs(counts: LoopStatus["counts"] | undefined): number | undefined {
  if (!counts) return undefined;
  const total = numeric(counts.dangling_refs);
  if (total !== undefined) return total;
  const legacyPredictions = numeric(counts.dangling_predictions);
  const legacyLinks = numeric(counts.dangling_links);
  if (legacyPredictions === undefined && legacyLinks === undefined) return undefined;
  return (legacyPredictions ?? 0) + (legacyLinks ?? 0);
}

export function SystemStatus() {
  const [status, setStatus] = useState<LoopStatus | null>(null);
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [overview, setOverview] = useState<ApiOverview | null>(null);
  const [llmDiagnostics, setLlmDiagnostics] = useState<LlmDiagnostics | null>(null);
  const [llmDiagnosticsError, setLlmDiagnosticsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthResp, overviewResp, llmDiagnosticsResp] = await Promise.all([
        eventsApi.health(),
        eventsApi.overview().catch(() => null),
        eventsApi.llmDiagnostics().catch((e) => {
          setLlmDiagnosticsError(e instanceof Error ? e.message : "LLM 诊断加载失败");
          return null;
        }),
      ]);
      setHealth(healthResp);
      setOverview(overviewResp);
      setLlmDiagnostics(llmDiagnosticsResp);
      if (llmDiagnosticsResp) setLlmDiagnosticsError(null);
      setStatus(healthResp.loop);
    } catch (e) {
      setError(e instanceof Error ? e.message : "状态加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const run = status ? latestRun(status) : null;
  const runs = status ? runEntries(status) : [];
  const recentRuns = status ? recentRunEntries(status).slice(0, 12) : [];
  const maxDuration = Math.max(1, ...recentRuns.map((r) => runDurationMs(r) ?? 0));
  const failed = runs.some(([, r]) => r.status === "failed");
  const running = status?.scheduler?.running;
  const apiMeta = apiStatusMeta(health?.status);
  const llmTaskCount = llmDiagnostics?.tasks.length ?? 0;
  const llmConfiguredTaskCount = llmDiagnostics?.configured_task_count ?? 0;
  const llmUnconfigured = (llmDiagnostics?.unconfigured_task_count ?? 0) > 0;
  // Optional so an older backend (no cost_cap key) renders exactly as before
  // rather than showing a card full of em dashes.
  const llmCostCap = llmDiagnostics?.cost_cap;
  const degraded = apiMeta.degraded || failed || running === false || llmUnconfigured;
  const failedWithoutDetails = runs.some(([, r]) => r.status === "failed" && !r.error);
  const endpointCount = overview?.endpoints ? Object.keys(overview.endpoints).length : null;
  // E2: this used to sum `dangling_predictions + dangling_links`, which covered
  // two of the five tables carrying an event_id. The one genuinely stranded row
  // in the live database was an open simulated trade, so the badge read 0 while
  // a broken reference existed. `dangling_refs` is the backend's own total over
  // every watched table; the two legacy keys remain the fallback for a backend
  // that predates it, so an older API does not make the badge read "—".
  //
  // 53.133: that fallback used to end in `?? 0`, which printed a number for two
  // different unknowns -- "not asked yet" (first frame) and "this backend sends
  // none of the three keys". Both are `undefined` now and the badge prints "—".
  const danglingRefs = resolveDanglingRefs(status?.counts);
  // 53.95: `dangling_refs` counts history and can never return to zero after
  // the first purge (a voided trade still names its deleted event), so the
  // badge reads the actionable subset — rows that still have a next step
  // blocked. Backends that predate the key fall back to the total rather than
  // to "—", exactly like the legacy pair above.
  const actionableDanglingRefs =
    numeric(status?.counts?.actionable_dangling_refs) ?? danglingRefs;
  const danglingByTable = status?.counts?.dangling_by_table ?? {};
  const actionableDanglingByTable =
    status?.counts?.actionable_dangling_by_table ?? danglingByTable;
  // "3 broken references" is not actionable without knowing which store, and the
  // badge has no room for a breakdown.
  const formatBreakdown = (byTable: Record<string, number>) =>
    Object.entries(byTable)
      .filter(([, n]) => n > 0)
      .map(([table, n]) => `${table}: ${n}`)
      .join(" · ");
  // The historical total is only worth the tooltip when the badge is showing
  // zero: that is the one case where "0" needs the follow-up "but N rows are
  // still pointing at deleted events, and no action remains for them".
  //
  // 53.133: "—" gets its own sentence. Reusing the zero case's line would say
  // "there is nothing stranded" about a reading that has not arrived.
  const danglingTitle =
    actionableDanglingRefs === undefined
      ? "还没读到状态 —— 这个 — 不是 0"
      : actionableDanglingRefs > 0
        ? formatBreakdown(actionableDanglingByTable)
        : (danglingRefs ?? 0) > 0
          ? `无待处置项 · 历史悬空引用 ${danglingRefs}（${formatBreakdown(danglingByTable)}）`
          : "没有指向已删除事件的残留行";

  // 53.126: the readings the §5 switches exist for. `predictions` is a
  // status-grouped map, so the badge shows the total and the tooltip the
  // breakdown. The one switch covers `orphan_predictions` too (same table), but
  // that number still has no badge here -- it needs its own copy, not a gate.
  const predictionTotal =
    status?.counts?.predictions === undefined
      ? undefined
      : Object.values(status.counts.predictions).reduce((sum, n) => sum + n, 0);
  const predictionsBadge = countBadge(
    "预测",
    predictionTotal,
    { available: status?.counts?.predictions_available },
    formatBreakdown(status?.counts?.predictions ?? {}),
  );
  // `pending_reviews` / `breached_reviews` are copied straight out of the
  // `review_queue` block, so their gates are that block's pair -- not any key
  // under `counts`. Reading the wrong one would be the §53.117 mistake again.
  const reviewFlags = {
    available: status?.review_queue?.available,
    enabled: status?.review_queue?.enabled,
  };
  const pendingReviewBadge = countBadge("审核待办", status?.counts?.pending_reviews, reviewFlags);
  const breachedReviewBadge = countBadge("SLA 逾期", status?.counts?.breached_reviews, reviewFlags);
  // `orphan_predictions` shares `predictions_available` -- same table, and the
  // producer ANDs the two reads together (§53.117). The number is *not* "a
  // prediction whose event vanished": it counts events that have already
  // resolved while their prediction is still marked open, i.e. settlement never
  // closed it. The label has to say that, because the obvious guess ("孤儿" =
  // a prediction with no event) names the opposite defect -- and that one is
  // already what `dangling_refs` above measures.
  const orphanBadge = countBadge(
    "结算未收口",
    status?.counts?.orphan_predictions,
    { available: status?.counts?.predictions_available },
    "已结算事件上仍标记 open 的预测条数；>0 说明结算没把预测收口",
  );
  const predictionsUnavailable = status?.counts?.predictions_available === false;
  const predictionEntries = Object.entries(status?.counts?.predictions ?? {}).sort(
    ([, a], [, b]) => b - a,
  );

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-center">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-2 rounded-md px-1 -mx-1 transition-colors hover:bg-secondary"
              aria-expanded={expanded}
              aria-label={expanded ? "折叠系统状态" : "展开系统状态"}
            >
              <span
                className={cn(
                  "flex size-8 items-center justify-center rounded-md",
                  degraded ? "bg-neg/10 text-neg" : "bg-pos/10 text-pos",
                )}
              >
                {degraded ? (
                  <AlertTriangle className="size-4" aria-hidden="true" />
                ) : (
                  <Activity className="size-4" aria-hidden="true" />
                )}
              </span>
              <div className="flex flex-col">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">系统状态</span>
                  <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", apiMeta.cls)}>
                    API {apiMeta.label}
                  </span>
                </div>
                <span className="text-xs text-muted-foreground">
                  调度器 {running === false ? "已停止" : "运行中"}
                  {run ? ` · 最近任务 ${fmtDateTime(run.finished_at ?? run.started_at)}` : ""}
                </span>
              </div>
              <ChevronDown
                className={cn("size-4 text-muted-foreground transition-transform", !expanded && "-rotate-90")}
                aria-hidden="true"
              />
            </button>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span className="rounded bg-secondary px-2 py-1">事件 {status?.counts?.events ?? "—"}</span>
            <span className="rounded bg-secondary px-2 py-1">已结算 {status?.counts?.resolved_events ?? "—"}</span>
            <span className={predictionsBadge.cls} title={predictionsBadge.title}>
              {predictionsBadge.text}
            </span>
            <span className={orphanBadge.cls} title={orphanBadge.title}>
              {orphanBadge.text}
            </span>
            <span className="rounded bg-secondary px-2 py-1">待审链接 {status?.counts?.pending_links ?? "—"}</span>
            <span className={pendingReviewBadge.cls} title={pendingReviewBadge.title}>
              {pendingReviewBadge.text}
            </span>
            <span className={breachedReviewBadge.cls} title={breachedReviewBadge.title}>
              {breachedReviewBadge.text}
            </span>
            <span className="rounded bg-secondary px-2 py-1" title={danglingTitle}>
              引用异常 {actionableDanglingRefs ?? "—"}
            </span>
            <span className="rounded bg-secondary px-2 py-1">校准样本 {status?.counts?.calibration_n ?? "—"}</span>
            <span className="rounded bg-secondary px-2 py-1">版本 {overview?.version ?? health?.version ?? "—"}</span>
            <span className="rounded bg-secondary px-2 py-1">接口 {endpointCount ?? "—"}</span>
            <span className="rounded bg-secondary px-2 py-1">
              LLM 路由 {llmDiagnostics ? `${llmConfiguredTaskCount}/${llmTaskCount}` : "—"}
            </span>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 md:justify-end">
          {error && <span className="text-xs text-neg">{error}</span>}
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
            刷新状态
          </button>
        </div>
      </div>

      {/* 53.132: the distribution is the most concrete thing this panel knows, and
          it used to live only inside the collapse -- the other place it could be
          read was a tooltip on `预测 171`, and a tooltip needs a hover.

          Gated on `status` rather than left to render on the first frame:
          `status === null` means "not asked yet", and 「共 0 条」 there would be
          exactly the confident zero this whole line of work exists to remove
          (§5). The empty-after-a-successful-read case still renders its own
          sentence, because that zero is real. */}
      {status && (
        <div className="border-t border-border pt-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-foreground">预测台账</span>
            <span className="text-[11px] text-muted-foreground">
              {predictionsUnavailable ? "这次没读到" : `共 ${predictionTotal ?? 0} 条`}
            </span>
          </div>
          {predictionsUnavailable ? (
            <p className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
              predictions 与 orphan_predictions 这次都没读到 —— 同一张表、同一个开关。这里的 0 不是「一条都没有」；
              后端日志里有 prediction status counts unavailable / orphan prediction count unavailable。
            </p>
          ) : predictionEntries.length === 0 ? (
            <p className="py-2 text-xs text-muted-foreground">台账里还没有预测。</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {predictionEntries.map(([name, n]) => (
                <span
                  key={name}
                  data-testid={`prediction-status-${name}`}
                  className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground"
                  title={PREDICTION_STATUS_LABELS[name]?.hint}
                >
                  <span className="text-foreground">{PREDICTION_STATUS_LABELS[name]?.label ?? name}</span> {n}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {expanded && (
        <>
          <div className="border-t border-border pt-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-foreground">LLM 网关路由</span>
              {llmDiagnostics && (
                <span className="text-[11px] text-muted-foreground">
                  已配置 {llmConfiguredTaskCount}/{llmTaskCount} 个任务
                </span>
              )}
            </div>
            {llmCostCap && (
              <div
                data-testid="llm-cost-cap"
                className="mb-3 rounded-md border border-border bg-background/40 p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">每日 LLM 成本上限</span>
                    <span
                      className={cn(
                        "rounded-md border px-2 py-0.5 text-[11px] font-medium",
                        llmCostCapMeta(llmCostCap.status).cls,
                      )}
                    >
                      {llmCostCapMeta(llmCostCap.status).label}
                    </span>
                  </div>
                  {llmCostCap.enabled ? (
                    <span className="text-[11px] text-muted-foreground">
                      今日 {fmtUsd(llmCostCap.spend_today_usd)} / 上限{" "}
                      {fmtUsd(llmCostCap.cap_usd)} · 剩余 {fmtUsd(llmCostCap.remaining_usd)}
                    </span>
                  ) : (
                    <span className="text-[11px] text-muted-foreground">
                      LLM_DAILY_COST_CAP_USD=0，不限额
                    </span>
                  )}
                </div>
                {llmCostCap.enabled && typeof llmCostCap.used_ratio === "number" && (
                  <div
                    className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-secondary"
                    role="progressbar"
                    aria-label="今日 LLM 成本占上限比例"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(Math.min(1, llmCostCap.used_ratio) * 100)}
                  >
                    <div
                      className={cn(
                        "h-full rounded-full",
                        llmCostCap.status === "exceeded"
                          ? "bg-neg"
                          : llmCostCap.status === "warning"
                            ? "bg-warn"
                            : "bg-pos",
                      )}
                      style={{ width: `${Math.min(100, llmCostCap.used_ratio * 100)}%` }}
                    />
                  </div>
                )}
                {llmCostCap.error && (
                  <p className="mt-2 text-[11px] text-warn">
                    读取今日花费失败（{llmCostCap.error}）；上限仍在生效。
                  </p>
                )}
              </div>
            )}
            {llmDiagnosticsError ? (
              <p className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs leading-relaxed text-neg">
                {llmDiagnosticsError}
              </p>
            ) : !llmDiagnostics ? (
              <p className="py-2 text-xs text-muted-foreground">暂无 LLM 诊断数据。</p>
            ) : (
              <div className="grid gap-2 md:grid-cols-2">
                {llmDiagnostics.tasks.map((task) => {
                  const meta = llmRouteMeta(task.configured);
                  return (
                    <div key={task.task} className="rounded-md border border-border bg-background/40 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium">{llmTaskLabel(task.task)}</span>
                            <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", meta.cls)}>
                              {meta.label}
                            </span>
                          </div>
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            {task.setting} · {llmRouteSourceLabel(task.route_source)}
                          </div>
                        </div>
                      </div>
                      {task.routes.length === 0 ? (
                        <p className="mt-2 text-xs text-muted-foreground">未解析到 provider/model 路由。</p>
                      ) : (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {task.routes.map((route, index) => (
                            <span
                              key={`${task.task}:${route.provider}:${index}`}
                              className={cn(
                                "rounded bg-secondary px-2 py-1 font-mono text-[11px]",
                                route.api_key_configured ? "text-foreground" : "text-muted-foreground",
                              )}
                              title={`models=${route.models.join(", ") || "none"}; key=${route.api_key_configured ? "yes" : "no"}; base_url=${route.base_url_configured ? "yes" : "no"}`}
                            >
                              {route.provider} · {route.models.length} models · key {route.api_key_configured ? "yes" : "no"}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="border-t border-border pt-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-foreground">循环任务</span>
              {failedWithoutDetails && (
                <span className="text-[11px] text-muted-foreground">输入写入 key 后刷新，可查看失败详情。</span>
              )}
            </div>
        {runs.length === 0 ? (
          <p className="py-2 text-xs text-muted-foreground">暂无任务运行记录。</p>
        ) : (
          <div className="divide-y divide-border">
            {runs.map(([key, r]) => {
              const meta = statusMeta(r.status);
              const result = resultEntries(r);
              return (
                <div key={key} className="py-3 first:pt-0 last:pb-0">
                  <div className="grid gap-2 md:grid-cols-[1fr_auto] md:items-start">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{jobLabel(key, r)}</span>
                        <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", meta.cls)}>
                          {meta.label}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span>开始 {fmtDateTime(r.started_at)}</span>
                        <span>结束 {fmtDateTime(r.finished_at)}</span>
                        <span>耗时 {runDuration(r)}</span>
                      </div>
                    </div>
                    {r.id && (
                      <span className="truncate font-mono text-[11px] text-muted-foreground md:max-w-44 md:text-right">
                        {r.id}
                      </span>
                    )}
                  </div>

                  {r.error && (
                    <p className="mt-2 rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs leading-relaxed text-neg">
                      {r.error}
                    </p>
                  )}

                  {result.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {result.map(([k, v]) => (
                        <span
                          key={k}
                          className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground"
                        >
                          <span className="text-foreground">{k}</span> {formatResultValue(v)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {recentRuns.length > 0 && (
        <div className="border-t border-border pt-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-foreground">运行历史</span>
            <span className="text-[11px] text-muted-foreground">最近 {recentRuns.length} 次</span>
          </div>
          <div className="flex items-center gap-1 overflow-hidden">
            {[...recentRuns].reverse().map((r, index) => (
              <span
                key={`${r.id ?? r.job_name ?? "run"}:${r.started_at ?? index}`}
                className={cn("h-2 flex-1 rounded-full", historyTone(r.status))}
                aria-hidden="true"
              />
            ))}
          </div>
          <div className="mt-3 grid gap-2">
            {recentRuns.slice(0, 8).map((r, index) => {
              const ms = runDurationMs(r) ?? 0;
              const width = Math.max(4, Math.round((ms / maxDuration) * 100));
              const key = r.job_name ?? "job";
              const meta = statusMeta(r.status);
              return (
                <div key={`${r.id ?? key}:${r.started_at ?? index}`} className="grid gap-1.5">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 text-xs">
                    <div className="min-w-0">
                      <span className="font-medium text-foreground">{jobLabel(key, r)}</span>
                      <span className="ml-2 text-muted-foreground">{fmtDateTime(r.started_at)}</span>
                    </div>
                    <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", meta.cls)}>
                      {runDuration(r)}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
                    <div className={cn("h-full", historyTone(r.status))} style={{ width: `${width}%` }} />
                  </div>
                  {r.error && (
                    <p className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs leading-relaxed text-neg">
                      {r.error}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
        </>
      )}
    </section>
  );
}
