import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MatchPredictionCard } from "./match-prediction-card";
import type {
  MatchFixtureRecord,
  MatchPredictionPayload,
} from "@/lib/generated-types";

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

// Every key of the published payload is present: `engine_used` is written by
// `_engine_used_from_method`, which maps `rule_only` onto `"hybrid"` because
// the rule engine *is* the hybrid engine's rule half (audit 53.76).
const ruleOnlyPrediction: MatchPredictionPayload = {
  predicted_score: { home: 2, away: 1 },
  outcome_probabilities: {
    home_win: 0.5,
    draw: 0.25,
    away_win: 0.25,
  },
  confidence: 0.72,
  prediction_method: "rule_only",
  engine_used: "hybrid",
  ai_reasoning: null,
  key_factors: null,
  last_updated: null,
  has_betting_odds: false,
  data_quality: "partial",
  data_quality_notes: [],
  data_quality_score: null,
  raw_confidence: null,
  confidence_calibration: null,
  high_confidence_selection: null,
  explanation_contributions: null,
};

describe("MatchPredictionCard hybrid fallback label", () => {
  it("shows rule-only predictions as the hybrid engine", () => {
    render(<MatchPredictionCard match={match} prediction={ruleOnlyPrediction} />);

    expect(screen.getByText("混合引擎")).toBeInTheDocument();
  });
});
