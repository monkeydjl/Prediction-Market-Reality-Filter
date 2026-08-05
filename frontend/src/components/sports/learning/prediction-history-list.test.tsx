import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

const apiMocks = vi.hoisted(() => ({
  usePredictionHistory: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  usePredictionHistory: apiMocks.usePredictionHistory,
}));

import { PredictionHistoryList } from "./prediction-history-list";

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
  beforeEach(() => {
    apiMocks.usePredictionHistory.mockReset();
  });

  it("renders table rows with history data", async () => {
    apiMocks.usePredictionHistory.mockReturnValue({
      data: { items: [mockItem], total: 1, limit: 50, offset: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("nba-20250101-LAL-BOS")).toBeInTheDocument();
    });
  });

  it("shows — for outcome=null (unfinished)", async () => {
    apiMocks.usePredictionHistory.mockReturnValue({
      data: {
        items: [{ ...mockItem, outcome: null }],
        total: 1,
        limit: 50,
        offset: 0,
      },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      // Deviation: outcome=null causes BOTH the result cell (resultBadge returns "—")
      // AND the MAE cell (`outcome?.score_mae?.toFixed(2) ?? "—"`) to render "—".
      expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });
  });

  it("shows 待算 for outcome_correct=null", async () => {
    apiMocks.usePredictionHistory.mockReturnValue({
      data: {
        items: [{
          ...mockItem,
          outcome: { ...mockItem.outcome!, outcome_correct: null },
        }],
        total: 1,
        limit: 50,
        offset: 0,
      },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("待算")).toBeInTheDocument();
    });
  });

  it("renders row as link to trajectory page", async () => {
    apiMocks.usePredictionHistory.mockReturnValue({
      data: { items: [mockItem], total: 1, limit: 50, offset: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      const link = screen.getByRole("link");
      expect(link.getAttribute("href")).toBe(
        "/sports/learning/history/?matchId=nba-20250101-LAL-BOS",
      );
    });
  });

  it("renders pagination controls", async () => {
    apiMocks.usePredictionHistory.mockReturnValue({
      data: { items: [mockItem], total: 100, limit: 50, offset: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("下一页")).toBeInTheDocument();
    });
  });

  it("renders empty state", async () => {
    apiMocks.usePredictionHistory.mockReturnValue({
      data: { items: [], total: 0, limit: 50, offset: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("暂无预测历史记录")).toBeInTheDocument();
    });
  });

  it("refetches with competition filter when changed on page 1", async () => {
    // Regression: previously the fetch effect's deps were [sport, offset].
    // When on page 1 (offset=0) and competition changes, setOffset(0) bails
    // out (no ref change) so the fetch effect never re-fired and the new
    // competition value was never sent to fetchPredictionHistory.
    apiMocks.usePredictionHistory.mockReturnValue({
      data: { items: [mockItem], total: 1, limit: 50, offset: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("nba-20250101-LAL-BOS")).toBeInTheDocument();
    });

    // Change competition dropdown from "全部" (value "") to "nba"
    const competitionSelect = screen.getByLabelText("赛事");
    fireEvent.change(competitionSelect, { target: { value: "nba" } });

    await waitFor(() => {
      const lastCall = apiMocks.usePredictionHistory.mock.calls[
        apiMocks.usePredictionHistory.mock.calls.length - 1
      ][0];
      expect(lastCall).toMatchObject({ competition: "nba" });
    });
  });
});
