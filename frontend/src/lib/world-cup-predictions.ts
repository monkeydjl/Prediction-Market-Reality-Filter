/**
 * API client for World Cup match predictions
 */

import { getWorldCupApiBase } from "./env";
import { getOperatorApiKey } from "./api";

export interface PredictedScore {
  home: number;
  away: number;
}

export interface OutcomeProbabilities {
  home_win: number;
  draw: number;
  away_win: number;
}

export interface MatchFixture {
  match_id: string;
  fixture_id: number;
  home_team: string;
  away_team: string;
  kickoff_utc: string;
  venue: string;
  stage: string;
  group?: string;
  status: string;
  home_score?: number;
  away_score?: number;
}

export interface MatchPrediction {
  predicted_score: PredictedScore;
  outcome_probabilities: OutcomeProbabilities;
  confidence: number;
  prediction_method?: string;
  ai_reasoning?: string;
  key_factors?: string[];
  last_updated?: string;
  elo_ratings?: {
    home: number;
    away: number;
  };
  has_betting_odds?: boolean;
  engine_used?: "elo_odds" | "hybrid" | "auto";
  data_quality?: "real" | "partial" | "mock";
  data_quality_score?: number;
  betting_analysis?: {
    "1x2": { home_win: number; draw: number; away_win: number; implied_odds: Record<string, number> };
    double_chance: Record<string, number>;
    over_under: Record<string, number>;
    btts: { yes: number; no: number };
    top_3_correct_scores: { score: string; probability: number }[];
  };
  tactical_analysis?: string;
}

export interface MatchWithPrediction {
  match: MatchFixture;
  prediction?: MatchPrediction;
}

export interface PredictionHistoryEntry {
  timestamp: string;
  predicted_score: PredictedScore;
  outcome_probabilities: OutcomeProbabilities;
  confidence: number;
  trigger: string;
  prediction_method?: string;
}

export interface PredictionTriggerResult {
  status?: string;
  match_id?: string;
  predicted_score?: PredictedScore;
  outcome_probabilities?: OutcomeProbabilities;
  confidence?: number;
  prediction_method?: string;
  elo_ratings?: MatchPrediction["elo_ratings"];
  has_betting_odds?: boolean;
  engine_used?: MatchPrediction["engine_used"];
  error?: string;
}

const API_BASE = getWorldCupApiBase();

/**
 * Build headers for POST requests, including the operator API key if set.
 * This mirrors the behavior of the centralized `api.ts` client.
 */
export function postHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const key = getOperatorApiKey();
  if (key) headers["X-API-Key"] = key;
  return extra ? { ...headers, ...Object.fromEntries(new Headers(extra)) } : headers;
}

/**
 * Fetch all matches with optional filters
 */
export async function fetchMatches(params?: {
  stage?: string;
  status?: string;
  limit?: number;
}): Promise<MatchWithPrediction[]> {
  const query = new URLSearchParams();
  if (params?.stage) query.set('stage', params.stage);
  if (params?.status) query.set('status', params.status);
  if (params?.limit) query.set('limit', params.limit.toString());

  // Add cache-busting timestamp
  query.set('_t', Date.now().toString());

  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/matches?${query}`,
    { cache: 'no-store' }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch matches: ${response.statusText}`);
  }

  const data = await response.json();
  return data.matches || [];
}

/**
 * Fetch a single match with its prediction
 */
export async function fetchMatchWithPrediction(matchId: string): Promise<MatchWithPrediction> {
  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/matches/${matchId}`,
    { cache: 'no-store' }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch match: ${response.statusText}`);
  }

  return await response.json();
}

/**
 * Fetch prediction history for a match
 */
export async function fetchPredictionHistory(matchId: string): Promise<PredictionHistoryEntry[]> {
  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/matches/${matchId}/prediction-history`,
    { cache: 'no-store' }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch prediction history: ${response.statusText}`);
  }

  const data = await response.json();
  return data.history || [];
}

/**
 * Fetch today's matches with predictions
 */
export async function fetchTodayMatches(): Promise<MatchWithPrediction[]> {
  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/today?_t=${Date.now()}`,
    { cache: 'no-store' }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch today's matches: ${response.statusText}`);
  }

  const data = await response.json();
  return data.matches || [];
}

/**
 * Trigger manual prediction for a match
 */
export async function triggerPrediction(
  matchId: string,
  engine?: "elo_odds" | "hybrid" | "auto"
): Promise<PredictionTriggerResult> {
  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/matches/${matchId}/predict`,
    {
      method: 'POST',
      headers: postHeaders(),
      body: JSON.stringify({ engine: engine || 'auto' }),
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to trigger prediction: ${response.statusText}`);
  }

  return await response.json();
}

/**
 * Compare predictions from different engines
 */
export async function compareEngines(matchId: string): Promise<{
  elo_odds?: MatchPrediction;
  hybrid?: MatchPrediction;
}> {
  try {
    // Trigger both engines in parallel
    const [eloResult, hybridResult] = await Promise.all([
      triggerPrediction(matchId, "elo_odds"),
      triggerPrediction(matchId, "hybrid")
    ]);

    // Convert flat API response to MatchPrediction format
    const toMatchPrediction = (result: PredictionTriggerResult): MatchPrediction | undefined => {
      if (
        result.status === "error" ||
        !result.predicted_score ||
        !result.outcome_probabilities ||
        result.confidence == null
      ) {
        return undefined;
      }

      return {
        predicted_score: result.predicted_score,
        outcome_probabilities: result.outcome_probabilities,
        confidence: result.confidence,
        prediction_method: result.prediction_method,
        elo_ratings: result.elo_ratings,
        has_betting_odds: result.has_betting_odds,
        engine_used: result.engine_used
      };
    };

    return {
      elo_odds: toMatchPrediction(eloResult),
      hybrid: toMatchPrediction(hybridResult)
    };
  } catch (error) {
    console.error("Failed to compare engines:", error);
    return {};
  }
}

/**
 * Get AI analysis of a match prediction
 */
export async function analyzePrediction(matchId: string): Promise<string> {
  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/matches/${matchId}/analyze`,
    {
      method: 'POST',
      headers: postHeaders(),
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to analyze prediction: ${response.statusText}`);
  }

  const data = await response.json();
  return data.analysis || "分析结果为空";
}

/**
 * Sync fixtures from API-Football
 */
export async function syncFixtures(): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/world-cup/predictions/sync-fixtures`,
    {
      method: 'POST',
      headers: postHeaders(),
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to sync fixtures: ${response.statusText}`);
  }
}
