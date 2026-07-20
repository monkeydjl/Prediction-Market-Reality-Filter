"use client";

import useSWR, { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery, sportPost } from "../client";
import type { SettlementList, CalibrationList } from "../types";

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

export async function processSettlement(matchId: string): Promise<void> {
  await sportPost(`/sport-settlements/process/${matchId}`);
  await mutate(`${getApiBase()}/sport-settlements/history`);
}
