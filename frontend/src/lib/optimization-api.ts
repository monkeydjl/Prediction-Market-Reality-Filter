"use client";

export interface OptimizedParams {
  id: number;
  sport: string;
  competition: string;
  factor_weights: string;
  elo_params: string;
  score: number;
  accuracy: number;
  brier_score: number;
  mae: number;
  sample_count: number;
  trial_number: number | null;
  status: string;
  created_at: string | null;
  applied_at: string | null;
}

function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "";
  return base.replace(/\/api$/, "");
}

export async function fetchOptimizationParams(): Promise<OptimizedParams[]> {
  const url = `${getApiBase()}/api/sport-optimization/params`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export async function triggerOptimization(sport: string, nTrials: number = 150): Promise<{ task_id: string }> {
  const url = `${getApiBase()}/api/sport-optimization/run`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sport, n_trials: nTrials }),
  });
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}
