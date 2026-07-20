"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery } from "../client";

// --- Types ---

export interface EdgeSource {
  link_id: number;
  source: string;
  contract_id: string;
  implied_prob: number;
  liquidity: number | null;
  volume: number | null;
  weight: number;
  link_confidence: number;
}

export interface FactorDriver {
  factor: string;
  weight: number;
  impact: number;
  outcome_prob?: number;
  available?: boolean;
  detail?: string | null;
}

export interface EdgeResult {
  mapped_outcome: string;
  model_prob: number;
  market_prob: number;
  raw_edge: number;
  trust: number;
  liquidity_factor: number;
  adjusted_edge: number;
  spread: number | null;
  sources_count: number;
  stale: boolean;
  /** low | normal | high | critical — ops review queue priority */
  review_priority?: string;
  factor_drivers?: FactorDriver[] | null;
  factor_attribution?: string | null;
  captured_at: string;
  sources: EdgeSource[];
}

export interface EdgeLatestResponse {
  match_id: string;
  outcomes: EdgeResult[];
  engine_name: string | null;
  competition: string | null;
  prediction_timestamp: string | null;
  skipped: boolean;
  skip_reason: string | null;
}

export interface EdgeHistoryPoint {
  captured_at: string;
  model_prob: number;
  market_prob: number;
  raw_edge: number;
  adjusted_edge: number;
  stale: boolean;
}

export interface EdgeHistoryResponse {
  match_id: string;
  series: {
    mapped_outcome: string;
    snapshots: EdgeHistoryPoint[];
  }[];
}

export interface EdgeDiscrepancyItem {
  match_id: string;
  mapped_outcome: string;
  model_prob: number;
  market_prob: number;
  raw_edge: number;
  adjusted_edge: number;
  stale: boolean;
  trust?: number | null;
  liquidity_factor?: number | null;
  sources_count?: number | null;
  review_priority?: string;
  factor_drivers?: FactorDriver[] | null;
  factor_attribution?: string | null;
  captured_at: string;
}

export interface EdgeDiscrepanciesResponse {
  items: EdgeDiscrepancyItem[];
  total: number;
}

// --- Hooks ---

export function useEdgeLatest(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-edges/${matchId}/latest` : null;
  return useSWR<EdgeLatestResponse>(key);
}

/** On-demand edge computation (requires write key). */
export async function detectEdges(matchId: string): Promise<EdgeLatestResponse> {
  const { sportPost } = await import("../client");
  const { mutate } = await import("swr");
  const result = await sportPost<EdgeLatestResponse>(
    `/sport-edges/${matchId}/detect`,
  );
  await mutate(`${getApiBase()}/sport-edges/${matchId}/latest`);
  await mutate(`${getApiBase()}/sport-edges/${matchId}/history`);
  return result;
}

export function useEdgeHistory(matchId: string | null, mappedOutcome?: string) {
  const q = buildQuery({ mapped_outcome: mappedOutcome });
  const key = matchId ? `${getApiBase()}/sport-edges/${matchId}/history${q}` : null;
  return useSWR<EdgeHistoryResponse>(key);
}

export function useEdgeDiscrepancies(params?: { limit?: number; min_abs_edge?: number }) {
  const q = buildQuery({ limit: params?.limit ?? 20, min_abs_edge: params?.min_abs_edge });
  const key = `${getApiBase()}/sport-edges/discrepancies${q}`;
  return useSWR<EdgeDiscrepanciesResponse>(key);
}
