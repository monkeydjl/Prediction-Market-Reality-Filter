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
