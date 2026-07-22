"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";

export type BettingCatalogCompetition = {
  id: string;
  sport: string;
  label: string;
  short_label: string;
  description: string;
  status: "live" | "kernel" | "coming_soon";
  href: string;
  competition_code: string | null;
  kernel_sport: string | null;
  track: "kernel" | "world_cup" | "placeholder";
  section: string;
  adapter_likely?: boolean;
};

export type BettingCatalogTool = {
  id: string;
  href: string;
  title: string;
  description: string;
  section: string;
};

export type BettingCatalogFlags = {
  kernel_prediction_enabled?: boolean;
  phase2_leagues_enabled?: boolean;
  epl_data_enabled?: boolean;
  ucl_data_enabled?: boolean;
  phase4_nba_enabled?: boolean;
  phase5_mlb_enabled?: boolean;
  phase5_nhl_enabled?: boolean;
  phase_lol_enabled?: boolean;
  lol_dry_run_import?: boolean;
  lol_dry_run_path_configured?: boolean;
};

export type BettingCatalog = {
  version: number;
  sections: Record<string, string>;
  competitions: BettingCatalogCompetition[];
  tools: BettingCatalogTool[];
  flags?: BettingCatalogFlags;
  notes?: Record<string, unknown>;
};

/** Optional live catalog from backend; static FE catalog remains the offline default. */
export function useBettingCatalog() {
  const key = `${getApiBase()}/betting/catalog`;
  return useSWR<BettingCatalog>(key, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
}

export type BettingLolStatus = {
  schedule_vendor?: string;
  effective_schedule_vendor?: string;
  schedule_source_blocked?: boolean;
  schedule_source_reason?: string | null;
  vendor_api_base_configured?: boolean;
  vendor_api_key_configured?: boolean;
  settle_grace_hours?: number;
  production_http_client_ready?: boolean;
};

export type BettingStatus = {
  version: number;
  flags?: BettingCatalogFlags;
  lol?: BettingLolStatus;
  kernel_ready: boolean;
  registered_prefixes: string[];
  kernel_error: string | null;
  hint?: string;
};

/** Read-only operator diagnostic (flags + MultiAdapter prefixes). */
export function useBettingStatus() {
  const key = `${getApiBase()}/betting/status`;
  return useSWR<BettingStatus>(key, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
}
