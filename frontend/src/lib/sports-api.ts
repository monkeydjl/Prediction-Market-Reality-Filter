import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

// Type definitions (single source of truth)

export interface MatchSummary {
  match_id: string;
  sport: string;
  competition: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  has_prediction: boolean;
}

export interface MatchDetail {
  match_id: string;
  sport: string;
  competition: string;
  season_key: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  round: string | null;
}

export interface ContributionItem {
  factor: string;
  direction: string;
  weight: number;
  available: boolean;
  detail: string | null;
  predicted_outcome: string | null;
}

export interface PredictionResult {
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  explanation: ContributionItem[];
  feature_version: string;
  prediction_timestamp: string | null;
}

export class NotFoundError extends Error {}

export async function fetchMatches(sport?: string): Promise<MatchSummary[]> {
  const params = sport ? `?sport=${sport}` : "";
  const res = await fetch(`${API_BASE}/api/predictions/matches${params}`);
  if (!res.ok) throw new Error("Failed to fetch matches");
  return res.json();
}

export async function fetchMatchDetail(
  matchId: string,
): Promise<{ match: MatchDetail; prediction: PredictionResult | null }> {
  const res = await fetch(`${API_BASE}/api/predictions/matches/${matchId}`);
  if (res.status === 404) throw new NotFoundError("Match not found");
  if (!res.ok) throw new Error("Failed to fetch match");
  return res.json();
}

export async function triggerPrediction(matchId: string): Promise<PredictionResult> {
  const res = await fetch(`${API_BASE}/api/predictions/matches/${matchId}/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error("Prediction failed");
  return res.json();
}
