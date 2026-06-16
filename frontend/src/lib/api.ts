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

  health: () =>
    api<{
      count?: number;
      actionable_count?: number;
      average_confidence?: number;
      signal_breakdown?: Record<string, number>;
    }>("/calibration/summary"),

  calibration: () =>
    api<{
      overall: CalibrationAgg;
      by_source: Record<string, CalibrationAgg>;
      by_base_rate_category: Record<string, CalibrationAgg>;
    }>("/events/calibration"),
};

export interface CalibrationAgg {
  brier_score: number | null;
  skill_score: number | null;
  grade: string;
  n: number;
}
