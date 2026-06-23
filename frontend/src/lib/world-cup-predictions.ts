/**
 * API client for World Cup match predictions
 */

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
}

export interface MatchPrediction {
  predicted_score: PredictedScore;
  outcome_probabilities: OutcomeProbabilities;
  confidence: number;
  prediction_method?: string;
  ai_reasoning?: string;
  key_factors?: string[];
  last_updated?: string;
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
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

/**
 * Fetch all matches with optional filters
 */
export async function fetchMatches(params?: {
  stage?: string;
  status?: string;
  limit?: number;
}): Promise<MatchFixture[]> {
  const query = new URLSearchParams();
  if (params?.stage) query.set('stage', params.stage);
  if (params?.status) query.set('status', params.status);
  if (params?.limit) query.set('limit', params.limit.toString());

  const response = await fetch(
    `${API_BASE}/world-cup/predictions/matches?${query}`,
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
    `${API_BASE}/world-cup/predictions/matches/${matchId}`,
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
    `${API_BASE}/world-cup/predictions/matches/${matchId}/prediction-history`,
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
    `${API_BASE}/world-cup/predictions/today`,
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
export async function triggerPrediction(matchId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/matches/${matchId}/predict`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to trigger prediction: ${response.statusText}`);
  }
}

/**
 * Sync fixtures from API-Football
 */
export async function syncFixtures(): Promise<void> {
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/sync-fixtures`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to sync fixtures: ${response.statusText}`);
  }
}
