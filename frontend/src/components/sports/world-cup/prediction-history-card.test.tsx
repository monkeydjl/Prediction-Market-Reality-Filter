import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PredictionHistoryCard } from "./prediction-history-card";
import type { MatchFixture } from "@/lib/world-cup/predictions-api";
import { fetchPredictionHistory } from "@/lib/world-cup/predictions-api";

vi.mock("@/lib/world-cup/predictions-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/world-cup/predictions-api")>();
  return {
    ...actual,
    fetchPredictionHistory: vi.fn(),
  };
});

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

describe("PredictionHistoryCard engine label", () => {
  it("shows Elo-only history as the public Elo+赔率 engine", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValue([
      {
        timestamp: "2026-06-24T12:00:00Z",
        predicted_score: { home: 1.03, away: 1.61 },
        outcome_probabilities: { home_win: 0.3, draw: 0.25, away_win: 0.45 },
        confidence: 0.72,
        trigger: "manual",
        prediction_method: "elo_only",
      },
    ]);

    render(<PredictionHistoryCard match={match} />);

    expect(await screen.findByText("Elo+赔率")).toBeInTheDocument();
  });

  it("shows integrated methods as the integrated engine", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValue([
      {
        timestamp: "2026-06-24T12:00:00Z",
        predicted_score: { home: 1.03, away: 1.61 },
        outcome_probabilities: { home_win: 0.3, draw: 0.25, away_win: 0.45 },
        confidence: 0.72,
        trigger: "manual",
        prediction_method: "integrated (elo_odds 40% + hybrid 60%)",
      },
    ]);

    render(<PredictionHistoryCard match={match} />);

    expect(await screen.findByText("集成引擎")).toBeInTheDocument();
  });

  it("shows calibration and contribution metadata when history includes it", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValue([
      {
        timestamp: "2026-06-24T12:00:00Z",
        predicted_score: { home: 2, away: 1 },
        outcome_probabilities: { home_win: 0.6, draw: 0.25, away_win: 0.15 },
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
        trigger: "manual",
        prediction_method: "elo_only",
      },
    ]);

    render(<PredictionHistoryCard match={match} />);

    expect(await screen.findByText("Calibration")).toBeInTheDocument();
    expect(screen.getByText(/80%.*65%.*n=4/)).toBeInTheDocument();
    expect(screen.getByText("Contrib")).toBeInTheDocument();
    expect(screen.getByText(/Elo \+5\.0pp\/-3\.0pp/)).toBeInTheDocument();
  });

  it("labels prediction history rows with missing or degraded data quality", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValue([
      {
        timestamp: "2026-06-24T12:00:00Z",
        predicted_score: { home: 1, away: 1 },
        outcome_probabilities: { home_win: 0.34, draw: 0.33, away_win: 0.33 },
        confidence: 0.52,
        trigger: "daily_update",
        prediction_method: "hybrid",
        data_quality: "partial",
        data_quality_notes: ["data_quality_missing"],
      },
    ]);

    render(<PredictionHistoryCard match={match} />);

    expect(await screen.findByText("历史质量未标记")).toBeInTheDocument();
  });
});
