import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface MarketSettlement {
  id: number;
  match_id: string;
  mapped_outcome: string;
  engine: string;
  competition: string;
  settlement_implied_prob: number | null;
  settlement_captured_at: string | null;
  link_id: number | null;
  model_prob: number | null;
  market_prob_at_detection: number | null;
  raw_edge: number | null;
  adjusted_edge: number | null;
  brier_score: number | null;
  signed_error: number | null;
  direction_correct: number | null;
  status: string;
  skip_reason: string | null;
  match_finished_at: string;
  processed_at: string;
}

export interface MarketCalibration {
  id: number;
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_brier: number;
  avg_signed_error: number;
  direction_accuracy: number;
  last_updated: string;
}

export interface SettlementList {
  items: MarketSettlement[];
  total: number;
}

export interface CalibrationList {
  items: MarketCalibration[];
  total: number;
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  if (entries.length === 0) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of entries) usp.set(k, String(v));
  return `?${usp.toString()}`;
}

export async function fetchSettlement(matchId: string): Promise<SettlementList> {
  const res = await fetch(`${API_BASE}/api/sport-settlements/${matchId}`);
  if (!res.ok) throw new Error(`Failed to fetch settlement: ${res.status}`);
  return res.json();
}

export async function fetchSettlementHistory(
  limit: number = 20,
  engine?: string,
): Promise<SettlementList> {
  const q = buildQuery({ limit, engine });
  const res = await fetch(`${API_BASE}/api/sport-settlements/history${q}`);
  if (!res.ok) throw new Error(`Failed to fetch history: ${res.status}`);
  return res.json();
}

export async function fetchCalibrations(
  engine?: string,
  competition?: string,
): Promise<CalibrationList> {
  const q = buildQuery({ engine, competition });
  const res = await fetch(`${API_BASE}/api/sport-settlements/calibrations${q}`);
  if (!res.ok) throw new Error(`Failed to fetch calibrations: ${res.status}`);
  return res.json();
}
