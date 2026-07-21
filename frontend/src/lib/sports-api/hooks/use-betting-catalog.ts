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
