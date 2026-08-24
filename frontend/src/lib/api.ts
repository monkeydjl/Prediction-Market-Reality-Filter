import type {
  EventRecord,
  TrackedEntry,
  Mover,
  HistorySnapshot,
  Trend,
  SimilarEvent,
} from "./types";
import type {
  DecisionTimelineResponse as GeneratedDecisionTimelineResponse,
  AutoResolveResponse as GeneratedAutoResolveResponse,
  EventListResponse as GeneratedEventListResponse,
  EventMoversResponse as GeneratedEventMoversResponse,
  EventHistoryResponse as GeneratedEventHistoryResponse,
} from "./generated-types";
import { getApiBase } from "./env";
import { applyOperatorAuthHeaders } from "./operator-credentials";

export {
  applyOperatorAuthHeaders,
  buildOperatorAuthHeaders,
  clearOperatorCredentials,
  getOperatorApiKey,
  getOperatorCredentialsSnapshot,
  getOperatorId,
  hasOperatorApiKey,
  setOperatorApiKey,
  setOperatorId,
} from "./operator-credentials";

// Same-origin in production (FastAPI serves the static export at / and the API
// under /api). In dev, next.config rewrites proxy /api/* to :8000.
const BASE = getApiBase();
const GET_CACHE_TTL_MS = 15_000;

const getCache = new Map<string, { expiresAt: number; value: unknown }>();

/** A GET that is currently in flight, shared by every caller that joins it. */
type Inflight = { promise: Promise<unknown>; waiters: number; abort: () => void };

const inflightGets = new Map<string, Inflight>();

/**
 * Await a shared in-flight GET under this caller's own deadline.
 *
 * Joined callers used to be handed the originator's promise directly, so they
 * inherited its AbortController and its timer: a caller that joined one second
 * ago failed with 请求超时 the moment the originator's 60s budget ran out. Each
 * caller now times its own wait, and the underlying fetch is aborted only once
 * no caller is waiting on it any more.
 */
function awaitInflight<T>(entry: Inflight, timeoutMs: number): Promise<T> {
  entry.waiters += 1;
  return new Promise<T>((resolve, reject) => {
    let done = false;
    const timer = globalThis.setTimeout(() => {
      if (done) return;
      done = true;
      entry.waiters -= 1;
      if (entry.waiters === 0) entry.abort();
      reject(new Error("请求超时，请稍后重试"));
    }, timeoutMs);
    const settle = (finish: () => void) => {
      if (done) return;
      done = true;
      globalThis.clearTimeout(timer);
      entry.waiters -= 1;
      finish();
    };
    entry.promise.then(
      (value) => settle(() => resolve(value as T)),
      (error) => settle(() => reject(error)),
    );
  });
}

function pruneExpiredGetCache(now = Date.now()): void {
  for (const [key, cached] of getCache.entries()) {
    if (cached.expiresAt <= now) getCache.delete(key);
  }
}

function detailToText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg?: unknown }).msg ?? "");
        }
        return "";
      })
      .filter(Boolean)
      .join("；");
  }
  return "";
}

function parseErrorBody(bodyText: string): string {
  const text = bodyText.trim();
  if (!text) return "";
  try {
    const data = JSON.parse(text) as { detail?: unknown; message?: unknown };
    return detailToText(data.detail) || detailToText(data.message) || text;
  } catch {
    return text;
  }
}

export function buildApiErrorMessage(status: number, bodyText: string): string {
  const text = parseErrorBody(bodyText);

  if (status >= 500) {
    return "服务器暂时不可用，请稍后重试";
  }

  if (status === 404) {
    return "请求的资源不存在";
  }

  if (status === 401 || status === 403) {
    return "当前请求未获授权：请先在右上角「授权」中输入 backend/.env 里的 API_WRITE_KEY";
  }

  if (status === 400) {
    return text || "请求参数无效";
  }

  return text || `请求失败（HTTP ${status}）`;
}

/** Error thrown by all fetch wrappers for non-2xx HTTP responses. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Shared catch-block handler for fetch wrappers. Never returns. */
export function handleFetchError(error: unknown): never {
  if (error instanceof Error && error.name === "AbortError") {
    throw new Error("请求超时，请稍后重试");
  }
  if (error instanceof TypeError) {
    throw new Error("无法连接到服务器，请检查网络或后端服务状态");
  }
  throw error;
}

async function api<T>(
  path: string,
  init?: RequestInit,
  options: {
    timeoutMs?: number;
    acceptStatuses?: number[];
    invalidateGetCache?: boolean;
    cacheGet?: boolean;
  } = {},
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const isGet = method === "GET";
  const shouldCacheGet = isGet && options.cacheGet !== false;
  const cacheKey = `${BASE}${path}`;
  const timeoutMs = options.timeoutMs ?? 60_000;

  if (shouldCacheGet) {
    pruneExpiredGetCache();
    const cached = getCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return cached.value as T;
    const pending = inflightGets.get(cacheKey);
    if (pending) {
      try {
        return await awaitInflight<T>(pending, timeoutMs);
      } catch (error) {
        return handleFetchError(error);
      }
    }
  }

  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  applyOperatorAuthHeaders(headers);

  const controller = new AbortController();
  const abort = () => controller.abort();
  init?.signal?.addEventListener("abort", abort, { once: true });

  const request = (async () => {
    const res = await fetch(BASE + path, {
      ...init,
      headers,
      signal: controller.signal,
    });
    if (!res.ok && !options.acceptStatuses?.includes(res.status)) {
      const bodyText = await res.text();
      throw new ApiError(res.status, buildApiErrorMessage(res.status, bodyText));
    }
    const data = await res.json() as T;
    if (isGet) {
      if (shouldCacheGet) {
        getCache.set(cacheKey, { expiresAt: Date.now() + GET_CACHE_TTL_MS, value: data });
      }
    } else if (options.invalidateGetCache !== false) {
      getCache.clear();
    }
    return data;
  })();

  if (shouldCacheGet) {
    const entry: Inflight = { promise: request, waiters: 0, abort };
    inflightGets.set(cacheKey, entry);
    // Deregister when the fetch settles rather than when this caller stops
    // waiting: a caller that gives up early must not send the next one off to
    // start a duplicate request. This handler also owns the rejection when no
    // waiter is left to observe it.
    void request.then(
      () => inflightGets.delete(cacheKey),
      () => inflightGets.delete(cacheKey),
    );
    try {
      return await awaitInflight<T>(entry, timeoutMs);
    } catch (error) {
      return handleFetchError(error);
    } finally {
      init?.signal?.removeEventListener("abort", abort);
    }
  }

  const timeout = globalThis.setTimeout(abort, timeoutMs);

  try {
    return await request;
  } catch (error) {
    return handleFetchError(error);
  } finally {
    globalThis.clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abort);
  }
}

export interface CalibrationAgg {
  brier_score: number | null;
  skill_score: number | null;
  grade: string;
  n: number;
}

export interface CategoryCoverage {
  total: number;
  classified: number;
  unknown: number;
  unknown_rate: number | null;
}

// A committed prediction rendered for human review (M5 decision report).
// Mirrors decision_report_service.build_decision_report.
/** Quality overlays attached by analyze_event (optional / flag-gated). */
export interface DecisionQualityOverlay {
  displayed_direction?: string | null;
  downgrade_reason?: string | null;
  decision_rationale_zh?: string | null;
  [key: string]: unknown;
}

export interface MarketQualityOverlay {
  score?: number | null;
  downgrade_reason?: string | null;
  [key: string]: unknown;
}

export interface SourceReliabilityOverlay {
  overall_score?: number | null;
  downgrade_reason?: string | null;
  [key: string]: unknown;
}

export interface DecisionReport {
  event_id: string;
  event: { title: string; title_zh?: string; summary: string };
  probability: {
    estimated: number | null;
    baseline: number | null;
    change: number | null;
    direction: string | null;
  };
  market_view: {
    market_probability: number | null;
    platform: string;
    liquidity: number | null;
    volume: number | null;
  };
  edge: { raw: number | null; adjusted: number | null; trust: number | null };
  diagnosis: {
    qualified: boolean | null;
    segment_n: number | null;
    segment_min_samples?: number | null;
    segment_skill: number | null;
    liquidity_factor: number | null;
    reason: string;
  };
  confidence: { level: string | null; score: number | null; confidence: number | null };
  recommendation: { decision: string | null; action: string; calibration_status?: string | null };
  risk: { level: string | null; flags: string[] };
  category: string | null;
  status: string | null;
  actionable_recommendation?: {
    direction: string;
    confidence: string;
    suggested_allocation_pct: number;
    edge: number;
    risk_level: string;
    rationale: string;
    calibration_status: string;
  } | null;
  /** Phase quality overlays — present when corresponding flags are on. */
  decision_quality?: DecisionQualityOverlay | null;
  market_quality?: MarketQualityOverlay | null;
  source_reliability?: SourceReliabilityOverlay | null;
  final_displayed_direction?: string | null;
  final_downgrade_reason?: string | null;
}

// Re-export generated response types (replaces hand-written inline versions).
// Source of truth: backend/app/models/event.py via generate_types.py.
export type DecisionTimelineResponse = GeneratedDecisionTimelineResponse;
export type AutoResolveResponse = GeneratedAutoResolveResponse;
export type EventListResponse = GeneratedEventListResponse;
export type EventMoversResponse = GeneratedEventMoversResponse;
export type EventHistoryResponse = GeneratedEventHistoryResponse;

// An event's edge trajectory + freshness (M5 fresh-edge surface).
// Mirrors trend_analysis_service.analyze_edge_trajectory.
export interface EdgeTrajectory {
  observations: number;
  latest_edge: number | null;
  first_edge: number | null;
  peak_edge: number | null;
  net_edge_change: number;
  recent_edge_change: number;
  age_hours: number | null;
  freshness_band: string;
  classification: string; // no_data | stale | closed | fresh | decaying
}

export interface EdgePoint {
  timestamp?: string | null;
  estimated: number;
  baseline: number;
  edge: number;
}

export interface FreshEdge {
  event_id: string;
  event_title: string;
  event_title_zh?: string;
  edge: EdgeTrajectory;
  series?: EdgePoint[];
}

export interface LoopRun {
  id?: string;
  job?: string;
  job_name?: string;
  status: string;
  started_at?: string;
  finished_at?: string | null;
  duration_ms?: number | null;
  duration_seconds?: number | null;
  error?: string | null;
  result?: Record<string, unknown>;
  details?: Record<string, unknown>;
}

export interface LoopStatus {
  scheduler?: { running?: boolean | null };
  runs?: Record<string, LoopRun | null>;
  recent_runs?: LoopRun[];
  counts?: {
    events?: number;
    resolved_events?: number;
    open_opportunities?: number;
    pending_links?: number;
    orphan_predictions?: number;
    dangling_predictions?: number;
    dangling_links?: number;
    // E2: the two keys above cover two tables; five carry an event_id. Read
    // `dangling_refs` for the total and `dangling_by_table` for where they are.
    dangling_refs?: number;
    dangling_by_table?: Record<string, number>;
    calibration_n?: number;
    predictions?: Record<string, number>;
  };
  storage?: {
    loop_db_schema_versions?: Record<string, number>;
  };
  calibration?: PredictionCalibration;
}

export interface LlmDiagnosticRoute {
  provider: string;
  models: string[];
  provider_configured: boolean;
  api_key_configured: boolean;
  base_url_configured: boolean;
}

export interface LlmDiagnosticTask {
  task: string;
  setting: string;
  route_source: string;
  configured: boolean;
  routes: LlmDiagnosticRoute[];
}

export interface LlmDiagnostics {
  tasks: LlmDiagnosticTask[];
  configured_task_count: number;
  unconfigured_task_count: number;
}

// ── M6 Simulated trades ────────────────────────────────────────────

export interface SimTrade {
  trade_id: string;
  event_id: string;
  event_title: string;
  direction: "YES" | "NO";
  entry_prob: number;
  market_prob: number;
  /** EIP raw edge: AI% − market% (0–100 pp), same as predictions.raw_edge */
  entry_edge: number;
  /** Alias of entry_edge when returned by API */
  raw_edge?: number;
  /** Edge in favor of position: YES→raw, NO→−raw */
  directional_edge?: number;
  edge_definition?: string;
  entry_time: string;
  position_pct: number;
  confidence: number | null;
  trust_weight: number | null;
  decision: string;
  exit_prob: number | null;
  exit_market: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  actual_outcome: number | null;
  pnl_pct: number | null;
  is_win: number | null;
  status: "open" | "closed";
}

export interface TradeStats {
  total_closed: number;
  win_rate: number | null;
  total_pnl_pct: number;
  avg_pnl_pct: number | null;
  /** mean |raw_edge| on closed trades (legacy key) */
  avg_edge_at_entry: number | null;
  avg_raw_edge_at_entry?: number | null;
  avg_directional_edge_at_entry?: number | null;
  edge_definition?: Record<string, string>;
  by_direction: Record<string, {
    total: number; wins: number; win_rate: number;
    avg_pnl: number; total_pnl: number;
  }>;
  by_decision: Record<string, {
    total: number; wins: number; win_rate: number;
    avg_pnl: number;
  }>;
}

export interface ApiOverview {
  system: string;
  version: string;
  app: string;
  docs: string;
  endpoints: Record<string, string>;
}

export interface ApiHealth {
  status: "ok" | "degraded" | string;
  version: string;
  scheduler_running?: boolean | null;
  failed_runs?: string[];
  loop: LoopStatus;
}

export interface PendingLink {
  event_id: string;
  id?: string;
  event_title?: string;
  event_title_zh?: string;
  event_summary?: string;
  event_resolution_criteria?: string;
  market_name?: string;
  contract_id?: string;
  market_question?: string;
  resolution_criteria?: string;
  link_method?: string;
  link_confidence?: number;
  linked_at?: string;
  verified?: boolean;
}

export interface AutoResolveMatch {
  event_id?: string;
  event_title?: string;
  matched_to?: string;
  market_name?: string;
  contract_id?: string;
  actual_outcome?: number | null;
  match_score?: number;
  result?: string;
}

export interface AutoResolveResult {
  status?: string;
  dry_run?: boolean;
  resolved_count?: number;
  pending_count?: number;
  invalid_count?: number;
  checked_count?: number;
  unresolved_events?: number;
  reconciled_count?: number;
  matches?: AutoResolveMatch[];
  by_source?: Record<string, number>;
}

// The act-only prediction calibration scorecard (M2/M5).
// Mirrors prediction_store.calibration_summary.
export interface PredictionCalibration {
  n: number;
  brier_score: number | null;
  grade: string;
  mean_raw_edge: number | null;
  realized_edge: number | null;
  directional_hit_rate: number | null;
  segment_min_samples?: number | null;
  by_category: Record<string, { n: number; brier_score: number; skill_score: number; grade: string }>;
  segments?: Record<string, {
    n: number;
    brier_score: number | null;
    skill_score: number | null;
    grade: string;
    market_brier_score?: number | null;
    market_relative_skill?: number | null;
    segment_min_samples?: number | null;
    qualified?: boolean;
  }>;
}

/** Phase 3 edge × confidence diagnostic (act + watch + provisional). */
export interface CalibrationBucketCell {
  n: number;
  brier_score: number | null;
  direction_correct_rate: number | null;
}

export interface CalibrationBucketSummary {
  n: number;
  by_edge_bucket: Record<string, CalibrationBucketCell>;
  by_confidence_bucket: Record<string, CalibrationBucketCell>;
  by_edge_x_confidence: Record<string, CalibrationBucketCell>;
}

// ── Quality operations metrics (Plan 2 §1.6) ───────────────────────────────
// Mirrors backend /api/quality-metrics/{summary,timeseries,anomalies,drift}.

export interface QualityMetricsSummary {
  timeframe: string;
  counts: {
    events: number;
    resolved_events: number;
    with_decision_quality: number;
    with_market_quality: number;
    with_source_reliability: number;
    with_llm_telemetry: number;
  };
  final_direction: Record<string, number>;
  consensus: Record<string, number>;
  downgrade: {
    final_downgrade_reason_present: number;
    build_errors: {
      decision_quality: number;
      market_quality: number;
      source_reliability: number;
    };
  };
  market_quality: {
    count: number;
    wide_spread_flag_count: number;
    thin_market_flag_count: number;
    score_avg: number | null;
    score_min: number | null;
    score_max: number | null;
  };
  source_reliability: {
    count: number;
    overall_score_avg: number | null;
    source_count_avg: number | null;
    domain_diversity_avg: number | null;
  };
  llm_telemetry: {
    count: number;
    degraded_mode_count: number;
    estimated_token_cost_total: number;
  };
  calibration: Record<string, unknown>;
  calibration_buckets: Record<string, unknown>;
  scheduler: {
    last_runs: Record<string, {
      status: string | null;
      started_at: string | null;
      finished_at: string | null;
      duration_ms: number | null;
    } | null>;
    recent_failed_count: number;
    recent_runs_count: number;
  };
}

export interface QualityMetricsAnomaly {
  code: string;
  severity: "high" | "medium" | "low" | string;
  detail: unknown;
  /** Sample event ids for operator deep-links (when available). */
  event_ids?: string[];
  /** Suggested operator page (e.g. /history, /quality). */
  href?: string | null;
}

export interface SchedulerTimeseriesPoint {
  job_name: string | null;
  status: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface QualityMetricsDrift {
  recent_window_n: number;
  drift: {
    drift_score: number | null;
    recent_mean: number | null;
    baseline_mean: number | null;
    recent_n: number;
    baseline_n: number;
  } | null;
  ece: {
    recent: number | null;
    baseline: number | null;
  };
  degraded_mixing: {
    recent_degraded_count: number;
    recent_n: number;
    contaminated: boolean;
  };
  buckets: Record<string, {
    recent: { n: number; brier_score: number | null; direction_correct_rate: number | null };
    baseline: { n: number; brier_score: number | null; direction_correct_rate: number | null };
  }>;
  alerts: QualityMetricsAnomaly[];
  alerts_enabled: boolean;
}

// ── Sliced quality metrics report (NEXT #3) ──────────────────────────────
// Mirrors backend /api/quality-metrics/report. See
// backend/app/services/quality_metrics_report_service.py for the source of
// truth on field names and semantics.

export interface QualityReportBrier {
  brier_score: number | null;
  skill_score: number | null;
  grade: string;
  n: number;
}

export interface QualityReportSlice {
  n: number;
  direction_correct_true: number;
  direction_correct_false: number;
  direction_correct_none: number;
  /** true / (true + false). null when no directional events. */
  direction_accuracy: number | null;
  brier: QualityReportBrier;
}

export interface QualityReportOverview {
  total_resolved: number;
  with_calibration: number;
  missing_calibration: number;
}

export interface CalibrationDeviationRow {
  bucket: string;
  n: number;
  predicted_mean: number | null;
  actual_mean: number | null;
  /** predicted - actual. Positive = overconfident. */
  deviation: number | null;
}

export interface QualityMetricsReport {
  overview: QualityReportOverview;
  by_source_type: Record<string, QualityReportSlice>;
  by_analysis_quality: Record<string, QualityReportSlice>;
  by_edge_bucket: Record<string, QualityReportSlice>;
  by_source_reliability_bucket: Record<string, QualityReportSlice>;
  calibration_deviation: CalibrationDeviationRow[];
  report_errors: { event_id: string; error: string }[];
}

// ── Quality alerts / domain reliability ─────────────────────────────────
// Mirror backend /api/quality-metrics/alerts and /domain-reliability. See
// backend/app/services/quality_alert_service.py and
// backend/app/memory/domain_reliability_store.py for the source of truth.

export interface QualityAlert {
  code: string;
  severity: "high" | "medium";
  scope: "overview" | "slice";
  dimension: string | null;
  slice: string | null;
  metric: string;
  value: number | null;
  threshold: number | null;
  n: number;
}

export interface QualityAlertsResponse {
  alerts: QualityAlert[];
  alert_count: number;
  diagnostics?: {
    insufficient_samples: {
      dimension: string;
      slice: string;
      n: number;
      min_samples: number;
    }[];
  };
}

export interface DomainReliabilityRow {
  domain: string;
  category: string;
  sample_count: number;
  correct_count: number;
  wrong_count: number;
  credibility_sum: number;
  /** correct / sample. null when sample_count is 0. */
  reliability_score: number | null;
  credibility_avg: number | null;
  insufficient_samples: boolean;
  first_seen: string | null;
  last_updated: string | null;
}

export interface DomainReliabilityResponse {
  domains: DomainReliabilityRow[];
  total_domains: number;
  total_rows: number;
}

export interface PredictionRecord {
  id: string;
  event_id: string;
  event_title?: string;
  event_title_zh?: string;
  contract_id?: string;
  platform?: string;
  base_rate_category?: string;
  ai_probability: number;
  market_probability: number;
  raw_edge: number;
  adjusted_edge?: number | null;
  trust?: number | null;
  decision?: string;
  status?: string;
  actual_outcome?: number | null;
  brier_score?: number | null;
  created_at?: string;
  resolved_at?: string | null;
}

export interface EventListFilters {
  q?: string;
  status?: "active" | "tracking" | "watching" | "archived" | "all";
  category?: string;
  sort?: "value" | "delta" | "probability" | "support";
  exclude_expired?: boolean;
  resolved_only?: boolean;
}

export const eventsApi = {
  overview: () =>
    api<ApiOverview>(""),

  health: () =>
    api<ApiHealth>("/health", undefined, { acceptStatuses: [503] }),

  discover: (limit = 2, useCache = false, signal?: AbortSignal) =>
    api<{ events: EventRecord[]; source?: string; count?: number }>(
      `/events/discover?limit=${limit}&use_cache=${useCache}`,
      { signal },
      { timeoutMs: 300_000 },
    ),

  analyze: (body: {
    event_question: string;
    baseline_probability: number;
    news_context?: string;
    volume?: number;
    liquidity?: number;
  }) =>
    api<EventRecord>(
      "/events/analyze",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      { timeoutMs: 180_000 },
    ),

  list: (limit = 50, offset = 0, filters: EventListFilters = {}) => {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
      exclude_expired: String(filters.exclude_expired ?? true),
    });
    if (filters.q) params.set("q", filters.q);
    if (filters.status && filters.status !== "all") params.set("status", filters.status);
    if (filters.category && filters.category !== "all") params.set("category", filters.category);
    if (filters.sort && filters.sort !== "value") params.set("sort", filters.sort);
    if (filters.resolved_only) params.set("resolved_only", "true");
    return api<{
      events: TrackedEntry[];
      count?: number;
      total?: number;
      limit?: number;
      offset?: number;
    }>(`/events/?${params.toString()}`);
  },

  categoryCounts: (filters: Omit<EventListFilters, "category"> = {}) => {
    const params = new URLSearchParams({
      exclude_expired: String(filters.exclude_expired ?? true),
    });
    if (filters.q) params.set("q", filters.q);
    if (filters.status && filters.status !== "all") params.set("status", filters.status);
    if (filters.sort && filters.sort !== "value") params.set("sort", filters.sort);
    if (filters.resolved_only) params.set("resolved_only", "true");
    return api<{ counts: Record<string, number> }>(`/events/category-counts?${params.toString()}`);
  },

  detail: (id: string) =>
    api<TrackedEntry>(`/events/${encodeURIComponent(id)}`),

  setTracking: (id: string, body: { status?: string; priority?: string }) =>
    api<TrackedEntry>(`/events/${encodeURIComponent(id)}/tracking`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  resolveAuto: (limit = 200, dryRun = false) =>
    api<AutoResolveResult>(
      `/events/resolve/auto?limit=${limit}&dry_run=${dryRun}`,
      { method: "POST" },
      { timeoutMs: 180_000 },
    ),

  resolveManual: (
    id: string,
    body: { actual_outcome: number; confidence?: number; notes?: string },
  ) =>
    api<TrackedEntry>(`/events/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  history: (id: string) =>
    api<{ history: HistorySnapshot[]; trend?: Trend; edge?: EdgeTrajectory; count?: number }>(
      `/events/${encodeURIComponent(id)}/history`
    ),

  batchSparklines: (eventIds: string[]) =>
    api<{ sparklines: Record<string, number[]> }>("/events/batch-sparklines", {
      method: "POST",
      body: JSON.stringify({ event_ids: eventIds }),
    }, { invalidateGetCache: false }),

  similar: (id: string) =>
    api<{ similar: SimilarEvent[]; count?: number }>(
      `/events/${encodeURIComponent(id)}/similar`
    ),

  delete: (id: string) =>
    // `stranded_refs` names the loop-DB tables still pointing at this event
    // after the JSON record is gone (E2: no FK spans JSON and SQLite, and
    // nothing prunes those tables). The rows are deliberately kept.
    api<{ event_id: string; message: string; stranded_refs: Record<string, number>; stranded_total: number }>(
      `/events/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    ),

  resolveExpired: () =>
    api<{ total: number; resolved: number; archived: number; parsed_dates: number; message: string }>("/events/resolve-expired", {
      method: "POST",
    }, { timeoutMs: 300_000 }),

  translateAll: (force = false) =>
    api<{ total: number; translated: number; message: string }>(`/events/translate-all?force=${force}`, {
      method: "POST",
    }, { timeoutMs: 300_000 }),

  translateEvent: (id: string, force = false) =>
    api<{ event_id: string; event_title_zh: string; message: string }>(
      `/events/${encodeURIComponent(id)}/translate?force=${force}`,
      { method: "POST" },
      { timeoutMs: 60_000 },
    ),

  movers: (limit = 10) =>
    api<{ movers: Mover[]; count?: number }>(`/events/movers?limit=${limit}`),

  calibration: () =>
    api<{
      overall: CalibrationAgg;
      by_source: Record<string, CalibrationAgg>;
      by_base_rate_category: Record<string, CalibrationAgg>;
      category_coverage: CategoryCoverage;
    }>("/events/calibration"),

  // M5 opportunity surface. Defaults to act + watch + provisional_act; pass "act" to narrow.
  openDecisions: (decision?: "act" | "watch" | "provisional_act", limit = 10, offset = 0) =>
    api<{
      count: number;
      total?: number;
      limit?: number;
      offset?: number;
      decision_totals?: Record<string, number>;
      decisions: DecisionReport[];
    }>(
      `/events/decisions/open?limit=${limit}&offset=${offset}${decision ? `&decision=${decision}` : ""}`,
    ),

  decision: (id: string) =>
    api<DecisionReport>(`/events/${encodeURIComponent(id)}/decision`),

  decisionTimeline: (id: string, limit?: number) =>
    api<DecisionTimelineResponse>(
      `/events/${encodeURIComponent(id)}/decision-timeline` +
      (limit ? `?limit=${limit}` : "")
    ),

  // M5 fresh-edge surface (recent, holding-near-peak divergences).
  freshEdges: (limit = 10) =>
    api<{ count: number; edges: FreshEdge[] }>(`/events/edges/fresh?limit=${limit}`),

  // Delete all event data (requires write key).
  resetData: () =>
    api<{ message: string; cleared: Record<string, number | string> }>(
      "/events/reset",
      { method: "POST" },
    ),

  edgeMonitor: (limit = 10, offset = 0, classification = "all") =>
    api<{
      count: number;
      total?: number;
      limit?: number;
      offset?: number;
      classification: string;
      classification_totals?: Record<string, number>;
      edges: FreshEdge[];
    }>(
      `/events/edges/fresh?limit=${limit}&offset=${offset}&classification=${classification}&include_series=true`,
    ),

  // M2/M5 act-only prediction calibration scorecard.
  predictionCalibration: () =>
    api<PredictionCalibration>("/events/predictions/calibration"),

  predictionCalibrationBuckets: () =>
    api<CalibrationBucketSummary>("/events/predictions/calibration/buckets"),

  recentPredictions: (limit = 10, offset = 0) =>
    api<{ predictions: PredictionRecord[]; count?: number; total?: number; limit?: number; offset?: number }>(
      `/events/predictions/recent?limit=${limit}&offset=${offset}`,
    ),

  loopStatus: () =>
    api<LoopStatus>("/events/loop/status"),

  llmDiagnostics: () =>
    api<LlmDiagnostics>("/llm/diagnostics"),

  discoverStatus: () =>
    api<Record<string, unknown>>("/events/discover/status", undefined, { cacheGet: false }),

  // M6 simulated trades (paper trading)
  tradeStats: () =>
    api<TradeStats>("/events/trades/stats"),
  openTrades: (limit = 10, offset = 0) =>
    api<{ count: number; total?: number; limit?: number; offset?: number; trades: SimTrade[] }>(
      `/events/trades/open?limit=${limit}&offset=${offset}`,
    ),
  closedTrades: (limit = 10, offset = 0) =>
    api<{ count: number; total?: number; limit?: number; offset?: number; trades: SimTrade[] }>(
      `/events/trades/closed?limit=${limit}&offset=${offset}`,
    ),

  pendingLinks: () =>
    api<{ pending: PendingLink[] }>("/events/links/pending"),

  verifyLink: (id: string, contractId: string) =>
    api<PendingLink>(`/events/${encodeURIComponent(id)}/link/verify`, {
      method: "POST",
      body: JSON.stringify({ contract_id: contractId }),
    }),
};

export const qualityMetricsApi = {
  summary: (timeframe: "24h" | "7d" | "all" = "24h") =>
    api<QualityMetricsSummary>(`/quality-metrics/summary?timeframe=${timeframe}`),

  timeseries: (window: "24h" | "7d" | "30d" = "7d") =>
    api<{ window: string; points: SchedulerTimeseriesPoint[] }>(
      `/quality-metrics/timeseries?window=${window}`,
    ),

  anomalies: () =>
    api<{ count: number; anomalies: QualityMetricsAnomaly[] }>(
      "/quality-metrics/anomalies",
    ),

  drift: () =>
    api<QualityMetricsDrift>("/quality-metrics/drift"),

  report: (params?: { limit?: number; sample?: number }) => {
    const qs = new URLSearchParams();
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.sample != null) qs.set("sample", String(params.sample));
    const tail = qs.toString();
    return api<QualityMetricsReport>(
      `/quality-metrics/report${tail ? `?${tail}` : ""}`,
    );
  },

  alerts: (params?: {
    limit?: number;
    sample?: number;
    includeInsufficientSamples?: boolean;
  }) => {
    const qs = new URLSearchParams();
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.sample != null) qs.set("sample", String(params.sample));
    if (params?.includeInsufficientSamples) {
      qs.set("include_insufficient_samples", "true");
    }
    const tail = qs.toString();
    return api<QualityAlertsResponse>(
      `/quality-metrics/alerts${tail ? `?${tail}` : ""}`,
    );
  },

  domainReliability: (params?: {
    domain?: string;
    category?: string;
    minSamples?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.domain) qs.set("domain", params.domain);
    if (params?.category) qs.set("category", params.category);
    if (params?.minSamples != null) qs.set("min_samples", String(params.minSamples));
    const tail = qs.toString();
    return api<DomainReliabilityResponse>(
      `/quality-metrics/domain-reliability${tail ? `?${tail}` : ""}`,
    );
  },
};

// ── Review queue ────────────────────────────────────────────────────────
// Mirrors backend /api/review-queue. The action vocabulary is locked by
// backend/app/memory/review_queue_store.py — keep the union in sync with it.

export type ReviewQueueStatus = "pending" | "resolved";

export type ReviewQueueAction =
  | "confirm"
  | "override"
  | "request_more_evidence"
  | "mark_bad_source"
  | "mark_bad_resolution";

export interface ReviewQueueItem {
  item_id: string;
  event_id: string;
  trigger: string;
  severity: string;
  /** Urgency order (ERROR > WARN); -1 for a severity the store does not know. */
  severity_rank: number;
  reason: string;
  context: Record<string, unknown>;
  status: ReviewQueueStatus;
  reviewer: string | null;
  reviewer_decision: string | null;
  reviewer_note: string;
  created_at: string;
  /** Hours waited. Pending items only — the store omits it for resolved rows. */
  age_hours?: number | null;
  resolved_at: string | null;
}

export interface ReviewQueueAuditEntry {
  audit_id: number;
  item_id: string;
  reviewer: string;
  action: string;
  note: string;
  acted_at: string;
}

export interface ReviewQueueListResponse {
  items: ReviewQueueItem[];
  /** Items in this response. */
  count: number;
  /** Items matching the filter before ``limit`` truncated it. */
  total: number;
  truncated: boolean;
  status: ReviewQueueStatus;
}

export interface ReviewQueueSlaSeverity {
  count: number;
  oldest_age_hours: number | null;
  breached: number;
  /** Budget for this severity; null when the severity has none. */
  sla_hours: number | null;
}

/** Counts and ages only — no reasons, no event text. See /api/review-queue/sla. */
export interface ReviewQueueSlaSummary {
  pending_total: number;
  oldest_age_hours: number | null;
  oldest_item_id: string | null;
  breached_total: number;
  /** Pending items whose severity has no budget; they can never breach. */
  unknown_severity: number;
  sla_hours: Record<string, number>;
  by_severity: Record<string, ReviewQueueSlaSeverity>;
  by_trigger: Record<string, number>;
}

export interface ReviewQueueAuditResponse {
  audit: ReviewQueueAuditEntry[];
  count: number;
  item_id: string;
}

export const reviewQueueApi = {
  list: (params?: {
    status?: ReviewQueueStatus;
    trigger?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.trigger) qs.set("trigger", params.trigger);
    if (params?.limit != null) qs.set("limit", String(params.limit));
    const tail = qs.toString();
    return api<ReviewQueueListResponse>(
      `/review-queue${tail ? `?${tail}` : ""}`,
    );
  },

  sla: () => api<{ sla: ReviewQueueSlaSummary }>("/review-queue/sla"),

  audit: (itemId: string) =>
    api<ReviewQueueAuditResponse>(
      `/review-queue/${encodeURIComponent(itemId)}/audit`,
    ),

  takeAction: (
    itemId: string,
    body: { reviewer: string; action: ReviewQueueAction; note?: string },
  ) =>
    api<{ item: ReviewQueueItem }>(
      `/review-queue/${encodeURIComponent(itemId)}/action`,
      { method: "POST", body: JSON.stringify(body) },
    ),
};
