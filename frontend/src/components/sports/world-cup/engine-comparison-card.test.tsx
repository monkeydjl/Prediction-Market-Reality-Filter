import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EngineComparisonCard } from "./engine-comparison-card";
import type { MatchFixtureRecord } from "@/lib/generated-types";
import type { CompareOnlyPrediction } from "@/lib/world-cup/predictions-api";

const { triggerPredictionMock } = vi.hoisted(() => ({
  triggerPredictionMock: vi.fn(),
}));

vi.mock("@/lib/world-cup/predictions-api", async (importOriginal) => {
  const original = await importOriginal<
    typeof import("@/lib/world-cup/predictions-api")
  >();
  return { ...original, triggerPrediction: triggerPredictionMock };
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

// Audit 53.79: handleApply is the only live reader of the skip shape, and it
// had zero assertions. The in-band failure branch is gone on purpose -- the
// route turns pipeline errors into HTTP 500 -- so these two cases pin what
// remains: the two-key skip surfacing, and the success path notifying the
// parent.
describe("EngineComparisonCard apply", () => {
  beforeEach(() => {
    triggerPredictionMock.mockReset();
  });

  it("surfaces the two-key skip shape instead of a false applied state", async () => {
    triggerPredictionMock.mockResolvedValueOnce({
      status: "skipped",
      reason: "Match already started",
    });
    render(<EngineComparisonCard match={match} eloOddsPrediction={prediction} />);

    fireEvent.click(screen.getByText("应用此预测"));

    expect(await screen.findByText(/无法应用：Match already started/)).toBeInTheDocument();
    expect(screen.queryByText("已应用")).not.toBeInTheDocument();
  });

  it("falls back to the generic skip message when the reason is missing", async () => {
    triggerPredictionMock.mockResolvedValueOnce({ status: "skipped" });
    render(<EngineComparisonCard match={match} eloOddsPrediction={prediction} />);

    fireEvent.click(screen.getByText("应用此预测"));

    expect(await screen.findByText(/无法应用：比赛已开赛或已结束/)).toBeInTheDocument();
  });

  it("marks applied and notifies the parent on an ok envelope", async () => {
    triggerPredictionMock.mockResolvedValueOnce({
      status: "ok",
      action: "updated",
      match_id: "match-1",
    });
    const onApplyPrediction = vi.fn();
    render(
      <EngineComparisonCard
        match={match}
        eloOddsPrediction={prediction}
        onApplyPrediction={onApplyPrediction}
      />
    );

    fireEvent.click(screen.getByText("应用此预测"));

    expect(await screen.findByText("已应用")).toBeInTheDocument();
    await waitFor(() => expect(onApplyPrediction).toHaveBeenCalledTimes(1));
  });
});
