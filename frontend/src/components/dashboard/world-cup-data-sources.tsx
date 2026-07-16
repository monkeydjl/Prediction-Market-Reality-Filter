"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Eye,
  Gavel,
  Loader2,
  Plug,
  RefreshCw,
  UploadCloud,
} from "lucide-react";
import {
  eventsApi,
  type LoopRun,
  type WorldCupApiFootballConnectionResult,
  type WorldCupCallBudget,
  type WorldCupDataSourceActionMode,
  type WorldCupDataSourceActionResult,
  type WorldCupDataSourceStatus,
  type WorldCupFeedConfig,
  type WorldCupPipelineValidateResult,
  type WorldCupResolveMatch,
  type WorldCupResolveResult,
  type WorldCupSkippedSource,
  type WorldCupSportmonksConnectionResult,
  type WorldCupSourceFetch,
} from "@/lib/api";
import { analyticsApi } from "@/lib/world-cup/analytics-api";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

type SourceAction = "preview" | "import";

interface SourceRow {
  mode: WorldCupDataSourceActionMode;
  label: string;
  configured: boolean;
  ready: boolean;
  detail: string;
  meta: string[];
  references: SourceReference[];
}

interface ActionState {
  mode: WorldCupDataSourceActionMode;
  action: SourceAction;
}

interface CompletedAction extends ActionState {
  result: WorldCupDataSourceActionResult;
  completedAt: Date;
}

interface SourceReference {
  label: string;
  value: string;
}

interface PredictionCoverageMatch {
  match_id: string;
  home_team: string;
  away_team: string;
  kickoff_utc?: string | null;
  stage?: string | null;
  status?: string | null;
  prediction_method?: string | null;
  last_updated?: string | null;
  age_hours?: number | null;
}

interface PredictionCoverage {
  status: string;
  coverage_ok: boolean;
  scheduled_count: number;
  predicted_count: number;
  missing_count: number;
  stale_count: number;
  stale_after_hours: number;
  missing_predictions: PredictionCoverageMatch[];
  stale_predictions: PredictionCoverageMatch[];
}

const RESOLVE_DRY_RUN_LIMIT = 200;
const PREDICTION_COVERAGE_STALE_HOURS = 24;

const SOURCE_LABELS: Record<WorldCupDataSourceActionMode, string> = {
  data_file: "Data file",
  bundle_file: "Bundle file",
  bundle_url: "Bundle URL",
  feeds: "Raw feeds",
  api_football: "API-Football",
  football_data: "Football-Data.org",
  sportmonks: "Sportmonks",
};

const RUN_STATUS_META: Record<string, { label: string; cls: string; icon: "ok" | "warn" }> = {
  success: { label: "成功", cls: "border-pos/40 bg-pos/10 text-pos", icon: "ok" },
  failed: { label: "失败", cls: "border-neg/40 bg-neg/10 text-neg", icon: "warn" },
  running: { label: "运行中", cls: "border-primary/40 bg-primary/10 text-primary", icon: "ok" },
};

function formatDurationMs(ms: unknown) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function formatValue(value: unknown) {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("zh-CN") : String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === "object") return `${Object.keys(value).length} 项`;
  return String(value);
}

function outcomeLabel(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${Number(value).toFixed(0)}%`;
}

function matchResultLabel(result: string | undefined) {
  if (result === "would_resolve") return "将结算";
  if (result === "resolved") return "已结算";
  return result || "未知";
}

function statusBadge(status: string | undefined, fallback = "未知") {
  const meta = RUN_STATUS_META[status ?? ""];
  if (meta) return meta;
  return {
    label: status || fallback,
    cls: "border-border bg-secondary text-muted-foreground",
    icon: "warn" as const,
  };
}

function configuredFeeds(feeds: WorldCupFeedConfig[] | undefined) {
  return (feeds ?? []).filter((feed) => feed.configured);
}

function hasConfiguredFeedKind(feeds: WorldCupFeedConfig[] | undefined, kind: string) {
  return configuredFeeds(feeds).some((feed) => feed.kind === kind);
}

function hasQualificationSource(status: WorldCupDataSourceStatus | null) {
  const sources = status?.configured_sources;
  if (!sources) return false;
  return Boolean(
    sources.api_football?.configured
      || sources.football_data?.configured
      || hasConfiguredFeedKind(sources.feeds, "standings")
      || hasConfiguredFeedKind(sources.sportmonks?.feeds, "standings")
  );
}

function feedReferences(feeds: WorldCupFeedConfig[] | undefined): SourceReference[] {
  return (feeds ?? [])
    .map((feed) => ({
      label: feed.kind || feed.source || "feed",
      value: feed.source_url || "",
    }))
    .filter((reference) => reference.value);
}

function sourceRows(status: WorldCupDataSourceStatus | null): SourceRow[] {
  const sources = status?.configured_sources ?? {};
  const feeds = configuredFeeds(sources.feeds);
  const sportmonksFeeds = configuredFeeds(sources.sportmonks?.feeds);
  const api = sources.api_football;
  const footballData = sources.football_data;
  const sportmonks = sources.sportmonks;

  return [
    {
      mode: "data_file",
      label: SOURCE_LABELS.data_file,
      configured: Boolean(sources.data_file?.configured),
      ready: Boolean(sources.data_file?.configured && sources.data_file?.exists),
      detail: sources.data_file?.configured
        ? sources.data_file.exists ? sources.data_file.path || "已配置" : "文件不存在"
        : "未配置",
      meta: [],
      references: [],
    },
    {
      mode: "bundle_file",
      label: SOURCE_LABELS.bundle_file,
      configured: Boolean(sources.bundle_file?.configured),
      ready: Boolean(sources.bundle_file?.configured && sources.bundle_file?.exists),
      detail: sources.bundle_file?.configured
        ? sources.bundle_file.exists ? sources.bundle_file.path || "已配置" : "文件不存在"
        : "未配置",
      meta: [],
      references: [],
    },
    {
      mode: "bundle_url",
      label: SOURCE_LABELS.bundle_url,
      configured: Boolean(sources.bundle_url?.configured),
      ready: Boolean(sources.bundle_url?.configured),
      detail: sources.bundle_url?.source_url || "未配置",
      meta: [],
      references: [],
    },
    {
      mode: "feeds",
      label: SOURCE_LABELS.feeds,
      configured: feeds.length > 0,
      ready: feeds.length > 0,
      detail: feeds.length > 0 ? feeds.map((feed) => feed.kind).filter(Boolean).join(", ") : "未配置",
      meta: feeds.length > 0 ? [`${feeds.length} 个 feed`] : [],
      references: feedReferences(feeds),
    },
    {
      mode: "api_football",
      label: SOURCE_LABELS.api_football,
      configured: Boolean(api?.configured),
      ready: Boolean(api?.configured),
      detail: api?.configured
        ? `league ${api.league_id || "—"} · season ${api.season || "—"}`
        : "未配置",
      meta: [
        api?.fetch_events ? "events" : "",
        api?.fetch_lineups ? "lineups" : "",
        api?.fetch_statistics ? "statistics" : "",
        api?.max_detail_calls != null ? `budget ${api.max_detail_calls}` : "",
      ].filter(Boolean),
      references: api?.base_url ? [{ label: "base", value: api.base_url }] : [],
    },
    {
      mode: "football_data",
      label: SOURCE_LABELS.football_data,
      configured: Boolean(footballData?.configured),
      ready: Boolean(footballData?.configured),
      detail: footballData?.configured
        ? `competition ${footballData.competition || "WC"}`
        : "未配置",
      meta: [],
      references: footballData?.base_url ? [{ label: "base", value: footballData.base_url }] : [],
    },
    {
      mode: "sportmonks",
      label: SOURCE_LABELS.sportmonks,
      configured: Boolean(sportmonks?.configured),
      ready: Boolean(sportmonks?.configured),
      detail: sportmonksFeeds.length > 0
        ? sportmonksFeeds.map((feed) => feed.kind).filter(Boolean).join(", ")
        : "未配置",
      meta: sportmonksFeeds.length > 0 ? [`${sportmonksFeeds.length} 个 feed`] : [],
      references: feedReferences(sportmonksFeeds),
    },
  ];
}

function resultPills(result: Record<string, unknown>) {
  const items: [string, unknown][] = [
    ["provider", result.provider],
    ["mode", result.mode],
    ["sources", result.source_count],
    ["converted", result.converted_fact_count],
    ["imported", result.imported],
    ["errors", result.error_count],
    ["fetches", result.source_fetch_count],
    ["skipped", result.skipped_source_count],
    ["duration", result.duration_ms != null ? formatDurationMs(result.duration_ms) : undefined],
    ["replace", result.replace],
  ];
  return items.filter(([, value]) => value != null && value !== "");
}

function fetchesFromRun(run: LoopRun | null | undefined): WorldCupSourceFetch[] {
  const value = run?.result?.source_fetches;
  return Array.isArray(value) ? value as WorldCupSourceFetch[] : [];
}

function skippedFromRun(run: LoopRun | null | undefined): WorldCupSkippedSource[] {
  const value = run?.result?.skipped_sources;
  return Array.isArray(value) ? value as WorldCupSkippedSource[] : [];
}

function budgetFromRun(run: LoopRun | null | undefined): WorldCupCallBudget {
  return asRecord(run?.result?.call_budget) as WorldCupCallBudget;
}

function sourceReferencesFromResult(result: Record<string, unknown> | undefined): SourceReference[] {
  const references: SourceReference[] = [];
  if (!result) return references;

  if (typeof result.source_file === "string" && result.source_file) {
    references.push({ label: "file", value: result.source_file });
  }
  if (typeof result.source_url === "string" && result.source_url) {
    references.push({ label: "url", value: result.source_url });
  }
  if (Array.isArray(result.source_feeds)) {
    references.push(...feedReferences(result.source_feeds as WorldCupFeedConfig[]));
  }
  return references;
}

function errorsFromResult(result: Record<string, unknown> | undefined): unknown[] {
  return Array.isArray(result?.errors) ? result.errors : [];
}

function callBudgetEntries(budget: WorldCupCallBudget | undefined) {
  if (!budget || Object.keys(budget).length === 0) return [];
  return [
    ["fixtures", budget.fixture_count],
    ["max", budget.max_detail_calls],
    ["used", budget.detail_calls_used],
    ["skipped", budget.detail_calls_skipped],
    ["remaining", budget.detail_calls_remaining],
  ].filter(([, value]) => value != null);
}

function coverageMatchLabel(match: PredictionCoverageMatch) {
  const home = match.home_team || "home";
  const away = match.away_team || "away";
  return `${home} vs ${away}`;
}

function RunPills({ result }: { result: Record<string, unknown> }) {
  const pills = resultPills(result);
  if (pills.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {pills.map(([key, value]) => (
        <span key={key} className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground">
          <span className="text-foreground">{key}</span> {formatValue(value)}
        </span>
      ))}
    </div>
  );
}

function PredictionCoveragePanel({
  coverage,
  error,
}: {
  coverage: PredictionCoverage | null;
  error: string | null;
}) {
  if (error) {
    return (
      <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
        Prediction coverage unavailable: {error}
      </div>
    );
  }
  if (!coverage) return null;

  const issueMatches = [
    ...coverage.missing_predictions.map((match) => ({ match, kind: "missing" })),
    ...coverage.stale_predictions.map((match) => ({ match, kind: "stale" })),
  ];

  return (
    <div className={cn(
      "grid gap-2 rounded-md border px-3 py-3 text-xs",
      coverage.coverage_ok
        ? "border-pos/40 bg-pos/10 text-pos"
        : "border-warn/40 bg-warn/10 text-warn",
    )}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-medium">
          {coverage.coverage_ok ? (
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
          ) : (
            <AlertTriangle className="size-3.5" aria-hidden="true" />
          )}
          <span>Prediction coverage</span>
        </div>
        <span className="text-[11px] text-muted-foreground">
          stale after {coverage.stale_after_hours}h
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {[
          ["scheduled", coverage.scheduled_count],
          ["predicted", coverage.predicted_count],
          ["missing", coverage.missing_count],
          ["stale", coverage.stale_count],
        ].map(([label, value]) => (
          <span key={label} className="rounded bg-card/70 px-2 py-1 font-mono text-[11px] text-muted-foreground">
            {label} {formatValue(value)}
          </span>
        ))}
      </div>
      {issueMatches.length > 0 && (
        <div className="divide-y divide-border/60 overflow-hidden border-y border-border/60">
          {issueMatches.slice(0, 4).map(({ match, kind }) => (
            <div key={`${kind}:${match.match_id}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="font-medium text-foreground">{coverageMatchLabel(match)}</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {kind}
                {match.prediction_method ? ` · ${match.prediction_method}` : ""}
                {match.age_hours != null ? ` · ${Number(match.age_hours).toFixed(1)}h` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SourceReferenceList({
  references,
  title,
  limit = 8,
  className,
}: {
  references: SourceReference[];
  title?: string;
  limit?: number;
  className?: string;
}) {
  if (references.length === 0) return null;
  const visible = references.slice(0, limit);
  return (
    <div className={cn("grid gap-1.5", className)}>
      {title && <span className="text-xs font-medium text-foreground">{title}</span>}
      <div className="divide-y divide-border overflow-hidden border-y border-border">
        {visible.map((reference, index) => (
          <div key={`${reference.label}:${reference.value}:${index}`} className="grid gap-1 py-2 md:grid-cols-[8rem_minmax(0,1fr)] md:items-center">
            <span className="font-mono text-xs text-foreground">{reference.label}</span>
            <span className="truncate text-xs text-muted-foreground">{reference.value}</span>
          </div>
        ))}
        {references.length > visible.length && (
          <div className="py-2 text-xs text-muted-foreground">
            +{references.length - visible.length} more
          </div>
        )}
      </div>
    </div>
  );
}

function FetchList({ fetches }: { fetches: WorldCupSourceFetch[] }) {
  if (fetches.length === 0) return null;
  return (
    <div className="grid gap-1.5">
      <span className="text-xs font-medium text-foreground">Fetches</span>
      <div className="divide-y divide-border overflow-hidden border-y border-border">
        {fetches.slice(0, 8).map((fetch, index) => {
          const meta = statusBadge(fetch.status, "unknown");
          return (
            <div key={`${fetch.kind ?? "fetch"}:${fetch.source_url ?? index}`} className="grid gap-1 py-2 md:grid-cols-[8rem_minmax(0,1fr)_auto] md:items-center">
              <span className="font-mono text-xs text-foreground">{fetch.kind || "—"}</span>
              <span className="truncate text-xs text-muted-foreground">{fetch.source_url || "—"}</span>
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", meta.cls)}>
                  {meta.label}
                </span>
                {formatDurationMs(fetch.duration_ms)}
              </span>
              {fetch.error && (
                <span className="text-xs text-neg md:col-span-3">{fetch.error}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function errorText(error: unknown) {
  if (typeof error === "string") return error;
  const record = asRecord(error);
  const message = record.error ?? record.message ?? record.detail;
  if (message != null) {
    const prefix = record.index != null ? `#${formatValue(record.index)} ` : "";
    return `${prefix}${formatValue(message)}`;
  }
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

function ErrorList({ errors }: { errors: unknown[] }) {
  if (errors.length === 0) return null;
  const visible = errors.slice(0, 6);
  return (
    <div className="grid gap-1.5">
      <span className="text-xs font-medium text-neg">Errors</span>
      <div className="divide-y divide-neg/20 overflow-hidden border-y border-neg/30">
        {visible.map((error, index) => (
          <div key={index} className="py-2 text-xs leading-relaxed text-neg">
            {errorText(error)}
          </div>
        ))}
        {errors.length > visible.length && (
          <div className="py-2 text-xs text-neg">
            +{errors.length - visible.length} more
          </div>
        )}
      </div>
    </div>
  );
}

function SkippedList({ skipped }: { skipped: WorldCupSkippedSource[] }) {
  if (skipped.length === 0) return null;
  return (
    <div className="grid gap-1.5">
      <span className="text-xs font-medium text-foreground">Skipped</span>
      <div className="divide-y divide-border overflow-hidden border-y border-border">
        {skipped.slice(0, 8).map((item, index) => (
          <div key={`${item.kind ?? "skip"}:${item.source_url ?? index}`} className="grid gap-1 py-2 md:grid-cols-[8rem_minmax(0,1fr)_auto] md:items-center">
            <span className="font-mono text-xs text-foreground">{item.kind || "—"}</span>
            <span className="truncate text-xs text-muted-foreground">{item.source_url || "—"}</span>
            <span className="text-xs text-warn">
              {item.reason || "skipped"}
              {item.required_calls != null ? ` · ${item.required_calls}/${item.remaining_calls ?? 0}` : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CallBudget({ budget }: { budget: WorldCupCallBudget | undefined }) {
  const entries = callBudgetEntries(budget);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => (
        <span key={key} className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground">
          <span className="text-foreground">{key}</span> {formatValue(value)}
        </span>
      ))}
      {(budget?.enabled_detail_feeds ?? []).map((feed) => (
        <span key={feed} className="rounded bg-primary/10 px-2 py-1 font-mono text-[11px] text-primary">
          {feed}
        </span>
      ))}
    </div>
  );
}

function FactKindPills({ byKind }: { byKind: Record<string, number> | undefined }) {
  const entries = Object.entries(byKind ?? {})
    .filter(([, count]) => Number.isFinite(count) && count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([kind, count]) => (
        <span key={kind} className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground">
          <span className="text-foreground">{kind}</span> {count.toLocaleString("zh-CN")}
        </span>
      ))}
    </div>
  );
}

function QualificationFactsPanel({ byKind }: { byKind: Record<string, number> | undefined }) {
  const count = byKind?.qualification ?? 0;
  const ok = count > 0;
  return (
    <div className={cn(
      "flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-xs",
      ok
        ? "border-pos/40 bg-pos/10 text-pos"
        : "border-warn/40 bg-warn/10 text-warn",
    )}>
      <div className="flex items-center gap-2 font-medium">
        {ok ? (
          <CheckCircle2 className="size-3.5" aria-hidden="true" />
        ) : (
          <AlertTriangle className="size-3.5" aria-hidden="true" />
        )}
        <span>Qualification facts</span>
      </div>
      <span className="font-mono text-[11px] text-muted-foreground">
        {count.toLocaleString("zh-CN")} imported
      </span>
    </div>
  );
}

function recommendedImportGuidance(mode: string) {
  if (mode === "api_football") {
    return "Run pipeline validation first; import only after it passes.";
  }
  if (mode === "football_data") {
    return "直接导入 Football-Data.org 真实积分榜。";
  }
  if (mode) {
    return "运行推荐来源的 Import，导入真实 qualification facts。";
  }
  return "";
}

function QualificationSourcePanel({ status }: { status: WorldCupDataSourceStatus | null }) {
  const readiness = status?.real_data_readiness;
  const sourceConfigured = readiness?.qualification_source_configured ?? hasQualificationSource(status);
  const factCount = readiness?.qualification_fact_count ?? status?.facts?.by_kind?.qualification ?? 0;
  const untrustedFactCount = readiness?.untrusted_qualification_fact_count ?? 0;
  const ok = sourceConfigured && factCount > 0;
  const issues = readiness?.issues ?? [];
  const issueDetails = readiness?.issue_details ?? [];
  const recommendedLabel = readiness?.recommended_qualification_import_label || "";
  const recommendedMode = readiness?.recommended_qualification_import_mode || "";
  const lastValidationRun = recommendedMode === "api_football"
    ? status?.runs?.world_cup_api_football_validate ?? null
    : null;
  const lastValidationResult = asRecord(lastValidationRun?.result);
  const lastValidationEntries = ([
    ["provider", lastValidationResult.provider],
    ["ok", lastValidationResult.ok],
    ["fixture_count", lastValidationResult.fixture_count],
    ["failed_step", lastValidationResult.failed_step],
    ["covered", lastValidationResult.covered],
    ["missing_from_store", lastValidationResult.missing_from_store],
  ] as [string, unknown][]).filter(([, value]) => value != null && value !== "");
  const importGuidance = recommendedImportGuidance(recommendedMode);
  const statusText = ok
    ? "Ready"
    : readiness?.qualification_source_state === "validation_failed"
      ? "Validation failed"
      : sourceConfigured
        ? "Configured, import required"
        : "Not configured: standings/API provider required";
  return (
    <div className={cn(
      "grid gap-2 rounded-md border px-3 py-2 text-xs",
      ok
        ? "border-pos/40 bg-pos/10 text-pos"
        : sourceConfigured
          ? "border-warn/40 bg-warn/10 text-warn"
          : "border-neg/40 bg-neg/10 text-neg",
    )}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-medium">
          {ok ? (
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
          ) : (
            <AlertTriangle className="size-3.5" aria-hidden="true" />
          )}
          <span>Real qualification source</span>
        </div>
        <span className="text-[11px] text-muted-foreground">
          {statusText}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <span className="rounded bg-card/70 px-2 py-1 font-mono text-[11px] text-muted-foreground">
          trusted {formatValue(factCount)}
        </span>
        {untrustedFactCount > 0 && (
          <span className="rounded bg-card/70 px-2 py-1 font-mono text-[11px] text-warn">
            untrusted {formatValue(untrustedFactCount)}
          </span>
        )}
      </div>
      {recommendedLabel && (
        <div className="grid gap-1 rounded bg-card/70 px-2 py-1.5 text-[11px] text-muted-foreground">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">Recommended import</span>
            <span>{recommendedLabel}</span>
            {recommendedMode && <span className="font-mono">{recommendedMode}</span>}
          </div>
          {!ok && importGuidance && (
            <div>{importGuidance}</div>
          )}
        </div>
      )}
      {lastValidationRun && (
        <div className="grid gap-1.5 rounded bg-card/70 px-2 py-1.5 text-[11px] text-muted-foreground">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-foreground">Last provider validation</span>
            <span className="font-mono">{lastValidationRun.status}</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {lastValidationEntries.map(([key, value]) => (
              <span key={key} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px]">
                <span className="text-foreground">{key}</span> {formatValue(value)}
              </span>
            ))}
          </div>
          {lastValidationRun.finished_at || lastValidationRun.started_at ? (
            <div>
              {fmtDateTime(lastValidationRun.finished_at ?? lastValidationRun.started_at)}
              {lastValidationRun.duration_ms != null ? ` · ${formatDurationMs(lastValidationRun.duration_ms)}` : ""}
            </div>
          ) : null}
          {lastValidationRun.error && (
            <div className="text-neg">{lastValidationRun.error}</div>
          )}
        </div>
      )}
      {issueDetails.length > 0 ? (
        <div className="grid gap-1.5">
          {issueDetails.map((issue) => (
            <div key={issue.code ?? issue.message} className="rounded bg-card/70 px-2 py-1.5 text-[11px] text-muted-foreground">
              <div className="font-medium text-foreground">{issue.message || issue.code}</div>
              {issue.action && <div className="mt-0.5">{issue.action}</div>}
            </div>
          ))}
        </div>
      ) : issues.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {issues.map((issue) => (
            <span key={issue} className="rounded bg-card/70 px-2 py-1 font-mono text-[11px] text-muted-foreground">
              {issue}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ResolveMatches({ matches }: { matches: WorldCupResolveMatch[] }) {
  if (matches.length === 0) {
    return (
      <p className="rounded-md border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground">
        当前 facts 还没有触发可结算的世界杯事件。
      </p>
    );
  }
  return (
    <div className="divide-y divide-border overflow-hidden border-y border-border">
      {matches.slice(0, 6).map((match, index) => (
        <div key={`${match.event_id ?? "match"}:${index}`} className="grid gap-2 py-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                {matchResultLabel(match.result)}
              </span>
              {match.confidence != null && (
                <span className="font-mono text-[11px] text-muted-foreground">
                  confidence {Number(match.confidence).toFixed(2)}
                </span>
              )}
              {(match.facts?.length ?? 0) > 0 && (
                <span className="font-mono text-[11px] text-muted-foreground">
                  facts {match.facts?.length}
                </span>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-sm font-medium">{match.event_title || match.event_id || "—"}</p>
            {match.reason && (
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{match.reason}</p>
            )}
          </div>
          <div className="font-mono text-sm font-semibold text-foreground md:text-right">
            {outcomeLabel(match.actual_outcome)}
          </div>
        </div>
      ))}
    </div>
  );
}

function ResolveDryRun({ result }: { result: WorldCupResolveResult }) {
  const matches = result.matches ?? [];
  return (
    <div className="grid gap-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">结算 dry-run</span>
        <span className="text-[11px] text-muted-foreground">
          checked {result.checked_count ?? 0} · would resolve {result.resolved_count ?? 0} · pending {result.pending_count ?? 0}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <span className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground">
          <span className="text-foreground">unresolved</span> {formatValue(result.unresolved_events)}
        </span>
        <span className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground">
          <span className="text-foreground">matches</span> {matches.length}
        </span>
      </div>
      <ResolveMatches matches={matches} />
    </div>
  );
}

export function WorldCupDataSources() {
  const [status, setStatus] = useState<WorldCupDataSourceStatus | null>(null);
  const [coverage, setCoverage] = useState<PredictionCoverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [replace, setReplace] = useState(false);
  const [running, setRunning] = useState<ActionState | null>(null);
  const [completed, setCompleted] = useState<CompletedAction | null>(null);
  const [dryRunLoading, setDryRunLoading] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<WorldCupResolveResult | null>(null);
  const [connTesting, setConnTesting] = useState(false);
  const [connResult, setConnResult] = useState<WorldCupApiFootballConnectionResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [validateResult, setValidateResult] = useState<WorldCupPipelineValidateResult | null>(null);

  const rows = useMemo(() => sourceRows(status), [status]);
  const lastRun = status?.runs?.world_cup_source_bundle_import ?? null;
  const lastRunResult = lastRun?.result ?? {};
  const lastFetches = fetchesFromRun(lastRun);
  const lastSkipped = skippedFromRun(lastRun);
  const lastBudget = budgetFromRun(lastRun);
  const actionFetches = completed?.result.source_fetches ?? [];
  const actionSkipped = completed?.result.skipped_sources ?? [];
  const actionResult = completed?.result as Record<string, unknown> | undefined;
  const actionSources = sourceReferencesFromResult(actionResult);
  const actionErrors = errorsFromResult(actionResult);
  const lastRunSources = sourceReferencesFromResult(lastRunResult);
  const lastRunErrors = errorsFromResult(lastRunResult);

  async function load(silent = false) {
    if (!silent) setLoading(true);
    setError(null);
    setCoverageError(null);
    try {
      const [statusResult, coverageResult] = await Promise.allSettled([
        eventsApi.worldCupDataSourcesStatus(),
        analyticsApi.predictionCoverage<PredictionCoverage>(PREDICTION_COVERAGE_STALE_HOURS),
      ]);
      if (statusResult.status === "fulfilled") {
        setStatus(statusResult.value);
      } else {
        throw statusResult.reason;
      }
      if (coverageResult.status === "fulfilled") {
        setCoverage(coverageResult.value);
      } else {
        setCoverage(null);
        setCoverageError(
          coverageResult.reason instanceof Error
            ? coverageResult.reason.message
            : String(coverageResult.reason)
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "世界杯数据源状态加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function runSourceAction(mode: WorldCupDataSourceActionMode, action: SourceAction) {
    setRunning({ mode, action });
    setError(null);
    try {
      const result = action === "preview"
        ? await eventsApi.worldCupDataSourcePreview(mode)
        : await eventsApi.worldCupDataSourceImport(mode, replace);
      setCompleted({ mode, action, result, completedAt: new Date() });
      if (action === "import") await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "世界杯数据源操作失败");
    } finally {
      setRunning(null);
    }
  }

  async function runResolveDryRun() {
    setDryRunLoading(true);
    setError(null);
    try {
      setDryRunResult(await eventsApi.worldCupResolveDryRun(RESOLVE_DRY_RUN_LIMIT));
    } catch (e) {
      setError(e instanceof Error ? e.message : "世界杯结算 dry-run 失败");
    } finally {
      setDryRunLoading(false);
    }
  }

  async function runConnectionTest() {
    setConnTesting(true);
    setConnResult(null);
    setError(null);
    try {
      setConnResult(await eventsApi.worldCupApiFootballTest());
    } catch (e) {
      setError(e instanceof Error ? e.message : "连接测试失败");
    } finally {
      setConnTesting(false);
    }
  }

  async function runPipelineValidation() {
    setValidating(true);
    setValidateResult(null);
    setError(null);
    try {
      setValidateResult(await eventsApi.worldCupApiFootballValidate());
      await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pipeline验证失败");
    } finally {
      setValidating(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-start">
        <div className="flex items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Database className="size-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-medium">世界杯数据源</h2>
              <span className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                facts {status?.facts?.count ?? "—"}
              </span>
              <span className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                schedule {status?.scheduled_import?.enabled ? status.scheduled_import.mode : "off"}
              </span>
              <span className={cn(
                "rounded-md border px-2 py-0.5 text-[11px]",
                status?.matchday_refresh?.enabled
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border bg-secondary text-muted-foreground",
              )}>
                matchday {status?.matchday_refresh?.enabled
                  ? `${status.matchday_refresh.interval_minutes}m / ${status.matchday_refresh.window_hours}h`
                  : "off"}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {status?.facts?.last_updated ? `facts 更新 ${fmtDateTime(status.facts.last_updated)}` : "facts 更新 —"}
              {status?.scheduled_import?.enabled
                ? ` · ${String(status.scheduled_import.hour_utc).padStart(2, "0")}:${String(status.scheduled_import.minute_utc).padStart(2, "0")} UTC`
                : ""}
              {status?.runs?.world_cup_matchday_refresh?.started_at
                ? ` · matchday 上次 ${fmtDateTime(status.runs.world_cup_matchday_refresh.finished_at ?? status.runs.world_cup_matchday_refresh.started_at)}`
                : ""}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 md:justify-end">
          <label className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={replace}
              onChange={(e) => setReplace(e.target.checked)}
              className="size-3.5 accent-primary"
            />
            替换导入
          </label>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading || dryRunLoading || Boolean(running)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
            刷新数据源
          </button>
          <button
            type="button"
            onClick={() => void runConnectionTest()}
            disabled={loading || connTesting || Boolean(running)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            {connTesting ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Plug className="size-3.5" aria-hidden="true" />
            )}
            测试连接
          </button>
          <button
            type="button"
            onClick={() => void runPipelineValidation()}
            disabled={loading || validating || Boolean(running)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            {validating ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Eye className="size-3.5" aria-hidden="true" />
            )}
            验证Pipeline
          </button>
          <button
            type="button"
            onClick={() => void runResolveDryRun()}
            disabled={loading || dryRunLoading || Boolean(running)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-primary bg-primary/15 px-2 text-xs font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
          >
            {dryRunLoading ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Gavel className="size-3.5" aria-hidden="true" />
            )}
            dry-run
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs leading-relaxed text-neg">
          {error}
        </div>
      )}

      {connResult && (
        <div className={cn(
          "flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-xs",
          connResult.ok
            ? "border-pos/40 bg-pos/10 text-pos"
            : "border-neg/40 bg-neg/10 text-neg",
        )}>
          {connResult.ok ? (
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
          ) : (
            <AlertTriangle className="size-3.5" aria-hidden="true" />
          )}
          <span className="font-medium">
            {connResult.ok ? "API-Football 连接正常" : `API-Football 连接失败: ${connResult.error}`}
          </span>
          {connResult.ok && connResult.subscription && (
            <span className="text-muted-foreground">
              {connResult.subscription.plan} · {connResult.requests_today}/{connResult.requests_limit} requests today
            </span>
          )}
        </div>
      )}

      {validateResult && (
        <div className={cn(
          "rounded-md border px-3 py-3 text-xs",
          validateResult.ok
            ? "border-pos/40 bg-pos/10 text-pos"
            : "border-neg/40 bg-neg/10 text-neg",
        )}>
          <div className="flex items-center gap-2 font-medium">
            {validateResult.ok ? (
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
            ) : (
              <AlertTriangle className="size-3.5" aria-hidden="true" />
            )}
            <span>Pipeline验证 {validateResult.ok ? "通过" : "失败"}</span>
          </div>
          {validateResult.summary && (
            <p className="mt-2 text-muted-foreground">{validateResult.summary}</p>
          )}
          {validateResult.error && (
            <p className="mt-2 rounded bg-card/70 px-2 py-1.5 text-neg">{validateResult.error}</p>
          )}
          {validateResult.steps && validateResult.steps.length > 0 && (
            <div className="mt-3 space-y-2">
              {validateResult.steps.map((step, idx) => (
                <div key={idx} className="flex items-start gap-2 rounded bg-card/50 px-2 py-1.5">
                  {step.ok ? (
                    <CheckCircle2 className="size-3 shrink-0 text-pos" aria-hidden="true" />
                  ) : (
                    <AlertTriangle className="size-3 shrink-0 text-neg" aria-hidden="true" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="font-medium capitalize">{step.name}</div>
                    {step.error && <div className="mt-0.5 text-neg">{step.error}</div>}
                    {step.fixture_count != null && (
                      <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                        fixture_count: {step.fixture_count}
                      </div>
                    )}
                    {step.detail && typeof step.detail === "object" && (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {Object.entries(step.detail as Record<string, unknown>).map(([key, value]) => (
                          <span key={key} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px]">
                            {key}: {formatValue(value)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          {validateResult.coverage && (
            <div className="mt-3 grid grid-cols-2 gap-2 rounded bg-card/50 p-2 text-[11px] sm:grid-cols-3">
              <div>
                <div className="text-muted-foreground">API fixtures</div>
                <div className="font-mono font-medium">{validateResult.coverage.api_fixture_count}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Stored facts</div>
                <div className="font-mono font-medium">{validateResult.coverage.stored_fact_count}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Covered</div>
                <div className="font-mono font-medium">{validateResult.coverage.covered}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Missing</div>
                <div className="font-mono font-medium">{validateResult.coverage.missing_from_store}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Extra</div>
                <div className="font-mono font-medium">{validateResult.coverage.extra_in_store}</div>
              </div>
            </div>
          )}
        </div>
      )}

      <FactKindPills byKind={status?.facts?.by_kind} />
      <QualificationFactsPanel byKind={status?.facts?.by_kind} />
      <QualificationSourcePanel status={status} />
      <PredictionCoveragePanel coverage={coverage} error={coverageError} />

      <div className="divide-y divide-border border-y border-border">
        {rows.map((row) => {
          const previewBusy = running?.mode === row.mode && running.action === "preview";
          const importBusy = running?.mode === row.mode && running.action === "import";
          return (
            <div key={row.mode} className="grid gap-3 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  {row.ready ? (
                    <CheckCircle2 className="size-3.5 text-pos" aria-hidden="true" />
                  ) : (
                    <AlertTriangle className="size-3.5 text-muted-foreground" aria-hidden="true" />
                  )}
                  <span className="text-sm font-medium">{row.label}</span>
                  <span
                    className={cn(
                      "rounded-md border px-2 py-0.5 text-[11px] font-medium",
                      row.ready
                        ? "border-pos/40 bg-pos/10 text-pos"
                        : row.configured
                          ? "border-warn/40 bg-warn/10 text-warn"
                          : "border-border bg-secondary text-muted-foreground",
                    )}
                  >
                    {row.ready ? "ready" : row.configured ? "check" : "missing"}
                  </span>
                </div>
                <div className="mt-1 flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="truncate">{row.detail}</span>
                  {row.meta.map((item) => (
                    <span key={item} className="font-mono">{item}</span>
                  ))}
                </div>
                <SourceReferenceList references={row.references} className="mt-2" />
              </div>
              <div className="flex items-center gap-2 md:justify-end">
                <button
                  type="button"
                  aria-label={`${row.label} 预览`}
                  disabled={!row.ready || Boolean(running)}
                  onClick={() => void runSourceAction(row.mode, "preview")}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
                >
                  <Eye className={cn("size-3.5", previewBusy && "animate-pulse")} aria-hidden="true" />
                  预览
                </button>
                <button
                  type="button"
                  aria-label={`${row.label} 导入`}
                  disabled={!row.ready || Boolean(running)}
                  onClick={() => void runSourceAction(row.mode, "import")}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-primary bg-primary/15 px-2 text-xs font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
                >
                  <UploadCloud className={cn("size-3.5", importBusy && "animate-pulse")} aria-hidden="true" />
                  导入
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {completed && (
        <div className="grid gap-3 border-t border-border pt-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-foreground">
              {SOURCE_LABELS[completed.mode]} {completed.action === "preview" ? "preview" : "import"}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {completed.completedAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>
          <RunPills result={completed.result as Record<string, unknown>} />
          <SourceReferenceList references={actionSources} title="Sources" />
          <CallBudget budget={completed.result.call_budget} />
          <ErrorList errors={actionErrors} />
          <FetchList fetches={actionFetches} />
          <SkippedList skipped={actionSkipped} />
        </div>
      )}

      {dryRunResult && <ResolveDryRun result={dryRunResult} />}

      {lastRun && (
        <div className="grid gap-3 border-t border-border pt-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-foreground">最近定时导入</span>
              <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", statusBadge(lastRun.status).cls)}>
                {statusBadge(lastRun.status).label}
              </span>
            </div>
            <span className="text-[11px] text-muted-foreground">
              {fmtDateTime(lastRun.finished_at ?? lastRun.started_at)} · {formatDurationMs(lastRun.duration_ms)}
            </span>
          </div>
          {lastRun.error && (
            <div className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs leading-relaxed text-neg">
              {lastRun.error}
            </div>
          )}
          <RunPills result={lastRunResult} />
          <SourceReferenceList references={lastRunSources} title="Sources" />
          <CallBudget budget={lastBudget} />
          <ErrorList errors={lastRunErrors} />
          <FetchList fetches={lastFetches} />
          <SkippedList skipped={lastSkipped} />
        </div>
      )}
    </section>
  );
}
