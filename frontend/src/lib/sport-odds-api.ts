import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface TraditionalOddsSnapshot {
  implied_prob: number;
  decimal_odds: number;
  bookmaker: string | null;
  bookmakers_count: number;
  captured_at: string | null;
}

export interface TraditionalOddsSeries {
  mapped_outcome: string;
  snapshots: TraditionalOddsSnapshot[];
}

export interface TraditionalOddsHistory {
  match_id: string;
  series: TraditionalOddsSeries[];
  skipped: boolean;
  skip_reason: string | null;
}

export interface TraditionalOddsLatest {
  match_id: string;
  outcomes: TraditionalOddsSnapshot[];
  skipped: boolean;
  skip_reason: string | null;
}

export async function fetchTraditionalOddsLatest(
  matchId: string,
): Promise<TraditionalOddsLatest> {
  const res = await fetch(`${API_BASE}/api/sport-odds/${matchId}/latest`);
  if (!res.ok) throw new Error(`Failed to fetch odds: ${res.status}`);
  return res.json();
}

export async function fetchTraditionalOddsHistory(
  matchId: string,
  mappedOutcome?: string,
): Promise<TraditionalOddsHistory> {
  const usp = new URLSearchParams();
  if (mappedOutcome) usp.set("mapped_outcome", mappedOutcome);
  const q = usp.toString() ? `?${usp.toString()}` : "";
  const res = await fetch(`${API_BASE}/api/sport-odds/${matchId}/history${q}`);
  if (!res.ok) throw new Error(`Failed to fetch odds history: ${res.status}`);
  return res.json();
}
