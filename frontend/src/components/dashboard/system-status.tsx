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
  const name = run?.job_name ?? run?.job ?? key;
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
  return run?.duration_ms ?? (run?.duration_seconds != null ? run.duration_seconds * 1000 : null);
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
  const result = run?.result ?? run?.details;
  if (!result) return [];
  return Object.entries(result)
    .filter(([, value]) => value != null && value !== "" && (!Array.isArray(value) || value.length > 0))
    .slice(0, 8);
}

function apiStatusMeta(status: string | null | undefined) {
  if (!status) return { label: "未知", cls: "border-border bg-secondary text-muted-foreground", degraded: false };
  return API_STATUS_META[status] ?? { label: status, cls: "border-border bg-secondary text-muted-foreground", degraded: false };
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
  const degraded = apiMeta.degraded || failed || running === false || llmUnconfigured;
  const failedWithoutDetails = runs.some(([, r]) => r.status === "failed" && !r.error);
  const endpointCount = overview?.endpoints ? Object.keys(overview.endpoints).length : null;
  // E2: this used to sum `dangling_predictions + dangling_links`, which covered
  // two of the five tables carrying an event_id. The one genuinely stranded row
  // in the live database was an open simulated trade, so the badge read 0 while
  // a broken reference existed. `dangling_refs` is the backend's own total over
  // every watched table; the two legacy keys remain the fallback for a backend
  // that predates it, so an older API does not make the badge read "—".
  const danglingRefs =
    status?.counts?.dangling_refs ??
    (status?.counts?.dangling_predictions ?? 0) + (status?.counts?.dangling_links ?? 0);
  const danglingByTable = status?.counts?.dangling_by_table ?? {};
  // "3 broken references" is not actionable without knowing which store, and the
  // badge has no room for a breakdown.
  const danglingTitle =
    danglingRefs > 0
      ? Object.entries(danglingByTable)
          .filter(([, n]) => n > 0)
          .map(([table, n]) => `${table}: ${n}`)
          .join(" · ")
      : "没有指向已删除事件的残留行";

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
            <span className="rounded bg-secondary px-2 py-1">待审链接 {status?.counts?.pending_links ?? "—"}</span>
            <span className="rounded bg-secondary px-2 py-1" title={danglingTitle}>
              引用异常 {danglingRefs}
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
                key={`${r.id ?? r.job_name ?? r.job ?? "run"}:${r.started_at ?? index}`}
                className={cn("h-2 flex-1 rounded-full", historyTone(r.status))}
                aria-hidden="true"
              />
            ))}
          </div>
          <div className="mt-3 grid gap-2">
            {recentRuns.slice(0, 8).map((r, index) => {
              const ms = runDurationMs(r) ?? 0;
              const width = Math.max(4, Math.round((ms / maxDuration) * 100));
              const key = r.job_name ?? r.job ?? "job";
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
