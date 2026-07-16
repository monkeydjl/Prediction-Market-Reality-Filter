"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { SettlementList, CalibrationList } from "../types";

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  if (entries.length === 0) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of entries) usp.set(k, String(v));
  return `?${usp.toString()}`;
}

export function useSettlement(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-settlements/${matchId}` : null;
  return useSWR<SettlementList>(key);
}

export function useSettlementHistory(limit: number = 20, engine?: string) {
  const q = buildQuery({ limit, engine });
  const key = `${getApiBase()}/sport-settlements/history${q}`;
  return useSWR<SettlementList>(key);
}

export function useCalibrations(engine?: string, competition?: string) {
  const q = buildQuery({ engine, competition });
  const key = `${getApiBase()}/sport-settlements/calibrations${q}`;
  return useSWR<CalibrationList>(key);
}
