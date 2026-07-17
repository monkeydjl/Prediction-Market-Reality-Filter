"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery } from "../client";
import type { SportRecommendation, RecommendationList } from "../types";

export function useRecommendation(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-recommendations/${matchId}` : null;
  return useSWR<SportRecommendation>(key);
}

export function useOpenDecisions(params?: { limit?: number; decision?: string }) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/sport-recommendations/open${qs}`;
  return useSWR<RecommendationList>(key);
}

export function useTopPicks(params?: { limit?: number; min_abs_edge?: number }) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/sport-recommendations/discrepancies${qs}`;
  return useSWR<RecommendationList>(key);
}
