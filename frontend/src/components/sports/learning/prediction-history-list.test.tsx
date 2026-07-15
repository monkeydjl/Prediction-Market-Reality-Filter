import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PredictionHistoryList } from "./prediction-history-list";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

// Mock learning-api
vi.mock("@/lib/learning-api", () => ({
  fetchPredictionHistory: vi.fn(),
}));

import { fetchPredictionHistory } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchPredictionHistory).mockReset();
});

const mockItem = {
  id: 1,
  match_id: "nba-20250101-LAL-BOS",
  sport: "basketball",
  competition: "nba",
  engine: "basketball",
  predicted_scores: { home: 112, away: 108 },
  outcome_probabilities: { home_win: 0.62, away_win: 0.38 },
  confidence: 0.59,
  feature_version: "nba-1.0",
  trigger: "initial",
  created_at: "2026-07-14T18:30:00Z",
  outcome: {
    home_score: 113,
    away_score: 107,
    outcome: "home_win",
    outcome_correct: 1,
    score_mae: 2.5,
    brier_score: 0.19,
    finished_at: "2026-07-15T02:00:00Z",
  },
};

describe("PredictionHistoryList", () => {
  it("renders table rows with history data", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [mockItem], total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("nba-20250101-LAL-BOS")).toBeInTheDocument();
    });
  });

  it("shows — for outcome=null (unfinished)", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [{ ...mockItem, outcome: null }], total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      // Deviation: outcome=null causes BOTH the result cell (resultBadge returns "—")
      // AND the MAE cell (`outcome?.score_mae?.toFixed(2) ?? "—"`) to render "—".
      // getByText would throw "multiple elements"; use getAllByText to disambiguate.
      expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });
  });

  it("shows 待算 for outcome_correct=null", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [{
        ...mockItem,
        outcome: { ...mockItem.outcome!, outcome_correct: null },
      }],
      total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("待算")).toBeInTheDocument();
    });
  });

  it("renders row as link to trajectory page", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [mockItem], total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      const link = screen.getByRole("link");
      expect(link.getAttribute("href")).toBe("/sports/learning/history/nba-20250101-LAL-BOS");
    });
  });

  it("renders pagination controls", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [mockItem], total: 100, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("下一页")).toBeInTheDocument();
    });
  });

  it("renders empty state", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [], total: 0, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("暂无预测历史记录")).toBeInTheDocument();
    });
  });
});
