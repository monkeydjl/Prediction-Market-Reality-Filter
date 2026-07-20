/** Parse Phase 9 optimization task.result into UI-ready backtest rows. */

export interface SportBacktestMetrics {
  sport: string;
  best_score: number | null;
  accuracy: number | null;
  brier_score: number | null;
  mae: number | null;
  sample_count: number | null;
  train_count: number | null;
  test_count: number | null;
  trials: number | null;
  factor_weights: Record<string, number> | null;
  elo_params: Record<string, number> | null;
  score_formula: string | null;
  error: string | null;
  match_count: number | null;
  saved_candidate_id: number | null;
}

export interface ParsedOptimizationTaskResult {
  sports: SportBacktestMetrics[];
}

function asNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return null;
}

function asWeightMap(v: unknown): Record<string, number> | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const out: Record<string, number> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    const n = asNumber(val);
    if (n != null) out[k] = n;
  }
  return Object.keys(out).length ? out : null;
}

function parseSportEntry(sport: string, raw: unknown): SportBacktestMetrics {
  if (!raw || typeof raw !== "object") {
    return {
      sport,
      best_score: null,
      accuracy: null,
      brier_score: null,
      mae: null,
      sample_count: null,
      train_count: null,
      test_count: null,
      trials: null,
      factor_weights: null,
      elo_params: null,
      score_formula: null,
      error: "invalid result",
      match_count: null,
      saved_candidate_id: null,
    };
  }
  const o = raw as Record<string, unknown>;
  const saved =
    o.saved_candidate && typeof o.saved_candidate === "object"
      ? (o.saved_candidate as Record<string, unknown>)
      : null;
  return {
    sport,
    best_score: asNumber(o.best_score),
    accuracy: asNumber(o.accuracy) ?? asNumber(saved?.accuracy),
    brier_score: asNumber(o.brier_score) ?? asNumber(saved?.brier_score),
    mae: asNumber(o.mae) ?? asNumber(saved?.mae),
    sample_count: asNumber(o.sample_count) ?? asNumber(saved?.sample_count),
    train_count: asNumber(o.train_count),
    test_count: asNumber(o.test_count),
    trials: asNumber(o.trials),
    factor_weights: asWeightMap(o.factor_weights),
    elo_params: asWeightMap(o.elo_params),
    score_formula:
      typeof o.score_formula === "string" ? o.score_formula : null,
    error: typeof o.error === "string" ? o.error : null,
    match_count: asNumber(o.match_count),
    saved_candidate_id: asNumber(saved?.id),
  };
}

/**
 * Accepts task.result from GET /sport-optimization/status/{id}
 * shape: { sports: { nba: {...}, mlb: {...} } }
 */
export function parseOptimizationTaskResult(
  result: unknown,
): ParsedOptimizationTaskResult | null {
  if (!result || typeof result !== "object") return null;
  const root = result as Record<string, unknown>;
  const sportsRaw = root.sports;
  if (!sportsRaw || typeof sportsRaw !== "object" || Array.isArray(sportsRaw)) {
    return null;
  }
  const sports = Object.entries(sportsRaw as Record<string, unknown>).map(
    ([sport, entry]) => parseSportEntry(sport, entry),
  );
  if (!sports.length) return null;
  return { sports };
}

export interface MetricBarPoint {
  sport: string;
  label: string;
  accuracyPct: number | null;
  brier: number | null;
  mae: number | null;
  score: number | null;
}

export function toMetricBarPoints(
  rows: SportBacktestMetrics[],
): MetricBarPoint[] {
  return rows
    .filter((r) => !r.error)
    .map((r) => ({
      sport: r.sport,
      label: r.sport.toUpperCase(),
      accuracyPct: r.accuracy != null ? r.accuracy * 100 : null,
      brier: r.brier_score,
      mae: r.mae,
      score: r.best_score,
    }));
}

export function toCandidateBarPoints(
  candidates: Array<{
    id: number;
    sport: string;
    accuracy: number;
    brier_score: number;
    mae: number;
    score: number;
    status: string;
  }>,
): MetricBarPoint[] {
  return candidates.map((c) => ({
    sport: c.sport,
    label: `${c.sport.toUpperCase()} #${c.id}`,
    accuracyPct: c.accuracy * 100,
    brier: c.brier_score,
    mae: c.mae,
    score: c.score,
  }));
}
