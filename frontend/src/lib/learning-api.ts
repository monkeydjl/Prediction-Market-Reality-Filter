import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

// Type definitions (single source of truth)

export interface EngineScoreItem {
  engine: string;
  competition: string | null;
  accuracy: number;
  avg_mae: number;
  brier_score: number;
  sample_count: number;
  confidence_calibration: number;
  last_updated: string | null;
}

export interface PredictionHistoryItem {
  id: number;
  match_id: string;
  sport: string | null;
  competition: string | null;
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  feature_version: string;
  trigger: string;
  created_at: string;
  outcome: {
    home_score: number;
    away_score: number;
    outcome: string;
    outcome_correct: number | null;
    score_mae: number | null;
    brier_score: number | null;
    finished_at: string | null;
  } | null;
}

export interface PredictionHistoryList {
  items: PredictionHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PredictionTrajectory {
  match_id: string;
  sport: string | null;
  competition: string | null;
  items: PredictionHistoryItem[];
  count: number;
}

export interface CalibrationItem {
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_confidence: number;
  avg_accuracy: number;
  last_updated: string | null;
}

export interface ReliabilityBin {
  lower: number;
  upper: number;
  center: number;
  avg_predicted: number | null;
  actual_frequency: number | null;
  count: number;
}

export interface ReliabilityData {
  engine: string | null;
  competition: string | null;
  bins: ReliabilityBin[];
  total_samples: number;
}

// Helper: build query string from params object
function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

// Fetch functions

export async function fetchEngineScores(params?: {
  engine?: string;
  competition?: string;
  sport?: string;
}): Promise<EngineScoreItem[]> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/engines/scores${qs}`);
  if (!res.ok) throw new Error("Failed to fetch engine scores");
  return res.json();
}

export async function fetchPredictionHistory(params?: {
  sport?: string;
  competition?: string;
  limit?: number;
  offset?: number;
}): Promise<PredictionHistoryList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/history${qs}`);
  if (!res.ok) throw new Error("Failed to fetch prediction history");
  return res.json();
}

export async function fetchPredictionTrajectory(matchId: string): Promise<PredictionTrajectory> {
  const res = await fetch(`${API_BASE}/api/predictions/history/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch trajectory");
  return res.json();
}

export async function fetchCalibration(params?: {
  engine?: string;
  competition?: string;
}): Promise<CalibrationItem[]> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/calibration${qs}`);
  if (!res.ok) throw new Error("Failed to fetch calibration");
  return res.json();
}

export async function fetchReliability(params?: {
  engine?: string;
  competition?: string;
  bins?: number;
}): Promise<ReliabilityData> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/calibration/reliability${qs}`);
  if (!res.ok) throw new Error("Failed to fetch reliability");
  return res.json();
}
