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
const GET_CACHE_TTL_MS = 15_000;

const getCache = new Map<string, { expiresAt: number; value: unknown }>();
const inflightGets = new Map<string, Promise<unknown>>();

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
    return "当前请求未获授权";
  }

  if (status === 400) {
    return text || "请求参数无效";
  }

  return text || `请求失败（HTTP ${status}）`;
}

async function api<T>(
  path: string,
  init?: RequestInit,
  options: { timeoutMs?: number } = {},
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const isGet = method === "GET";
  const cacheKey = `${BASE}${path}`;

  if (isGet) {
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
    if (!res.ok) {
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
  event: { title: string; summary: string };
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
    segment_skill: number | null;
    liquidity_factor: number | null;
    reason: string;
  };
  confidence: { level: string | null; score: number | null; confidence: number | null };
  recommendation: { decision: string | null; action: string };
  risk: { level: string | null; flags: string[] };
  category: string | null;
  status: string | null;
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

export interface FreshEdge {
  event_id: string;
  event_title: string;
  edge: EdgeTrajectory;
}

export interface LoopRun {
  job: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  duration_seconds?: number | null;
  error?: string;
  details?: Record<string, unknown>;
}

export interface LoopStatus {
  scheduler?: { running?: boolean | null };
  runs?: Record<string, LoopRun | null>;
  counts?: {
    events?: number;
    resolved_events?: number;
    open_opportunities?: number;
    pending_links?: number;
    orphan_predictions?: number;
    calibration_n?: number;
    predictions?: Record<string, number>;
  };
  calibration?: PredictionCalibration;
}

export interface PendingLink {
  event_id: string;
  id?: string;
  market_name?: string;
  contract_id?: string;
  market_question?: string;
  resolution_criteria?: string;
  link_method?: string;
  link_confidence?: number;
  linked_at?: string;
  verified?: boolean;
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
  by_category: Record<string, { n: number; brier_score: number; skill_score: number; grade: string }>;
}

export interface PredictionRecord {
  id: string;
  event_id: string;
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

export const eventsApi = {
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

  list: (limit = 50, offset = 0) =>
    api<{
      events: TrackedEntry[];
      count?: number;
      total?: number;
      limit?: number;
      offset?: number;
    }>(`/events/?limit=${limit}&offset=${offset}`),

  detail: (id: string) =>
    api<TrackedEntry>(`/events/${encodeURIComponent(id)}`),

  setTracking: (id: string, body: { status?: string; priority?: string }) =>
    api<TrackedEntry>(`/events/${encodeURIComponent(id)}/tracking`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  resolveAuto: () =>
    api<{
      status?: string;
      resolved_count?: number;
      checked_count?: number;
      by_source?: Record<string, number>;
    }>("/events/resolve/auto", { method: "POST" }, { timeoutMs: 180_000 }),

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

  // M5 opportunity surface. Defaults to act + watch; pass "act" to narrow.
  openDecisions: (decision?: "act" | "watch", limit = 50) =>
    api<{ count: number; decisions: DecisionReport[] }>(
      `/events/decisions/open?limit=${limit}${decision ? `&decision=${decision}` : ""}`,
    ),

  decision: (id: string) =>
    api<DecisionReport>(`/events/${encodeURIComponent(id)}/decision`),

  // M5 fresh-edge surface (recent, holding-near-peak divergences).
  freshEdges: (limit = 10) =>
    api<{ count: number; edges: FreshEdge[] }>(`/events/edges/fresh?limit=${limit}`),

  // M2/M5 act-only prediction calibration scorecard.
  predictionCalibration: () =>
    api<PredictionCalibration>("/events/predictions/calibration"),

  recentPredictions: (limit = 50) =>
    api<{ predictions: PredictionRecord[] }>(`/events/predictions/recent?limit=${limit}`),

  loopStatus: () =>
    api<LoopStatus>("/events/loop/status"),

  pendingLinks: () =>
    api<{ pending: PendingLink[] }>("/events/links/pending"),

  verifyLink: (id: string, contractId: string) =>
    api<PendingLink>(`/events/${encodeURIComponent(id)}/link/verify`, {
      method: "POST",
      body: JSON.stringify({ contract_id: contractId }),
    }),
};
