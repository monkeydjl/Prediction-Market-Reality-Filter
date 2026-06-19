import type {
  EventRecord,
  TrackedEntry,
  Mover,
  HistorySnapshot,
  Trend,
  SimilarEvent,
} from "./types";

// Same-origin in production (FastAPI serves the static export at / and the API
// under /api). In dev, next.config rewrites proxy /api/* to :8000.
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
  return res.json() as Promise<T>;
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

export const eventsApi = {
  discover: (limit = 2, useCache = false, signal?: AbortSignal) =>
    api<{ events: EventRecord[]; source?: string; count?: number }>(
      `/events/discover?limit=${limit}&use_cache=${useCache}`,
      { signal }
    ),

  analyze: (body: {
    event_question: string;
    baseline_probability: number;
    news_context?: string;
  }) =>
    api<EventRecord>("/events/analyze", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  list: (limit = 50) =>
    api<{ events: TrackedEntry[]; count?: number }>(`/events/?limit=${limit}`),

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
    }>("/events/resolve/auto", { method: "POST" }),

  history: (id: string) =>
    api<{ history: HistorySnapshot[]; trend?: Trend; count?: number }>(
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

  // M5 fresh-edge surface (recent, holding-near-peak divergences).
  freshEdges: (limit = 10) =>
    api<{ count: number; edges: FreshEdge[] }>(`/events/edges/fresh?limit=${limit}`),

  // M2/M5 act-only prediction calibration scorecard.
  predictionCalibration: () =>
    api<PredictionCalibration>("/events/predictions/calibration"),
};
