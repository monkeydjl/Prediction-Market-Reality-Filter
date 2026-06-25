"use client";

import useSWR from "swr";
import { getWorldCupApiBase } from "./env";
import type { MatchWithPrediction } from "./world-cup-predictions";

const API_BASE = getWorldCupApiBase();

async function matchesFetcher(url: string): Promise<MatchWithPrediction[]> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch matches: ${response.statusText}`);
  }
  const data = await response.json();
  return data.matches || [];
}

/**
 * SWR hook for fetching World Cup matches with built-in caching,
 * deduplication, and revalidation.
 */
export function useWorldCupMatches(params?: {
  stage?: string;
  status?: string;
  limit?: number;
}) {
  const query = new URLSearchParams();
  if (params?.stage) query.set("stage", params.stage);
  if (params?.status) query.set("status", params.status);
  if (params?.limit) query.set("limit", params.limit.toString());

  // Stable key (no _t cache-buster) so SWR can deduplicate properly.
  const url = `${API_BASE}/api/world-cup/predictions/matches?${query}`;

  return useSWR<MatchWithPrediction[]>(url, matchesFetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
    errorRetryCount: 2,
  });
}
