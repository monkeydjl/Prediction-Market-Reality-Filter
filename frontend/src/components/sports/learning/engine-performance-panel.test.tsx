import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useEngineScores: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useEngineScores: apiMocks.useEngineScores,
}));

import { EnginePerformancePanel } from "./engine-performance-panel";

const mockScore = {
  engine: "basketball",
  competition: "nba",
  accuracy: 0.625,
  avg_mae: 3.2,
  brier_score: 0.21,
  sample_count: 48,
  confidence_calibration: 0.94,
  last_updated: "2026-07-14T18:30:00Z",
};

describe("EnginePerformancePanel", () => {
  beforeEach(() => {
    apiMocks.useEngineScores.mockReset();
  });

  it("renders filter dropdowns", async () => {
    apiMocks.useEngineScores.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("引擎")).toBeInTheDocument();
      expect(screen.getByText("赛事")).toBeInTheDocument();
      expect(screen.getByText("运动")).toBeInTheDocument();
    });
  });

  it("renders data table with scores", async () => {
    apiMocks.useEngineScores.mockReturnValue({
      data: [mockScore],
      error: undefined,
      isLoading: false,
    });
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      // Use cell role to disambiguate from <option> elements in filter dropdowns
      expect(screen.getByRole("cell", { name: "basketball" })).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "nba" })).toBeInTheDocument();
      expect(screen.getByText("62.5%")).toBeInTheDocument();
    });
  });

  it("renders empty state when no data", async () => {
    apiMocks.useEngineScores.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("暂无性能数据，等待比赛结果录入")).toBeInTheDocument();
    });
  });

  it("renders loading state", async () => {
    apiMocks.useEngineScores.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<EnginePerformancePanel />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("renders error state on fetch failure", async () => {
    apiMocks.useEngineScores.mockReturnValue({
      data: undefined,
      error: new Error("Network error"),
      isLoading: false,
    });
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument();
    });
  });

  it("applies green color class for high accuracy", async () => {
    apiMocks.useEngineScores.mockReturnValue({
      data: [{ ...mockScore, accuracy: 0.85 }],
      error: undefined,
      isLoading: false,
    });
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      const accuracyCell = screen.getByText("85.0%");
      expect(accuracyCell.className).toContain("text-green");
    });
  });
});
