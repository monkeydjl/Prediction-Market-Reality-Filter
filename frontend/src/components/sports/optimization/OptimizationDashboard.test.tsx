import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/optimization-api", () => ({
  fetchOptimizationParams: vi.fn(),
}));

import { OptimizationDashboard } from "./OptimizationDashboard";
import { fetchOptimizationParams } from "@/lib/optimization-api";

describe("OptimizationDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(fetchOptimizationParams).mockReturnValue(new Promise(() => {}));
    render(<OptimizationDashboard />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when no params", async () => {
    vi.mocked(fetchOptimizationParams).mockResolvedValue([]);
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("displays params table when data available", async () => {
    vi.mocked(fetchOptimizationParams).mockResolvedValue([
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
    ]);
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("params-table")).toBeTruthy();
    });
    expect(screen.getByText("nba")).toBeTruthy();
    // Score 0.75 renders via toFixed(4) as "0.7500"
    expect(screen.getByText("0.7500")).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    vi.mocked(fetchOptimizationParams).mockRejectedValue(new Error("503"));
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });
});
