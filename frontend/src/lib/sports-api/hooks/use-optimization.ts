"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type { OptimizedParams } from "../types";

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
