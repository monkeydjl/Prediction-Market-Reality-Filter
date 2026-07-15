import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { EnginePerformancePanel } from "./engine-performance-panel";

// Mock learning-api
vi.mock("@/lib/learning-api", () => ({
  fetchEngineScores: vi.fn(),
}));

import { fetchEngineScores } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchEngineScores).mockReset();
});

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
  it("renders filter dropdowns", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("引擎")).toBeInTheDocument();
      expect(screen.getByText("赛事")).toBeInTheDocument();
      expect(screen.getByText("运动")).toBeInTheDocument();
    });
  });

  it("renders data table with scores", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([mockScore]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      // Use cell role to disambiguate from <option> elements in filter dropdowns
      expect(screen.getByRole("cell", { name: "basketball" })).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "nba" })).toBeInTheDocument();
      expect(screen.getByText("62.5%")).toBeInTheDocument();
    });
  });

  it("renders empty state when no data", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("暂无性能数据，等待比赛结果录入")).toBeInTheDocument();
    });
  });

  it("renders loading state", async () => {
    vi.mocked(fetchEngineScores).mockReturnValueOnce(new Promise(() => {})); // never resolves
    render(<EnginePerformancePanel />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("renders error state on fetch failure", async () => {
    vi.mocked(fetchEngineScores).mockRejectedValueOnce(new Error("Network error"));
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument();
    });
  });

  it("applies green color class for high accuracy", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([{ ...mockScore, accuracy: 0.85 }]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      const accuracyCell = screen.getByText("85.0%");
      expect(accuracyCell.className).toContain("text-green");
    });
  });
});
