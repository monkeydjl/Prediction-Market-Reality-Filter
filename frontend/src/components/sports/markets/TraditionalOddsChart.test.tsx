import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children, data }: { children: React.ReactNode; data: unknown[] }) => (
    <div data-testid="line-chart" data-count={data.length}>
      {children}
    </div>
  ),
  Line: ({ dataKey }: { dataKey: string }) => (
    <div data-testid={`line-${dataKey}`} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
  Legend: () => <div data-testid="legend" />,
}));

vi.mock("@/lib/sport-odds-api", () => ({
  fetchTraditionalOddsHistory: vi.fn(),
}));

vi.mock("@/lib/sport-markets-api", () => ({
  fetchMarketSnapshots: vi.fn(),
}));

import { TraditionalOddsChart } from "./TraditionalOddsChart";
import { fetchTraditionalOddsHistory } from "@/lib/sport-odds-api";
import { fetchMarketSnapshots } from "@/lib/sport-markets-api";

describe("TraditionalOddsChart", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(fetchTraditionalOddsHistory).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchMarketSnapshots).mockReturnValue(new Promise(() => {}));
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when both sources have no data", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "m1",
      series: [],
      skipped: true,
      skip_reason: "no_odds",
    });
    vi.mocked(fetchMarketSnapshots).mockResolvedValue({ series: [] });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders chart with both traditional and Polymarket data", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "nba-2026-g1",
      series: [
        {
          mapped_outcome: "home_win",
          snapshots: [
            { implied_prob: 0.60, decimal_odds: 1.667, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T10:00:00Z" },
            { implied_prob: 0.65, decimal_odds: 1.538, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T11:00:00Z" },
          ],
        },
      ],
      skipped: false,
      skip_reason: null,
    });
    vi.mocked(fetchMarketSnapshots).mockResolvedValue({
      series: [
        {
          contract_id: "c1",
          outcome_label: "Home Win",
          mapped_outcome: "home_win",
          snapshots: [
            { id: 1, implied_prob: 0.58, price: 0.58, captured_at: "2026-07-16T10:00:00Z" },
            { id: 2, implied_prob: 0.62, price: 0.62, captured_at: "2026-07-16T11:00:00Z" },
          ],
        },
      ],
    });
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.getByText("home_win")).toBeTruthy();
    expect(screen.getByTestId("series-home_win")).toBeTruthy();
    expect(screen.getByTestId("line-traditional")).toBeTruthy();
    expect(screen.getByTestId("line-polymarket")).toBeTruthy();
  });

  it("renders chart with traditional data only (Polymarket fails gracefully)", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "nba-2026-g1",
      series: [
        {
          mapped_outcome: "home_win",
          snapshots: [
            { implied_prob: 0.60, decimal_odds: 1.667, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T10:00:00Z" },
          ],
        },
      ],
      skipped: false,
      skip_reason: null,
    });
    vi.mocked(fetchMarketSnapshots).mockRejectedValue(new Error("503"));
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.getByText("home_win")).toBeTruthy();
  });

  it("shows error state when both fetches fail", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockRejectedValue(new Error("500"));
    vi.mocked(fetchMarketSnapshots).mockRejectedValue(new Error("503"));
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });
});
