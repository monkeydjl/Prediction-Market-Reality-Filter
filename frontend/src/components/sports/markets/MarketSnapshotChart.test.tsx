import { describe, expect, it, vi, beforeEach } from "vitest";
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
  Line: () => <div data-testid="line" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

const apiMocks = vi.hoisted(() => ({
  useMarketSnapshots: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useMarketSnapshots: apiMocks.useMarketSnapshots,
}));

import { MarketSnapshotChart } from "./MarketSnapshotChart";

describe("MarketSnapshotChart", () => {
  beforeEach(() => apiMocks.useMarketSnapshots.mockReset());

  it("renders chart with snapshot data", async () => {
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: {
        series: [
          {
            contract_id: "c1", outcome_label: "YES", mapped_outcome: "home_win",
            snapshots: [{ id: 1, implied_prob: 0.6, price: 0.6, captured_at: "t1" }],
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() =>
      expect(screen.getByTestId("series-c1")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("line-chart")).toBeInTheDocument();
  });

  it("passes snapshot count to chart", async () => {
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: {
        series: [
          {
            contract_id: "c1", outcome_label: "YES", mapped_outcome: "home_win",
            snapshots: [
              { id: 1, implied_prob: 0.6, price: 0.6, captured_at: "t1" },
              { id: 2, implied_prob: 0.65, price: 0.65, captured_at: "t2" },
            ],
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("line-chart")).toBeInTheDocument());
    expect(screen.getByTestId("line-chart").getAttribute("data-count")).toBe("2");
  });

  it("renders empty state", async () => {
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: { series: [] },
      error: undefined,
      isLoading: false,
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders multiple series", async () => {
    apiMocks.useMarketSnapshots.mockReturnValue({
      data: {
        series: [
          {
            contract_id: "c1", outcome_label: "YES", mapped_outcome: "home_win",
            snapshots: [{ id: 1, implied_prob: 0.6, price: 0.6, captured_at: "t1" }],
          },
          {
            contract_id: "c2", outcome_label: "NO", mapped_outcome: "away_win",
            snapshots: [{ id: 2, implied_prob: 0.4, price: 0.4, captured_at: "t1" }],
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("series-c1")).toBeInTheDocument());
    expect(screen.getByTestId("series-c2")).toBeInTheDocument();
  });
});
