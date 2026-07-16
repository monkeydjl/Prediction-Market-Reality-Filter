import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface SportRecommendation {
  match_id: string;
  mapped_outcome: string;
  direction: string;
  decision: string;
  confidence: string;
  risk_level: string;
  edge_pct: number;
  raw_edge_pct: number;
  trust: number;
  liquidity_factor: number;
  stale: boolean;
  suggested_allocation_pct: number;
  calibration_status: string;
  rationale: string;
  engine_name: string | null;
  competition: string | null;
  prediction_timestamp: string | null;
  model_prob: number;
  market_prob: number;
  sources_count: number;
  captured_at: string | null;
}

export interface RecommendationList {
  items: SportRecommendation[];
  total: number;
}

function buildQuery(params: Record<string, string | number | undefined | boolean>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

export async function fetchRecommendation(matchId: string): Promise<SportRecommendation> {
  const res = await fetch(`${API_BASE}/api/sport-recommendations/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch recommendation");
  return res.json();
}

export async function fetchOpenDecisions(params?: {
  limit?: number;
  decision?: string;
}): Promise<RecommendationList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/sport-recommendations/open${qs}`);
  if (!res.ok) throw new Error("Failed to fetch open decisions");
  return res.json();
}

export async function fetchTopPicks(params?: {
  limit?: number;
  min_abs_edge?: number;
}): Promise<RecommendationList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/sport-recommendations/discrepancies${qs}`);
  if (!res.ok) throw new Error("Failed to fetch top picks");
  return res.json();
}
