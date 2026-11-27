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

vi.mock("@/lib/use-price-stream", () => ({
  usePriceStream: () => ({ isConnected: false }),
}));

vi.mock("@/components/sports/realtime/RealtimePriceIndicator", () => ({
  RealtimePriceIndicator: () => <div data-testid="realtime-indicator" />,
}));

const apiMocks = vi.hoisted(() => ({
  useTraditionalOddsHistory: vi.fn(),
  useMarketSnapshots: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useTraditionalOddsHistory: apiMocks.useTraditionalOddsHistory,
  useMarketSnapshots: apiMocks.useMarketSnapshots,
}));

import { TraditionalOddsChart } from "./TraditionalOddsChart";

describe("TraditionalOddsChart", () => {
  beforeEach(() => {
    apiMocks.useTraditionalOddsHistory.mockReset();
    apiMocks.useMarketSnapshots.mockReset();
  });

  it("shows loading state initially", () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when both sources have no data", async () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: {
        match_id: "m1",
        series: [],
        skipped: true,
        skip_reason: "no_odds",
      },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: { series: [] },
      error: undefined,
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders chart with both traditional and Polymarket data", async () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: {
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
      },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: {
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
      },
      error: undefined,
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.getByTestId("odds-divergence-summary")).toBeTruthy();
    expect(screen.getAllByText("主胜").length).toBeGreaterThan(0);
    expect(screen.getByTestId("series-home_win")).toBeTruthy();
    expect(screen.getByTestId("line-traditional")).toBeTruthy();
    expect(screen.getByTestId("line-polymarket")).toBeTruthy();
  });

  it("renders chart with traditional data only (Polymarket fails gracefully)", async () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: {
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
      },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: undefined,
      error: new Error("503"),
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    // The outcome renders as a localized label ("主胜"), so assert on the
    // stable testid rather than the raw mapped_outcome key.
    expect(screen.getByTestId("series-home_win")).toBeTruthy();
  });

  it("shows error state when both fetches fail", async () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: undefined,
      error: new Error("500"),
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: undefined,
      error: new Error("503"),
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });

  // ── The two zeros of /sport-odds/{id}/history (audit 53.180) ──────────────
  // `no_odds` and `read_failed` both arrive as `series: []`. The chart used to
  // print 「暂无赔率数据」 for both, so a broken odds table looked like a quiet
  // market — the exact shape section 5 exists to forbid.

  it("names a failed odds read instead of calling the market quiet", async () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: { match_id: "m1", series: [], skipped: true, skip_reason: "read_failed" },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: { series: [] },
      error: undefined,
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-read-failed")).toBeTruthy();
    });
    expect(screen.getByTestId("odds-read-failed")).toHaveTextContent(
      "这不是「暂无赔率数据」",
    );
    expect(screen.queryByTestId("empty")).toBeNull();
  });

  it("still says 暂无赔率数据 when the read succeeded and found nothing", async () => {
    // Reverse control for the test above.
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: { match_id: "m1", series: [], skipped: true, skip_reason: "no_odds" },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: { series: [] },
      error: undefined,
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
    expect(screen.queryByTestId("odds-read-failed")).toBeNull();
  });

  it("warns that the comparison is one-sided when only the odds read failed", async () => {
    // Polymarket still has a series, so the chart is not empty: the missing
    // half is the traditional book, and a diverging-reader has to know it.
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: { match_id: "m1", series: [], skipped: true, skip_reason: "read_failed" },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: {
        series: [
          {
            contract_id: "c1",
            outcome_label: "Home Win",
            mapped_outcome: "home_win",
            snapshots: [
              { id: 1, link_id: 7, implied_prob: 0.55, price: 0.55, liquidity: null, volume: null, captured_at: "2026-07-16T10:00:00Z" },
            ],
            untimestamped_count: 0,
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.getByTestId("odds-read-failed-partial")).toHaveTextContent(
      "传统赔率读取失败",
    );
  });

  it("does not warn when both halves are healthy", async () => {
    apiMocks.useTraditionalOddsHistory.mockReturnValue({
      data: { match_id: "m1", series: [], skipped: true, skip_reason: "no_odds" },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: {
        series: [
          {
            contract_id: "c1",
            outcome_label: "Home Win",
            mapped_outcome: "home_win",
            snapshots: [
              { id: 1, link_id: 7, implied_prob: 0.55, price: 0.55, liquidity: null, volume: null, captured_at: "2026-07-16T10:00:00Z" },
            ],
            untimestamped_count: 0,
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.queryByTestId("odds-read-failed-partial")).toBeNull();
  });
});
