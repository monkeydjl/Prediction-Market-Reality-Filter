// Adapters: real backend shapes (see lib/types.ts) -> view models the ported
// v0 components consume. The backend probability scale is 0-100, matching the
// design, so no rescaling is needed. Everything is best-effort: the backend
// allows missing fields, so each accessor falls back gracefully.

import type {
  EventRecord,
  TrackedEntry,
  Mover,
  HistorySnapshot,
} from "./types";

export type Trend = "up" | "down" | "flat";

export interface EventView {
  id: string;
  title: string;
  description: string;
  category: string;
  currentProbability: number;
  baselineProbability: number;
  delta: number;
  direction: string;
  evidenceSupport: number; // 0-1
  priority: "high" | "medium" | "low";
  trackingStatus: "tracking" | "watching" | "archived";
  trend: Trend;
  valueScore: number;
}

function num(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function trendOf(delta: number): Trend {
  return delta > 0.5 ? "up" : delta < -0.5 ? "down" : "flat";
}

// Human tracking priority. A user's explicit choice (record.tracking.priority,
// set in the detail page) wins; otherwise fall back to impact.level.
function priorityOf(record: EventRecord): "high" | "medium" | "low" {
  const user = record.tracking?.priority;
  if (user === "high" || user === "medium" || user === "low") return user;
  const lvl = String(record.impact?.level ?? "").toUpperCase();
  if (lvl === "HIGH") return "high";
  if (lvl === "LOW") return "low";
  return "medium";
}

// Human tracking status (tracking | watching | archived). Defaults to
// "watching" when the user has not made an explicit decision (tracking=None).
// Events with a resolved outcome are always treated as archived so they leave
// the active list and enter the calibration/review database.
function trackingStatusOf(
  record: EventRecord,
): "tracking" | "watching" | "archived" {
  if (record.outcome?.status) return "archived";
  const s = record.tracking?.status;
  if (s === "tracking" || s === "archived") return s;
  return "watching";
}

// Sports events use source type as the dashboard segment; other records use
// legacy_analysis.base_rate_category, then fall back to the source type.
function categoryOf(record: EventRecord): string {
  if (record.source?.type === "sports_event") return "sports_event";
  const legacy = (record as unknown as { legacy_analysis?: Record<string, unknown> })
    .legacy_analysis;
  const cat = legacy?.base_rate_category;
  if (typeof cat === "string" && cat) return cat;
  return record.source?.type || record.source?.platform || "general";
}

export function adaptRecord(record: EventRecord): EventView {
  const p = record.probability ?? {};
  const delta = num(p.change);
  return {
    id: record.event_id,
    title: record.event_title_zh || record.event_title,
    description: record.event_summary ?? "",
    category: categoryOf(record),
    currentProbability: num(p.estimated, num(p.baseline)),
    baselineProbability: num(p.baseline),
    delta,
    direction: String(p.direction ?? "flat"),
    evidenceSupport: num(record.credibility?.confidence),
    priority: priorityOf(record),
    trackingStatus: trackingStatusOf(record),
    trend: trendOf(delta),
    valueScore: num(record.value_score),
  };
}

export function adaptEntry(entry: TrackedEntry): EventView {
  return adaptRecord(entry.record);
}

// Movers carry a trend block (from trend_analysis_service.rank_movers) rather
// than a full record; map what the dashboard cards need.
export function adaptMover(m: Mover): EventView {
  const t = m.trend ?? {};
  const delta = num(t.net_change);
  return {
    id: m.event_id,
    title: m.event_title_zh || m.event_title || m.event_id,
    description: "",
    category: "general",
    currentProbability: num(t.latest_probability),
    baselineProbability: num(t.latest_probability) - delta,
    delta,
    direction: String(t.direction ?? "flat"),
    evidenceSupport: 0,
    priority: "medium",
    trackingStatus: "watching",
    trend: trendOf(delta),
    valueScore: 0,
  };
}

// History snapshots -> a compact numeric series for sparklines / the chart.
export function sparkSeries(history: HistorySnapshot[]): number[] {
  return history
    .map((h) => Number(h.estimated))
    .filter((v) => Number.isFinite(v));
}
