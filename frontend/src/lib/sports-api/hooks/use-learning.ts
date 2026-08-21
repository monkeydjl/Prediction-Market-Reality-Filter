"use client";

import useSWR, { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery, sportPost } from "../client";
import type {
  EngineScoreItem,
  PredictionHistoryList,
  PredictionTrajectory,
  CalibrationItem,
  ReliabilityData,
  ConfidenceReliabilityData,
} from "../types";

/**
 * Per-engine score. The single-engine route returns a narrower row than
 * `/engines/scores` — no calibration or timestamp columns.
 */
export type SingleEngineScore = Pick<
  EngineScoreItem,
  "engine" | "competition" | "accuracy" | "avg_mae" | "brier_score" | "sample_count"
>;

export function useEngineScores(params?: {
  engine?: string;
  competition?: string;
  sport?: string;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/engines/scores${qs}`;
  return useSWR<EngineScoreItem[]>(key);
}

export function usePredictionHistory(params?: {
  sport?: string;
  competition?: string;
  limit?: number;
  offset?: number;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/history${qs}`;
  return useSWR<PredictionHistoryList>(key);
}

export function usePredictionTrajectory(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/predictions/history/${matchId}` : null;
  return useSWR<PredictionTrajectory>(key);
}

export function useCalibration(params?: { engine?: string; competition?: string }) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/calibration${qs}`;
  return useSWR<CalibrationItem[]>(key);
}

export function useReliability(params?: {
  engine?: string;
  competition?: string;
  bins?: number;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/calibration/reliability${qs}`;
  return useSWR<ReliabilityData>(key);
}

/**
 * Reliability of the engine's stated confidence (P1-X1). Distinct route from
 * useReliability: same bin shape, but binned on KernelPrediction.confidence
 * instead of max(outcome_probabilities), plus a signed over/under-confidence gap.
 */
export function useConfidenceReliability(params?: {
  engine?: string;
  competition?: string;
  bins?: number;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/calibration/confidence-reliability${qs}`;
  return useSWR<ConfidenceReliabilityData>(key);
}

/** Score for one engine. Returns 404 until that engine has graded samples. */
export function useEngineScore(engine: string | null, competition?: string) {
  const qs = buildQuery({ competition });
  const key = engine
    ? `${getApiBase()}/predictions/engines/${encodeURIComponent(engine)}/score${qs}`
    : null;
  return useSWR<SingleEngineScore>(key);
}

/**
 * Feed one finished match's outcome back into the learning loop, then refresh
 * the score views it updates.
 */
export async function processOutcome(
  matchId: string,
): Promise<{ match_id: string; status: string }> {
  const result = await sportPost<{ match_id: string; status: string }>(
    `/predictions/outcomes/${encodeURIComponent(matchId)}/process`,
  );
  await mutate(
    (key) => typeof key === "string" && key.includes("/predictions/engines/"),
    undefined,
    { revalidate: true },
  );
  return result;
}
