"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery } from "../client";
import type {
  EngineScoreItem,
  PredictionHistoryList,
  PredictionTrajectory,
  CalibrationItem,
  ReliabilityData,
} from "../types";

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
