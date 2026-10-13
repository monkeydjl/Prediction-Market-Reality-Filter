"use client";

import useSWR from "swr";
import { buildApiErrorMessage, buildOperatorAuthHeaders } from "@/lib/api";
import { getApiBase } from "@/lib/env";
import type { MatchListResponse, MatchWithPrediction } from "./predictions-api";

const API_BASE = getApiBase();

// Bounded fetcher for SWR. Mirrors the auth/timeout behavior of the central
// `api()` client in lib/api.ts so the World Cup matches endpoint receives the
// same X-API-Key / X-Operator session headers, is bounded by an
// AbortController timeout (a hung backend no longer leaves SWR spinning
// indefinitely), and surfaces a localized error message instead of the opaque
// `Failed to fetch matches: <statusText>`.
const MATCHES_FETCHER_TIMEOUT_MS = 60_000;

async function matchesFetcher(url: string): Promise<MatchWithPrediction[]> {
  const headers: Record<string, string> = { ...buildOperatorAuthHeaders() };

  const controller = new AbortController();
  const timeout = globalThis.setTimeout(
    () => controller.abort(),
    MATCHES_FETCHER_TIMEOUT_MS,
  );

  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers,
      signal: controller.signal,
    });
    if (!response.ok) {
      const bodyText = await response.text();
      throw new Error(buildApiErrorMessage(response.status, bodyText));
    }
    // The envelope is the generated `MatchListResponse` (audit 53.76), so
    // `matches` is required rather than guessed at with a hand-written inline
    // shape: the route validates the key, which means a missing one surfaces
    // as a backend failure instead of a silently empty board.
    const data = (await response.json()) as MatchListResponse;
    return data.matches;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    if (error instanceof TypeError) {
      // Network-level failure (DNS, connection refused, CORS preflight fail).
      throw new Error("无法连接到服务器，请检查网络或后端服务状态");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
  }
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
  const url = `${API_BASE}/world-cup/predictions/matches?${query}`;

  return useSWR<MatchWithPrediction[]>(url, matchesFetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
    errorRetryCount: 2,
  });
}
