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

const ruleOnlyPrediction: MatchPrediction = {
  predicted_score: { home: 2, away: 1 },
  outcome_probabilities: {
    home_win: 0.5,
    draw: 0.25,
    away_win: 0.25,
  },
  confidence: 0.72,
  prediction_method: "rule_only",
};

describe("MatchPredictionCard hybrid fallback label", () => {
  it("shows rule-only predictions as the hybrid engine", () => {
    render(<MatchPredictionCard match={match} prediction={ruleOnlyPrediction} />);

    expect(screen.getByText("混合引擎")).toBeInTheDocument();
  });
});
