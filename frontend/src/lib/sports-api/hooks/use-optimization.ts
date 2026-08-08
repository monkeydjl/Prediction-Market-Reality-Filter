"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type { ApplyParamsResult, OptimizedParams } from "../types";

export function useOptimizationParams() {
  const key = `${getApiBase()}/sport-optimization/params`;
  return useSWR<OptimizedParams[]>(key);
}

/** Applied params for one sport. Returns 404 until a candidate is applied. */
export function useAppliedParams(sport: string | null) {
  const key = sport
    ? `${getApiBase()}/sport-optimization/params/${encodeURIComponent(sport)}`
    : null;
  return useSWR<OptimizedParams>(key);
}

export interface BackfillSeedResult {
  backfill?: Record<string, unknown>;
  seed?: Record<string, unknown>;
}

/**
 * Backfill match results from synced fixtures and/or seed Elo ratings. Run
 * after a schedule sync when fixtures have scores but the result and Elo
 * tables are still empty. Idempotent on the backend.
 */
export async function backfillAndSeed(
  sport: string = "all",
  options: { backfill?: boolean; seedElo?: boolean } = {},
): Promise<BackfillSeedResult> {
  return sportPost<BackfillSeedResult>(`/sport-optimization/backfill-seed`, {
    sport,
    backfill: options.backfill ?? true,
    seed_elo: options.seedElo ?? true,
  });
}

export async function triggerOptimization(
  sport: string,
  nTrials: number = 150,
): Promise<{ task_id: string }> {
  const result = await sportPost<{ task_id: string }>(
    `/sport-optimization/run`,
    { sport, n_trials: nTrials },
  );
  await mutate(`${getApiBase()}/sport-optimization/params`);
  return result;
}

export async function triggerIngest(
  sport: string,
  seasons: string[],
): Promise<Record<string, unknown>> {
  const result = await sportPost<Record<string, unknown>>(
    `/sport-optimization/ingest`,
    { sport, seasons },
  );
  return result;
}

export interface TaskStatus {
  task_id: string;
  engine_name?: string;
  status: "pending" | "running" | "completed" | "failed";
  progress: number;
  total?: number;
  current_match?: string | null;
  /** Shape: { sports: { [sport]: optimize_sync result } } when completed */
  result: unknown;
  error: string | null;
  created_at: string;
  updated_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  logs?: Array<Record<string, unknown>>;
}

export function useTaskStatus(taskId: string | null) {
  const key = taskId
    ? `${getApiBase()}/sport-optimization/status/${taskId}`
    : null;
  return useSWR<TaskStatus>(key, {
    refreshInterval: (data) => {
      if (!data) return 2000;
      return data.status === "completed" || data.status === "failed" ? 0 : 2000;
    },
  });
}

export async function applyParams(paramsId: number): Promise<ApplyParamsResult> {
  const result = await sportPost<ApplyParamsResult>(
    `/sport-optimization/apply/${paramsId}`,
  );
  await mutate(`${getApiBase()}/sport-optimization/params`);
  return result;
}
