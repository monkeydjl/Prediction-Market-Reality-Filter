import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EngineComparisonCard } from "./engine-comparison-card";
import type { MatchFixtureRecord } from "@/lib/generated-types";
import type { CompareOnlyPrediction } from "@/lib/world-cup/predictions-api";

const match: MatchFixtureRecord = {
  match_id: "match-1",
  fixture_id: "1",
  home_team: "Argentina",
  away_team: "Brazil",
  kickoff_utc: "2026-06-24T12:00:00Z",
  venue: "MetLife Stadium",
  stage: "GROUP_STAGE",
  group: "A",
  status: "scheduled",
  home_score: null,
  away_score: null,
};

// Only three keys are required on this one -- it is the `/predict` compare
// projection, not the `/matches` payload -- but the nested blobs are the
// generated ones, so they are spelled out in full (audit 53.76).
const prediction: CompareOnlyPrediction = {
  predicted_score: { home: 2, away: 1 },
  outcome_probabilities: {
    home_win: 0.6,
    draw: 0.25,
    away_win: 0.15,
  },
  confidence: 0.65,
  raw_confidence: 0.8,
  confidence_calibration: {
    raw: 0.8,
    calibrated: 0.65,
    method: "piecewise_linear_reliability",
    engine_filter: null,
    total_samples: 8,
    min_total_samples: 6,
    min_bucket_samples: 3,
    is_reliable: true,
    bucket_is_reliable: true,
    is_reference_only: false,
    bucket: null,
    applied_bucket: {
      label: "80-100%",
      count: 4,
      actual_accuracy: null,
      avg_confidence: null,
    },
    reason: "piecewise_linear_calibration",
  },
  explanation_contributions: {
    engine: "elo_odds",
    home_team: "Argentina",
    away_team: "Brazil",
    prediction_method: "elo_only",
    engine_weights: null,
    items: [
      {
        key: "elo",
        label: "Elo",
        unit: "pp",
        home_impact: 5,
        away_impact: -3,
        description: "rating edge",
        available: true,
      },
    ],
  },
  prediction_method: "elo_only",
  engine_used: "elo_odds",
};

describe("EngineComparisonCard metadata", () => {
  it("shows calibration and contribution details for compare-only predictions", () => {
    render(
      <EngineComparisonCard
        match={match}
        eloOddsPrediction={prediction}
      />
    );

    expect(screen.getByText("Calibration")).toBeInTheDocument();
    expect(screen.getByText(/80%.*65%.*n=4/)).toBeInTheDocument();
    expect(screen.getByText("Elo")).toBeInTheDocument();
    expect(screen.getByText("+5.0pp/-3.0pp")).toBeInTheDocument();
  });
});
