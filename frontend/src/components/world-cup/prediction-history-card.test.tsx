import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PredictionHistoryCard } from "./prediction-history-card";
import type { MatchFixture } from "@/lib/world-cup-predictions";
import { fetchPredictionHistory } from "@/lib/world-cup-predictions";
import { analyticsApi } from "@/lib/analytics-api";

vi.mock("@/lib/world-cup-predictions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/world-cup-predictions")>();
  return {
    ...actual,
    fetchPredictionHistory: vi.fn(),
  };
});

vi.mock("@/lib/analytics-api", () => ({
  analyticsApi: {
    predictionTimeline: vi.fn(),
  },
}));

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
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(analyticsApi.predictionTimeline).mockResolvedValue({ match_id: "match-1", snapshots: [] });
  });

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
    vi.mocked(analyticsApi.predictionTimeline).mockResolvedValue({ match_id: "match-1", snapshots: [] });
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

  it("shows analytics timeline snapshot count when available", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValue([
      {
        timestamp: "2026-06-24T12:00:00Z",
        predicted_score: { home: 2, away: 1 },
        outcome_probabilities: { home_win: 0.6, draw: 0.25, away_win: 0.15 },
        confidence: 0.65,
        trigger: "manual",
        prediction_method: "elo_only",
      },
    ]);
    vi.mocked(analyticsApi.predictionTimeline).mockResolvedValue({
      match_id: "match-1",
      snapshots: [
        {
          timestamp: "2026-06-24T12:00:00Z",
          predicted_score: { home: 2, away: 1 },
          outcome_probabilities: { home_win: 0.6, draw: 0.25, away_win: 0.15 },
          confidence: 0.65,
          trigger: "manual",
          match_minute: null,
          actual_score: null,
        },
        {
          timestamp: "2026-06-24T13:00:00Z",
          predicted_score: { home: 1, away: 1 },
          outcome_probabilities: { home_win: 0.4, draw: 0.3, away_win: 0.3 },
          confidence: 0.55,
          trigger: "odds_update",
          match_minute: null,
          actual_score: null,
        },
      ],
    });

    render(<PredictionHistoryCard match={match} />);

    expect(await screen.findByText("Analytics timeline")).toBeInTheDocument();
    expect(screen.getByText("2 snapshots")).toBeInTheDocument();
    expect(screen.getByText("odds_update")).toBeInTheDocument();
  });
});
