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
  const s = record.tracking?.status;
  if (s === "tracking" || s === "archived") return s;
  return "watching";
}

const GENERIC_SOURCE_CATEGORIES = new Set([
  "prediction",
  "predictions",
  "prediction_market",
  "prediction_question",
  "polymarket",
  "kalshi",
  "limitless",
  "market",
  "unknown",
]);

const TEXT_CATEGORY_RULES: Array<[string, string[]]> = [
  [
    "monetary",
    [
      "central bank",
      "bank of england",
      "boe",
      "interest rate",
      "interest rates",
      "key rate",
      "official cash rate",
      "reserve bank",
      "rate cut",
      "rate hike",
      "\u592e\u884c",
      "\u5229\u7387",
      "\u964d\u606f",
      "\u52a0\u606f",
    ],
  ],
  [
    "sports_game",
    [
      "ufc",
      "mma",
      "knockout",
      "ko or tko",
      "tko",
      "main card",
      "heavyweight",
      "1+ shots",
      "shots",
      "\u7ec8\u7ed3",
      "\u62f3\u51fb",
      "\u683c\u6597",
    ],
  ],
  [
    "sports_general",
    [
      "lebron james",
      "cleveland cavaliers",
      "nba",
      "basketball",
      "\u52d2\u5e03\u6717",
      "\u8a79\u59c6\u65af",
      "\u9a91\u58eb\u961f",
      "\u7bee\u7403",
    ],
  ],
  [
    "crypto",
    [
      "bitcoin",
      "btc",
      "ethereum",
      "eth",
      "crypto",
      "opensea",
      "fdv",
      "token",
      "hype up or down",
      "hype \u6da8\u8dcc",
      "\u6bd4\u7279\u5e01",
      "\u4ee5\u592a\u574a",
      "\u52a0\u5bc6",
    ],
  ],
  [
    "tech_product",
    [
      "gta vi",
      "grand theft auto",
      "trailer",
      "iphone",
      "apple event",
      "product launch",
      "software update",
      "app store",
      "robotaxi",
      "\u9884\u544a\u7247",
      "\u79d1\u6280\u4ea7\u54c1",
    ],
  ],
  [
    "geopolitics_general",
    [
      "visit russia",
      "russia",
      "ukraine",
      "nato",
      "un vote",
      "diplomatic",
      "treaty",
      "ceasefire",
      "war",
      "invade",
      "israel",
      "israeli",
      "litani river",
      "airspace",
      "\u4fc4\u7f57\u65af",
      "\u4e4c\u514b\u5170",
      "\u5317\u7ea6",
      "\u5916\u4ea4",
      "\u505c\u706b",
      "\u6218\u4e89",
      "\u4ee5\u8272\u5217",
      "\u5229\u5854\u5c3c\u6cb3",
      "\u9886\u7a7a",
    ],
  ],
  [
    "legal",
    [
      "epstein",
      "fbi",
      "raid",
      "raided",
      "storage units",
      "court",
      "lawsuit",
      "trial",
      "indictment",
      "subpoena",
      "\u7231\u6cfc\u65af\u5766",
      "\u641c\u67e5",
      "\u50a8\u7269\u67dc",
      "\u6cd5\u9662",
      "\u8bc9\u8bbc",
      "\u5ba1\u5224",
    ],
  ],
  [
    "politics_general",
    [
      "election",
      "candidate",
      "nomination",
      "senate",
      "governor",
      "president",
      "prime minister",
      "parliament",
      "referendum",
      "trump administration",
      "population decrease",
      "population decline",
      "\u9009\u4e3e",
      "\u5019\u9009\u4eba",
      "\u63d0\u540d",
      "\u53c2\u8bae\u9662",
      "\u603b\u7edf",
      "\u5dde\u957f",
      "\u4eba\u53e3\u51cf\u5c11",
      "\u4eba\u53e3\u4e0b\u964d",
    ],
  ],
  [
    "weather_event",
    ["weather", "hurricane", "storm", "rainfall", "temperature", "\u5929\u6c14", "\u98d3\u98ce", "\u964d\u96e8", "\u6c14\u6e29"],
  ],
  [
    "health_event",
    ["vaccine", "fda approval", "clinical trial", "pandemic", "disease", "\u75ab\u82d7", "\u4e34\u5e8a\u8bd5\u9a8c", "\u75be\u75c5"],
  ],
  [
    "company_earnings",
    ["earnings", "revenue", "profit", "quarterly results", "\u8d22\u62a5", "\u8425\u6536", "\u5229\u6da6"],
  ],
  ["ipo", ["ipo", "initial public offering", "\u4e0a\u5e02"]],
];

function specificCategory(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const cleaned = value.trim();
  if (!cleaned) return undefined;
  const normalized = cleaned.toLowerCase().replace(/[\s-]+/g, "_");
  return GENERIC_SOURCE_CATEGORIES.has(normalized) ? undefined : cleaned;
}

function inferCategoryFromText(values: unknown[]): string | undefined {
  const text = values.map((value) => String(value ?? "")).join(" ").toLowerCase();
  if (!text.trim()) return undefined;
  for (const [category, needles] of TEXT_CATEGORY_RULES) {
    if (needles.some((needle) => textMatches(needle, text))) return category;
  }
  return undefined;
}

function textMatches(needle: string, text: string): boolean {
  if (/^[\x00-\x7F]+$/.test(needle) && /[a-z0-9]/i.test(needle)) {
    const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(?<![a-z0-9])${escaped}(?![a-z0-9])`).test(text);
  }
  return text.includes(needle);
}

// Sports events use source type as the dashboard segment; other records use
// the most specific non-generic category available.
function categoryOf(record: EventRecord): string {
  if (record.source?.type === "sports_event") return "sports_event";
  const legacy = (record as unknown as { legacy_analysis?: Record<string, unknown> })
    .legacy_analysis;
  const cat = legacy?.base_rate_category;
  const source = record.source as
    | (EventRecord["source"] & { category?: string; event_type?: string })
    | undefined;
  return (
    specificCategory(cat) ||
    specificCategory(source?.category) ||
    specificCategory(source?.event_type) ||
    specificCategory(source?.type) ||
    specificCategory(source?.platform) ||
    inferCategoryFromText([
      record.event_title,
      record.event_title_zh,
      record.event_summary,
      source?.question,
      source?.title,
      source?.name,
    ]) ||
    "general"
  );
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
