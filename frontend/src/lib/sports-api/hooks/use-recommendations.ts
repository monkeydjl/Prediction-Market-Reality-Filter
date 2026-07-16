"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { SportRecommendation, RecommendationList } from "../types";

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");
}

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
