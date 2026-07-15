import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PredictionTrajectory } from "./prediction-trajectory";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

// Mock recharts
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  Line: ({ dataKey }: { dataKey: string }) => (
    <div data-testid="line" data-key={dataKey} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

// Mock chart-lite
vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

// Mock learning-api
vi.mock("@/lib/learning-api", () => ({
  fetchPredictionTrajectory: vi.fn(),
}));

import { fetchPredictionTrajectory } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchPredictionTrajectory).mockReset();
});

const mockTrajectory = {
  match_id: "nba-1",
  sport: "basketball",
  competition: "nba",
  items: [
    {
      id: 1, match_id: "nba-1", sport: "basketball", competition: "nba",
      engine: "basketball",
      predicted_scores: { home: 112, away: 108 },
      outcome_probabilities: { home_win: 0.62, away_win: 0.38 },
      confidence: 0.59, feature_version: "nba-1.0", trigger: "initial",
      created_at: "2026-07-14T18:30:00Z", outcome: null,
    },
    {
      id: 2, match_id: "nba-1", sport: "basketball", competition: "nba",
      engine: "basketball",
      predicted_scores: { home: 114, away: 106 },
      outcome_probabilities: { home_win: 0.68, away_win: 0.32 },
      confidence: 0.64, feature_version: "nba-1.0", trigger: "weight_update",
      created_at: "2026-07-14T20:00:00Z", outcome: null,
    },
  ],
  count: 2,
};

describe("PredictionTrajectory", () => {
  it("renders match_id in header", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      expect(screen.getByText("nba-1")).toBeInTheDocument();
    });
  });

  it("renders trajectory chart with dynamic lines per outcome", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      const lines = screen.getAllByTestId("line");
      // 2 outcomes (home_win, away_win) → 2 lines in trajectory chart
      // Plus 1 line in confidence chart = 3 total
      expect(lines.length).toBe(3);
    });
  });

  it("renders back link", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      const link = screen.getByRole("link");
      expect(link.getAttribute("href")).toBe("/sports/learning?tab=history");
    });
  });

  it("renders empty state when no history", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce({
      match_id: "empty-1", sport: null, competition: null, items: [], count: 0,
    });
    render(<PredictionTrajectory matchId="empty-1" />);
    await waitFor(() => {
      expect(screen.getByText("该比赛暂无历史预测记录")).toBeInTheDocument();
    });
  });

  it("renders detail table with trigger info", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      expect(screen.getByText("initial")).toBeInTheDocument();
      expect(screen.getByText("weight_update")).toBeInTheDocument();
    });
  });
});
