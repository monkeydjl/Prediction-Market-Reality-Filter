"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type { MatchSummary, MatchDetail, PredictionResult } from "../types";

type MatchDetailResponse = { match: MatchDetail; prediction: PredictionResult | null };

export type MatchListFilters = {
  sport?: string | null;
  competition?: string | null;
};

export function useMatches(sportOrFilters?: string | MatchListFilters | null) {
  // Explicit null disables the request (e.g. coming_soon / world_cup landing).
  let key: string | null = null;
  if (sportOrFilters !== null) {
    const filters: MatchListFilters =
      typeof sportOrFilters === "string" || sportOrFilters === undefined
        ? { sport: sportOrFilters ?? undefined }
        : sportOrFilters;
    const params = new URLSearchParams();
    if (filters.sport) params.set("sport", filters.sport);
    if (filters.competition) params.set("competition", filters.competition);
    const qs = params.toString() ? `?${params.toString()}` : "";
    key = `${getApiBase()}/predictions/matches${qs}`;
  }
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
