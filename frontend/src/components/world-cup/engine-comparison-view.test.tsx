import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EngineComparisonView } from "./engine-comparison-view";

function stat(outcome_accuracy: number) {
  return {
    total_matches: 3,
    exact_score_rate: 0.33,
    outcome_accuracy,
    goal_diff_accuracy: 0.67,
    avg_score_error: 1.25,
    predictions: [],
  };
}

describe("EngineComparisonView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders all three engine summary columns", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        engines: {
          elo_odds: stat(0.6),
          hybrid: stat(0.7),
          integrated: stat(0.8),
        },
      }),
    }));

    render(<EngineComparisonView />);

    expect(await screen.findByRole("heading", { name: "Elo+赔率" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "混合引擎" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "集成引擎" })).toBeInTheDocument();
  });
});
