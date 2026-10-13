/**
 * API client for World Cup match predictions
 */

import { getApiBase } from "@/lib/env";
import {
  buildApiErrorMessage,
  getOperatorApiKey,
  getOperatorId,
} from "@/lib/api";
import type {
  AnalysisHistoryEntry,
  EngineComparisonResponse,
  EngineComparisonStat,
  MatchAnalysisHistoryResponse,
  MatchAnalyzeResponse,
  ScorePair as PredictedScore,
  SyncFixturesResponse,
} from "@/lib/generated-types";

// Contracts for this client now come from the generated types (audit 53.71).
// `PredictedScore` was a hand-written `{home, away}` pair and
// `EngineComparisonStat` a hand-written per-engine roll-up; the backend
// publishes both (as `ScorePair` / `EngineComparisonStat`), so the local
// declarations were retired rather than kept as second copies of generated
// shapes. The old names stay exported so existing consumers keep compiling.
//
// Audit 53.75 retired one more: the hand-written `AIAnalysisHistoryEntry`.
// Its `predicted_score` was typed with the *integer* `ScorePair`, but
// `get_analysis_history` reads `Float` columns, so the generated
// `AnalysisHistoryEntry.predicted_score` is `PredictedScorePair` (both members
// `float`). The old export name is gone -- the only consumer now imports the
// generated type.
export type { AnalysisHistoryEntry, EngineComparisonStat, PredictedScore };

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

export interface ConfidenceCalibrationBucket {
  label?: string;
  count?: number;
  actual_accuracy?: number | null;
  avg_confidence?: number | null;
}

export interface ConfidenceCalibrationInfo {
  raw: number;
  calibrated: number;
  method: string;
  engine_filter?: string;
  total_samples?: number;
  min_total_samples?: number;
  min_bucket_samples?: number;
  is_reliable?: boolean;
  bucket_is_reliable?: boolean;
  is_reference_only?: boolean;
  reason?: string;
  bucket?: ConfidenceCalibrationBucket | null;
  applied_bucket?: ConfidenceCalibrationBucket | null;
}

export interface ExplanationContributionItem {
  key: "elo" | "odds" | "schedule" | "injury" | "motivation" | "market_signal" | string;
  label: string;
  unit: "pp" | "xg" | "%xg" | string;
  home_impact: number;
  away_impact: number;
  description: string;
  available?: boolean;
}

export interface ExplanationContributions {
  engine?: string;
  home_team?: string;
  away_team?: string;
  prediction_method?: string;
  engine_weights?: {
    elo_weight?: number;
    hybrid_weight?: number;
    source?: string;
  } | null;
  items: ExplanationContributionItem[];
}

export interface MatchPrediction {
  predicted_score: PredictedScore;
  outcome_probabilities: OutcomeProbabilities;
  confidence: number;
  raw_confidence?: number;
  confidence_calibration?: ConfidenceCalibrationInfo | null;
  explanation_contributions?: ExplanationContributions | null;
  high_confidence_selection?: {
    selected_engine?: "elo_odds" | "hybrid" | "integrated" | string;
    selection_confidence?: number;
    candidate_confidences?: Record<
      string,
      {
        raw?: number;
        calibrated?: number;
        is_reliable?: boolean;
        is_reference_only?: boolean;
        total_samples?: number;
        min_total_samples?: number;
        min_bucket_samples?: number;
        bucket_is_reliable?: boolean;
        reason?: string;
        bucket?: ConfidenceCalibrationBucket | null;
        applied_bucket?: ConfidenceCalibrationBucket | null;
      }
    >;
  } | null;
  prediction_method?: string;
  ai_reasoning?: string;
  key_factors?: string[];
  last_updated?: string;
  elo_ratings?: {
    home: number;
    away: number;
  };
  has_betting_odds?: boolean;
  engine_used?: "elo_odds" | "hybrid" | "integrated" | "gbm" | "high_confidence" | "auto";
  data_quality?: "real" | "partial";
  data_quality_notes?: string[];
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
  raw_confidence?: number;
  confidence_calibration?: MatchPrediction["confidence_calibration"];
  explanation_contributions?: MatchPrediction["explanation_contributions"];
  high_confidence_selection?: MatchPrediction["high_confidence_selection"];
  trigger: string;
  prediction_method?: string;
  engine_used?: MatchPrediction["engine_used"];
  data_quality?: MatchPrediction["data_quality"];
  data_quality_notes?: string[];
}

export interface PredictionTriggerResult {
  status?: string;
  match_id?: string;
  predicted_score?: PredictedScore;
  outcome_probabilities?: OutcomeProbabilities;
  confidence?: number;
  raw_confidence?: number;
  confidence_calibration?: MatchPrediction["confidence_calibration"];
  explanation_contributions?: MatchPrediction["explanation_contributions"];
  high_confidence_selection?: MatchPrediction["high_confidence_selection"];
  prediction_method?: string;
  elo_ratings?: MatchPrediction["elo_ratings"];
  has_betting_odds?: boolean;
  engine_used?: MatchPrediction["engine_used"];
  error?: string;
  reason?: string;
}

const API_BASE = getApiBase();

/**
 * Build headers for POST requests, including the operator API key if set.
 * This mirrors the behavior of the centralized `api.ts` client.
 */
export function postHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const key = getOperatorApiKey();
  const operator = getOperatorId();
  if (key) headers["X-API-Key"] = key;
  if (operator) headers["X-Operator"] = operator;
  return extra ? { ...headers, ...Object.fromEntries(new Headers(extra)) } : headers;
}

async function worldCupFetchError(response: Response, fallback: string): Promise<Error> {
  const bodyText = await response.text();
  if (response.status === 401 || response.status === 403) {
    return new Error(buildApiErrorMessage(response.status, bodyText));
  }

  let detail = "";
  try {
    const data = JSON.parse(bodyText) as { detail?: unknown; message?: unknown };
    detail =
      typeof data.detail === "string"
        ? data.detail
        : typeof data.message === "string"
          ? data.message
          : "";
  } catch {
    detail = bodyText.trim();
  }

  return new Error(detail ? `${fallback}: ${detail}` : `${fallback}: ${response.statusText || response.status}`);
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
  return (data.history || []).filter(
    (entry: PredictionHistoryEntry) => !entry.trigger?.endsWith("_comparison")
  );
}

/**
 * Trigger manual prediction for a match
 */
export async function triggerPrediction(
  matchId: string,
  engine?: "elo_odds" | "hybrid" | "integrated" | "high_confidence" | "gbm" | "auto",
  options?: { compareOnly?: boolean }
): Promise<PredictionTriggerResult> {
  const query = options?.compareOnly ? "?compare_only=true" : "";
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/matches/${matchId}/predict${query}`,
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
  integrated?: MatchPrediction;
  gbm?: MatchPrediction;
}> {
  try {
    // Trigger engines in parallel in read-only mode so the comparison
    // card works even after kickoff (no persistence, no freeze).
    // Use allSettled so one engine failure doesn't block the others.
    const [eloSettled, hybridSettled, integratedSettled, gbmSettled] = await Promise.allSettled([
      triggerPrediction(matchId, "elo_odds", { compareOnly: true }),
      triggerPrediction(matchId, "hybrid", { compareOnly: true }),
      triggerPrediction(matchId, "integrated", { compareOnly: true }),
      triggerPrediction(matchId, "gbm", { compareOnly: true }),
    ]);

    const extractResult = (
      settled: PromiseSettledResult<PredictionTriggerResult>
    ): PredictionTriggerResult | undefined => {
      if (settled.status === "fulfilled") return settled.value;
      console.warn("Engine comparison failed:", settled.reason);
      return undefined;
    };

    const eloResult = extractResult(eloSettled);
    const hybridResult = extractResult(hybridSettled);
    const integratedResult = extractResult(integratedSettled);
    const gbmResult = extractResult(gbmSettled);

    // Convert flat API response to MatchPrediction format
    const toMatchPrediction = (result?: PredictionTriggerResult): MatchPrediction | undefined => {
      if (
        !result ||
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
        raw_confidence: result.raw_confidence,
        confidence_calibration: result.confidence_calibration,
        explanation_contributions: result.explanation_contributions,
        high_confidence_selection: result.high_confidence_selection,
        prediction_method: result.prediction_method,
        elo_ratings: result.elo_ratings,
        has_betting_odds: result.has_betting_odds,
        engine_used: result.engine_used
      };
    };

    return {
      elo_odds: toMatchPrediction(eloResult),
      hybrid: toMatchPrediction(hybridResult),
      integrated: toMatchPrediction(integratedResult),
      gbm: toMatchPrediction(gbmResult),
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
    `${API_BASE}/world-cup/predictions/matches/${matchId}/analyze`,
    {
      method: 'POST',
      headers: postHeaders(),
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to analyze prediction: ${response.statusText}`);
  }

  // The route publishes `MatchAnalyzeResponse`; reading `.analysis` off the
  // generated type is what keeps this projection honest (audit 53.75).
  const data = (await response.json()) as MatchAnalyzeResponse;
  return data.analysis || "分析结果为空";
}

/**
 * Fetch the stored AI analysis history for a match, newest first.
 */
export async function fetchAnalysisHistory(matchId: string): Promise<AnalysisHistoryEntry[]> {
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/matches/${matchId}/analysis-history`,
    { cache: 'no-store' }
  );

  if (!response.ok) {
    throw await worldCupFetchError(response, "加载分析历史失败");
  }

  const data = (await response.json()) as MatchAnalysisHistoryResponse;
  return data.history ?? [];
}

export interface OptimizedPrediction {
  predicted_score: PredictedScore;
  outcome_probabilities: OutcomeProbabilities;
  confidence: number;
  reasoning?: string;
}

export interface PredictionOptimization {
  blind_spots?: string[];
  calibration_issues?: string[];
  optimized_prediction?: OptimizedPrediction | null;
  raw_text?: string;
}

export interface PredictionOptimizeResult {
  status: string;
  match_id?: string;
  message?: string;
  original_prediction: {
    predicted_score: PredictedScore;
    outcome_probabilities: OutcomeProbabilities;
    confidence: number;
  };
  optimization: PredictionOptimization;
}

/**
 * Ask the AI optimizer for blind spots / calibration fixes on a match's
 * current prediction. Write operation — consumes provider quota.
 */
export async function optimizePrediction(matchId: string): Promise<PredictionOptimizeResult> {
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/matches/${matchId}/optimize`,
    {
      method: 'POST',
      headers: postHeaders(),
      cache: 'no-store',
    }
  );

  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new Error(data.detail || `HTTP ${response.status}`);
  }

  return await response.json();
}

export type EngineComparisonKey = "elo_odds" | "hybrid" | "integrated" | "gbm";

// Derived from the generated envelope rather than re-declared: the backend's
// `engines` is an open key map, so `EngineComparisonKey` stays a UI concern
// (which engines the console offers) while the value shape comes from the
// generated `EngineComparisonStat`.
export type EngineComparisonData = EngineComparisonResponse["engines"];

/**
 * Per-engine accuracy roll-up over settled matches. Throws with the backend's
 * message when no engine has enough graded predictions yet.
 */
export async function fetchEngineComparison(): Promise<EngineComparisonData> {
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/engine-comparison`,
    { cache: 'no-store' }
  );

  if (!response.ok) {
    throw new Error("Failed to load comparison data");
  }

  const result = (await response.json()) as EngineComparisonResponse;
  if (result.status !== "ok") {
    throw new Error(result.message || "No data available");
  }
  return result.engines ?? {};
}

/**
 * Sync fixtures from API-Football
 */
export async function syncFixtures(): Promise<SyncFixturesResponse> {
  const response = await fetch(
    `${API_BASE}/world-cup/predictions/sync-fixtures`,
    {
      method: 'POST',
      headers: postHeaders(),
    }
  );

  if (!response.ok) {
    throw await worldCupFetchError(response, "同步赛程失败");
  }

  return (await response.json()) as SyncFixturesResponse;
}
