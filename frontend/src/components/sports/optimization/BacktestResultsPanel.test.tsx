import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BacktestResultsPanel } from "./BacktestResultsPanel";
import type { SportBacktestMetrics } from "@/lib/sports-api/backtest-results";

vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => null,
}));

vi.mock("recharts", () => ({
  BarChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="bar-chart">{children}</div>
  ),
  Bar: () => null,
  CartesianGrid: () => null,
  Legend: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const nba: SportBacktestMetrics = {
  sport: "nba",
  best_score: 0.72,
  accuracy: 0.68,
  brier_score: 0.21,
  mae: 0.3,
  sample_count: 120,
  train_count: 480,
  test_count: 120,
  trials: 50,
  factor_weights: { elo: 0.4 },
  elo_params: null,
  score_formula: "formula",
  error: null,
  match_count: null,
  saved_candidate_id: 9,
};

describe("BacktestResultsPanel", () => {
  it("renders empty state", () => {
    render(<BacktestResultsPanel rows={[]} />);
    expect(screen.getByTestId("backtest-results-panel-empty")).toBeTruthy();
  });

  it("renders metrics table and chart for success rows", () => {
    render(<BacktestResultsPanel rows={[nba]} />);
    expect(screen.getByTestId("backtest-results-panel")).toBeTruthy();
    expect(screen.getByTestId("backtest-results-panel-chart")).toBeTruthy();
    expect(screen.getByTestId("backtest-row-nba")).toHaveTextContent("68.0%");
    expect(screen.getByTestId("backtest-row-nba")).toHaveTextContent("id=9");
  });

  it("shows error text for failed sport", () => {
    render(
      <BacktestResultsPanel
        rows={[
          {
            ...nba,
            sport: "mlb",
            error: "Not enough matches",
            accuracy: null,
            best_score: null,
          },
        ]}
      />,
    );
    expect(screen.getByTestId("backtest-row-mlb")).toHaveTextContent(
      "Not enough matches",
    );
  });
});
