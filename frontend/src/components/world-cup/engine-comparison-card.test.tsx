import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EngineComparisonCard } from "./engine-comparison-card";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup-predictions";

const match: MatchFixture = {
  match_id: "match-1",
  fixture_id: 1,
  home_team: "Argentina",
  away_team: "Brazil",
  kickoff_utc: "2026-06-24T12:00:00Z",
  venue: "MetLife Stadium",
  stage: "GROUP_STAGE",
  group: "A",
  status: "scheduled",
};

const prediction: MatchPrediction = {
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
    total_samples: 8,
    applied_bucket: { label: "80-100%", count: 4 },
  },
  explanation_contributions: {
    engine: "elo_odds",
    items: [
      {
        key: "elo",
        label: "Elo",
        unit: "pp",
        home_impact: 5,
        away_impact: -3,
        description: "rating edge",
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
