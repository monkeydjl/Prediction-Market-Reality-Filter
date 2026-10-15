import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PredictionAnalysisCard } from "./prediction-analysis-card";
import type {
  MatchFixtureRecord,
  MatchPredictionPayload,
} from "@/lib/generated-types";
import {
  fetchAnalysisHistory,
  optimizePrediction,
} from "@/lib/world-cup/predictions-api";

vi.mock("@/lib/world-cup/predictions-api", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/world-cup/predictions-api")
  >();
  return {
    ...actual,
    fetchAnalysisHistory: vi.fn(),
    analyzePrediction: vi.fn(),
    optimizePrediction: vi.fn(),
  };
});

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

const prediction: MatchPredictionPayload = {
  predicted_score: { home: 2, away: 1 },
  outcome_probabilities: { home_win: 0.55, draw: 0.25, away_win: 0.2 },
  confidence: 0.71,
  prediction_method: null,
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

describe("PredictionAnalysisCard analysis history", () => {
  it("renders a null created_at as a dash rather than crashing", async () => {
    vi.mocked(fetchAnalysisHistory).mockResolvedValue([
      {
        id: 8,
        analysis: "Older analysis",
        predicted_score: { home: 1, away: 1 },
        confidence: 0.6,
        // Both are nullable on the generated AnalysisHistoryEntry: the
        // producer writes `prediction_method` straight off a nullable column
        // and `created_at` behind an `if ... else None` (audit 53.75).
        prediction_method: null,
        created_at: null,
      },
    ]);

    render(<PredictionAnalysisCard match={match} prediction={prediction} />);

    expect(await screen.findByText("历史分析")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    // The float score pair renders through unchanged.
    expect(screen.getByText("1-1")).toBeInTheDocument();
  });

  it("formats a real created_at instead of falling back to the dash", async () => {
    vi.mocked(fetchAnalysisHistory).mockResolvedValue([
      {
        id: 9,
        analysis: "Newer analysis",
        predicted_score: { home: 2, away: 1 },
        confidence: 0.71,
        prediction_method: "elo_odds",
        created_at: "2026-10-13T00:00:00+00:00",
      },
    ]);

    render(<PredictionAnalysisCard match={match} prediction={prediction} />);

    expect(await screen.findByText("历史分析")).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
    expect(screen.getByText("2-1")).toBeInTheDocument();
  });
});

describe("PredictionAnalysisCard optimize path", () => {
  // The optimize consume path had zero assertions before audit 53.78; these
  // fixtures are typed against the generated `PredictionOptimizeResponse`.
  it("renders blind spots and the optimized prediction as analysis text", async () => {
    vi.mocked(optimizePrediction).mockResolvedValue({
      status: "ok",
      match_id: "match-1",
      original_prediction: {
        predicted_score: { home: 2, away: 1 },
        outcome_probabilities: { home_win: 0.55, draw: 0.25, away_win: 0.2 },
        confidence: 0.71,
      },
      optimization: {
        blind_spots: ["忽视了两队近 5 场的防守差异"],
        calibration_issues: [],
        optimized_prediction: {
          predicted_score: { home: 1.8, away: 1.2 },
          outcome_probabilities: {
            home_win: 0.5,
            draw: 0.28,
            away_win: 0.22,
          },
          confidence: 0.66,
          reasoning: "基于近期防守数据的微调",
        },
      },
    });

    render(<PredictionAnalysisCard match={match} prediction={prediction} />);

    const optimizeButton = (await screen.findByText("AI优化")) as HTMLButtonElement;
    optimizeButton.click();

    expect(await screen.findByText(/数据盲点识别/)).toBeInTheDocument();
    expect(screen.getByText(/忽视了两队近 5 场的防守差异/)).toBeInTheDocument();
    expect(screen.getByText(/优化后预测/)).toBeInTheDocument();
    expect(screen.getByText(/2\.0-1\.0 → 1\.8-1\.2/)).toBeInTheDocument();
    expect(screen.getByText(/调整理由/)).toBeInTheDocument();
  });

  it("falls back to raw_text when the LLM answer is unstructured", async () => {
    vi.mocked(optimizePrediction).mockResolvedValue({
      status: "ok",
      match_id: "match-1",
      original_prediction: {
        predicted_score: { home: 2, away: 1 },
        outcome_probabilities: { home_win: 0.55, draw: 0.25, away_win: 0.2 },
        confidence: 0.71,
      },
      optimization: {
        raw_text: "模型未返回结构化 JSON",
      },
    });

    render(<PredictionAnalysisCard match={match} prediction={prediction} />);

    const optimizeButton = (await screen.findByText("AI优化")) as HTMLButtonElement;
    optimizeButton.click();

    expect(
      await screen.findByText(/模型未返回结构化 JSON/)
    ).toBeInTheDocument();
  });
});
