import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MatchPredictionCard } from "./match-prediction-card";
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

function prediction(overrides: Partial<MatchPrediction>): MatchPrediction {
  return {
    predicted_score: { home: 2, away: 1 },
    outcome_probabilities: {
      home_win: 0.5,
      draw: 0.25,
      away_win: 0.25,
    },
    confidence: 0.72,
    ...overrides,
  };
}

describe("MatchPredictionCard engine label", () => {
  it("shows Elo+赔率 when the applied engine is elo_odds", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({ engine_used: "elo_odds" })}
      />
    );

    expect(screen.getByText("Elo+赔率")).toBeInTheDocument();
  });

  it("shows Elo评级 for Elo-only predictions", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "elo_odds",
          prediction_method: "elo_only",
        })}
      />
    );

    expect(screen.getByText("Elo评级")).toBeInTheDocument();
  });

  it("shows 混合引擎 when the applied engine is hybrid", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({ engine_used: "hybrid" })}
      />
    );

    expect(screen.getByText("混合引擎")).toBeInTheDocument();
  });
});
