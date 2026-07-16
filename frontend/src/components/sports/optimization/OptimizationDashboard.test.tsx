import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useOptimizationParams: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useOptimizationParams: apiMocks.useOptimizationParams,
}));

import { OptimizationDashboard } from "./OptimizationDashboard";

describe("OptimizationDashboard", () => {
  beforeEach(() => {
    apiMocks.useOptimizationParams.mockReset();
  });

  it("shows loading state initially", () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<OptimizationDashboard />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when no params", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("displays params table when data available", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [
        {
          id: 1,
          sport: "nba",
          competition: "nba",
          factor_weights: '{"elo": 0.50}',
          elo_params: '{"hfa": 110}',
          score: 0.75,
          accuracy: 0.70,
          brier_score: 0.20,
          mae: 0.30,
          sample_count: 100,
          status: "applied",
          created_at: "2026-07-16T10:00:00Z",
          applied_at: "2026-07-16T12:00:00Z",
        },
      ],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("params-table")).toBeTruthy();
    });
    expect(screen.getByText("nba")).toBeTruthy();
    // Score 0.75 renders via toFixed(4) as "0.7500"
    expect(screen.getByText("0.7500")).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: undefined,
      error: new Error("503"),
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });
});
