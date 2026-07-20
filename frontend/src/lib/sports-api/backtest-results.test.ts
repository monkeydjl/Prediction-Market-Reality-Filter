import { describe, expect, it } from "vitest";
import {
  parseOptimizationTaskResult,
  toCandidateBarPoints,
  toMetricBarPoints,
} from "./backtest-results";

describe("parseOptimizationTaskResult", () => {
  it("returns null for empty input", () => {
    expect(parseOptimizationTaskResult(null)).toBeNull();
    expect(parseOptimizationTaskResult({})).toBeNull();
  });

  it("parses multi-sport success and error entries", () => {
    const parsed = parseOptimizationTaskResult({
      sports: {
        nba: {
          best_score: 0.72,
          accuracy: 0.68,
          brier_score: 0.21,
          mae: 0.3,
          sample_count: 120,
          train_count: 480,
          test_count: 120,
          trials: 50,
          factor_weights: { elo: 0.4, form: 0.3 },
          elo_params: { hfa: 100 },
          score_formula: "0.5*accuracy + 0.3*(1-brier) + 0.2*(1-mae)",
          saved_candidate: { id: 7, accuracy: 0.68 },
        },
        mlb: {
          error: "Not enough matches (2); ingest history first",
          match_count: 2,
        },
      },
    });
    expect(parsed).not.toBeNull();
    expect(parsed!.sports).toHaveLength(2);
    const nba = parsed!.sports.find((s) => s.sport === "nba")!;
    expect(nba.accuracy).toBe(0.68);
    expect(nba.saved_candidate_id).toBe(7);
    expect(nba.factor_weights?.elo).toBe(0.4);
    const mlb = parsed!.sports.find((s) => s.sport === "mlb")!;
    expect(mlb.error).toMatch(/Not enough/);
    expect(mlb.match_count).toBe(2);
  });

  it("falls back to saved_candidate metrics", () => {
    const parsed = parseOptimizationTaskResult({
      sports: {
        nhl: {
          best_score: 0.6,
          saved_candidate: {
            id: 3,
            accuracy: 0.55,
            brier_score: 0.25,
            mae: 0.4,
            sample_count: 80,
          },
        },
      },
    });
    const nhl = parsed!.sports[0];
    expect(nhl.accuracy).toBe(0.55);
    expect(nhl.brier_score).toBe(0.25);
  });
});

describe("toMetricBarPoints", () => {
  it("skips errored sports and scales accuracy", () => {
    const points = toMetricBarPoints([
      {
        sport: "nba",
        best_score: 0.7,
        accuracy: 0.65,
        brier_score: 0.2,
        mae: 0.3,
        sample_count: 10,
        train_count: 40,
        test_count: 10,
        trials: 5,
        factor_weights: null,
        elo_params: null,
        score_formula: null,
        error: null,
        match_count: null,
        saved_candidate_id: 1,
      },
      {
        sport: "mlb",
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
        error: "fail",
        match_count: 0,
        saved_candidate_id: null,
      },
    ]);
    expect(points).toHaveLength(1);
    expect(points[0].accuracyPct).toBeCloseTo(65);
  });
});

describe("toCandidateBarPoints", () => {
  it("maps candidates to chart points", () => {
    const points = toCandidateBarPoints([
      {
        id: 1,
        sport: "nba",
        accuracy: 0.7,
        brier_score: 0.18,
        mae: 0.25,
        score: 0.75,
        status: "candidate",
      },
    ]);
    expect(points[0].label).toBe("NBA #1");
    expect(points[0].score).toBe(0.75);
  });
});
