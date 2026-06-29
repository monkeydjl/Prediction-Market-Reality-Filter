import type {
  EventRecord,
  TrackedEntry,
  Mover,
  HistorySnapshot,
  Trend,
  SimilarEvent,
} from "./types";
import { getApiBase } from "./env";

// Same-origin in production (FastAPI serves the static export at / and the API
// under /api). In dev, next.config rewrites proxy /api/* to :8000.
const BASE = getApiBase();
const OPERATOR_KEY_STORAGE = "pmrf.operatorApiKey";
const OPERATOR_ID_STORAGE = "pmrf.operatorId";
const GET_CACHE_TTL_MS = 15_000;

const getCache = new Map<string, { expiresAt: number; value: unknown }>();
const inflightGets = new Map<string, Promise<unknown>>();

function pruneExpiredGetCache(now = Date.now()): void {
  for (const [key, cached] of getCache.entries()) {
    if (cached.expiresAt <= now) getCache.delete(key);
  }
}

export function getOperatorApiKey(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(OPERATOR_KEY_STORAGE) ?? "";
}

export function setOperatorApiKey(value: string): void {
  if (typeof window === "undefined") return;
  const key = value.trim();
  if (key) window.sessionStorage.setItem(OPERATOR_KEY_STORAGE, key);
  else window.sessionStorage.removeItem(OPERATOR_KEY_STORAGE);
}

export function getOperatorId(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(OPERATOR_ID_STORAGE) ?? "";
}

export function setOperatorId(value: string): void {
  if (typeof window === "undefined") return;
  const operator = value.trim();
  if (operator) window.sessionStorage.setItem(OPERATOR_ID_STORAGE, operator);
  else window.sessionStorage.removeItem(OPERATOR_ID_STORAGE);
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

async function api<T>(
  path: string,
  init?: RequestInit,
  options: { timeoutMs?: number; acceptStatuses?: number[] } = {},
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const isGet = method === "GET";
  const cacheKey = `${BASE}${path}`;

  if (isGet) {
    pruneExpiredGetCache();
    const cached = getCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return cached.value as T;
    const pending = inflightGets.get(cacheKey);
    if (pending) return pending as Promise<T>;
  }

  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const operatorKey = getOperatorApiKey();
  if (operatorKey && !headers.has("X-API-Key")) headers.set("X-API-Key", operatorKey);
  const operatorId = getOperatorId();
  if (operatorId && !headers.has("X-Operator")) headers.set("X-Operator", operatorId);

  const controller = new AbortController();
  const timeout = globalThis.setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? 60_000,
  );
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
      throw new Error(buildApiErrorMessage(res.status, bodyText));
    }
    const data = await res.json() as T;
    if (isGet) {
      getCache.set(cacheKey, { expiresAt: Date.now() + GET_CACHE_TTL_MS, value: data });
    } else {
      getCache.clear();
    }
    return data;
  })();

  if (isGet) inflightGets.set(cacheKey, request as Promise<unknown>);

  try {
    return await request;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    if (error instanceof TypeError) {
      throw new Error("无法连接到服务器，请检查网络或后端服务状态");
    }
    throw error;
  } finally {
    if (isGet) inflightGets.delete(cacheKey);
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

// A committed prediction rendered for human review (M5 decision report).
// Mirrors decision_report_service.build_decision_report.
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
}

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
    calibration_n?: number;
    predictions?: Record<string, number>;
  };
  storage?: {
    loop_db_schema_versions?: Record<string, number>;
  };
  calibration?: PredictionCalibration;
}

// ── M6 Simulated trades ────────────────────────────────────────────

export interface SimTrade {
  trade_id: string;
  event_id: string;
  event_title: string;
  direction: "YES" | "NO";
  entry_prob: number;
  market_prob: number;
  entry_edge: number;
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
  avg_edge_at_entry: number | null;
  by_direction: Record<string, {
    total: number; wins: number; win_rate: number;
    avg_pnl: number; total_pnl: number;
  }>;
  by_decision: Record<string, {
    total: number; wins: number; win_rate: number;
    avg_pnl: number;
  }>;
}

export interface WorldCupSourceFetch {
  kind?: string;
  source_url?: string;
  status?: string;
  duration_ms?: number;
  error?: string;
}

export interface WorldCupSkippedSource {
  kind?: string;
  source_url?: string;
  reason?: string;
  required_calls?: number;
  remaining_calls?: number;
}

export interface WorldCupCallBudget {
  fixture_count?: number;
  max_detail_calls?: number;
  enabled_detail_feeds?: string[];
  detail_calls_used?: number;
  detail_calls_skipped?: number;
  detail_calls_remaining?: number;
}

export interface WorldCupRunSummary {
  status?: string;
  duration_ms?: number;
  source_count?: number;
  converted_fact_count?: number;
  skipped_source_count?: number;
  source_fetch_count?: number;
  source_fetch_duration_ms?: number;
}

export interface WorldCupFileConfig {
  configured?: boolean;
  path?: string;
  exists?: boolean;
}

export interface WorldCupUrlConfig {
  configured?: boolean;
  source_url?: string;
}

export interface WorldCupFeedConfig extends WorldCupUrlConfig {
  kind?: string;
  source?: string;
  observed_at?: string;
}

export interface WorldCupDataSourceStatus {
  facts?: {
    count?: number;
    by_kind?: Record<string, number>;
    last_updated?: string | null;
  };
  configured_sources?: {
    data_file?: WorldCupFileConfig;
    bundle_file?: WorldCupFileConfig;
    bundle_url?: WorldCupUrlConfig;
    feeds?: WorldCupFeedConfig[];
    api_football?: {
      configured?: boolean;
      base_url?: string;
      league_id?: string;
      season?: string;
      fetch_events?: boolean;
      fetch_lineups?: boolean;
      fetch_statistics?: boolean;
      max_detail_calls?: number;
    };
    sportmonks?: {
      configured?: boolean;
      feeds?: WorldCupFeedConfig[];
    };
  };
  scheduled_import?: {
    enabled?: boolean;
    mode?: string;
    replace?: boolean;
    hour_utc?: number;
    minute_utc?: number;
  };
  matchday_refresh?: {
    enabled?: boolean;
    interval_minutes?: number;
    window_hours?: number;
  };
  runs?: {
    world_cup_source_bundle_import?: LoopRun | null;
    world_cup_matchday_refresh?: LoopRun | null;
  };
}

export type WorldCupDataSourceActionMode =
  | "data_file"
  | "bundle_file"
  | "bundle_url"
  | "feeds"
  | "api_football"
  | "sportmonks";

export interface WorldCupDataSourceActionResult {
  provider?: string;
  source_count?: number;
  converted_fact_count?: number;
  imported?: number;
  error_count?: number;
  total?: number;
  replace?: boolean;
  source_url?: string;
  source_file?: string;
  source_feeds?: WorldCupFeedConfig[];
  skipped_source_count?: number;
  skipped_sources?: WorldCupSkippedSource[];
  source_fetch_count?: number;
  source_fetches?: WorldCupSourceFetch[];
  call_budget?: WorldCupCallBudget;
  run?: WorldCupRunSummary;
  errors?: unknown[];
}

export interface WorldCupResolveMatch {
  event_id?: string;
  event_title?: string;
  actual_outcome?: number | null;
  confidence?: number | null;
  reason?: string;
  facts?: string[];
  result?: string;
}

export interface WorldCupResolveResult {
  status?: string;
  dry_run?: boolean;
  resolved_count?: number;
  pending_count?: number;
  checked_count?: number;
  unresolved_events?: number;
  matches?: WorldCupResolveMatch[];
}

export interface WorldCupApiFootballConnectionResult {
  ok: boolean;
  account?: { firstname?: string; lastname?: string; email?: string };
  subscription?: { plan?: string; active?: boolean; end?: string | null };
  requests_today?: number;
  requests_limit?: number;
  error?: string | null;
}

export interface WorldCupSportmonksConnectionResult {
  ok: boolean;
  feed_tested?: string;
  feed_url?: string;
  item_count?: number;
  rate_limit?: {
    remaining?: number;
    limit?: number;
    resets_at?: number;
  } | null;
  error?: string | null;
}

export interface WorldCupPipelineValidateResult {
  ok?: boolean;
  steps?: Array<{
    name: string;
    ok: boolean;
    detail?: Record<string, unknown>;
    error?: string;
  }>;
  coverage?: {
    api_fixture_count: number;
    stored_fact_count: number;
    covered: number;
    missing_from_store: number;
    missing_ids_sample: string[];
    extra_in_store: number;
  };
  summary?: string;
  error?: string | null;
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
    brier_score: number;
    skill_score: number;
    grade: string;
    segment_min_samples?: number | null;
    qualified?: boolean;
  }>;
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
}

const WORLD_CUP_DATA_SOURCE_ACTION_PATHS: Record<WorldCupDataSourceActionMode, string> = {
  data_file: "/events/sports/world-cup/data/source",
  bundle_file: "/events/sports/world-cup/data/bundle/source",
  bundle_url: "/events/sports/world-cup/data/bundle/url",
  feeds: "/events/sports/world-cup/data/bundle/feeds",
  api_football: "/events/sports/world-cup/data/bundle/api-football",
  sportmonks: "/events/sports/world-cup/data/bundle/sportmonks",
};

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
    return api<{
      events: TrackedEntry[];
      count?: number;
      total?: number;
      limit?: number;
      offset?: number;
    }>(`/events/?${params.toString()}`);
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
    }),

  similar: (id: string) =>
    api<{ similar: SimilarEvent[]; count?: number }>(
      `/events/${encodeURIComponent(id)}/similar`
    ),

  movers: (limit = 10) =>
    api<{ movers: Mover[]; count?: number }>(`/events/movers?limit=${limit}`),

  calibration: () =>
    api<{
      overall: CalibrationAgg;
      by_source: Record<string, CalibrationAgg>;
      by_base_rate_category: Record<string, CalibrationAgg>;
    }>("/events/calibration"),

  // M5 opportunity surface. Defaults to act + watch + provisional_act; pass "act" to narrow.
  openDecisions: (decision?: "act" | "watch" | "provisional_act", limit = 50) =>
    api<{ count: number; decisions: DecisionReport[] }>(
      `/events/decisions/open?limit=${limit}${decision ? `&decision=${decision}` : ""}`,
    ),

  decision: (id: string) =>
    api<DecisionReport>(`/events/${encodeURIComponent(id)}/decision`),

  // M5 fresh-edge surface (recent, holding-near-peak divergences).
  freshEdges: (limit = 10) =>
    api<{ count: number; edges: FreshEdge[] }>(`/events/edges/fresh?limit=${limit}`),

  // Delete all event data (requires write key).
  resetData: () =>
    api<{ message: string; cleared: Record<string, number | string> }>(
      "/events/reset",
      { method: "POST" },
    ),

  edgeMonitor: (limit = 50) =>
    api<{ count: number; classification: string; edges: FreshEdge[] }>(
      `/events/edges/fresh?limit=${limit}&classification=all&include_series=true`,
    ),

  // M2/M5 act-only prediction calibration scorecard.
  predictionCalibration: () =>
    api<PredictionCalibration>("/events/predictions/calibration"),

  recentPredictions: (limit = 50) =>
    api<{ predictions: PredictionRecord[] }>(`/events/predictions/recent?limit=${limit}`),

  loopStatus: () =>
    api<LoopStatus>("/events/loop/status"),

  discoverStatus: () =>
    api<Record<string, unknown>>("/events/discover/status"),

  // M6 simulated trades (paper trading)
  tradeStats: () =>
    api<TradeStats>("/events/trades/stats"),
  openTrades: () =>
    api<{ count: number; trades: SimTrade[] }>("/events/trades/open"),
  closedTrades: (limit = 50) =>
    api<{ count: number; trades: SimTrade[] }>(`/events/trades/closed?limit=${limit}`),

  pendingLinks: () =>
    api<{ pending: PendingLink[] }>("/events/links/pending"),

  verifyLink: (id: string, contractId: string) =>
    api<PendingLink>(`/events/${encodeURIComponent(id)}/link/verify`, {
      method: "POST",
      body: JSON.stringify({ contract_id: contractId }),
    }),

  worldCupDataSourcesStatus: () =>
    api<WorldCupDataSourceStatus>("/events/sports/world-cup/data/sources/status"),

  worldCupDataSourcePreview: (mode: WorldCupDataSourceActionMode) =>
    api<WorldCupDataSourceActionResult>(
      `${WORLD_CUP_DATA_SOURCE_ACTION_PATHS[mode]}/preview`,
      { method: "POST" },
      { timeoutMs: 180_000 },
    ),

  worldCupDataSourceImport: (mode: WorldCupDataSourceActionMode, replace = false) =>
    api<WorldCupDataSourceActionResult>(
      `${WORLD_CUP_DATA_SOURCE_ACTION_PATHS[mode]}/import?replace=${replace}`,
      { method: "POST" },
      { timeoutMs: 180_000 },
    ),

  worldCupResolveDryRun: (limit = 200) =>
    api<WorldCupResolveResult>(
      `/events/sports/world-cup/resolve?dry_run=true&limit=${limit}`,
      { method: "POST" },
      { timeoutMs: 180_000 },
    ),

  worldCupApiFootballTest: () =>
    api<WorldCupApiFootballConnectionResult>(
      "/events/sports/world-cup/data/bundle/api-football/test",
      { method: "POST" },
      { timeoutMs: 15_000 },
    ),

  worldCupApiFootballValidate: () =>
    api<WorldCupPipelineValidateResult>(
      "/events/sports/world-cup/data/bundle/api-football/validate",
      { method: "POST" },
      { timeoutMs: 30_000 },
    ),

  worldCupSportmonksTest: () =>
    api<WorldCupSportmonksConnectionResult>(
      "/events/sports/world-cup/data/bundle/sportmonks/test",
      { method: "POST" },
      { timeoutMs: 15_000 },
    ),

  worldCupSportmonksValidate: () =>
    api<WorldCupPipelineValidateResult>(
      "/events/sports/world-cup/data/bundle/sportmonks/validate",
      { method: "POST" },
      { timeoutMs: 30_000 },
    ),
};
