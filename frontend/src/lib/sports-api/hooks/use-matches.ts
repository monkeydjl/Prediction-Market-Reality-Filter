"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type { MatchSummary, MatchDetail, PredictionResult } from "../types";

type MatchDetailResponse = { match: MatchDetail; prediction: PredictionResult | null };

export function useMatches(sport?: string) {
  const params = sport ? `?sport=${sport}` : "";
  const key = `${getApiBase()}/predictions/matches${params}`;
  return useSWR<MatchSummary[]>(key);
}

export function useMatchDetail(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/predictions/matches/${matchId}` : null;
  return useSWR<MatchDetailResponse>(key);
}

export async function triggerPrediction(matchId: string): Promise<PredictionResult> {
  const result = await sportPost<PredictionResult>(
    `/predictions/matches/${matchId}/predict`,
  );
  await mutate(`${getApiBase()}/predictions/matches/${matchId}`);
  return result;
}
