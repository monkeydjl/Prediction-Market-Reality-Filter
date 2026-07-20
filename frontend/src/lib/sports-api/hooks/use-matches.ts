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

export async function triggerPrediction(
  matchId: string,
  engine: string = "auto",
): Promise<PredictionResult> {
  const qs = engine && engine !== "auto" ? `?engine=${encodeURIComponent(engine)}` : "?engine=auto";
  const result = await sportPost<PredictionResult>(
    `/predictions/matches/${matchId}/predict${qs}`,
  );
  await mutate(`${getApiBase()}/predictions/matches/${matchId}`);
  return result;
}

export function useEngines() {
  const key = `${getApiBase()}/predictions/engines`;
  return useSWR<string[]>(key);
}

export type EnginesMeta = {
  engines: string[];
  kernel_enabled: boolean;
  flags: {
    football_multi_factor?: boolean;
    dixon_coles?: boolean;
    gbm?: boolean;
    ensemble?: boolean;
    situational?: boolean;
  };
};

export function useEnginesMeta() {
  const key = `${getApiBase()}/predictions/engines/meta`;
  return useSWR<EnginesMeta>(key);
}
