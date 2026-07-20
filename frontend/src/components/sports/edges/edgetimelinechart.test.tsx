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
  Line: ({ dataKey }: { dataKey: string }) => (
    <div data-testid={`line-${dataKey}`} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

vi.mock("@/lib/env", () => ({
  getApiBase: () => "/api",
}));

vi.mock("swr", () => ({
  default: () => ({
    data: undefined,
    error: undefined,
    isLoading: true,
  }),
}));

const apiMocks = vi.hoisted(() => ({
  useEdgeHistory: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useEdgeHistory: apiMocks.useEdgeHistory,
}));

import { EdgeTimelineChart } from "./edgetimelinechart";

const mockHistory = {
  match_id: "nba-2026-g1",
  series: [
    {
      mapped_outcome: "home_win",
      snapshots: [
        {
          captured_at: "2026-07-16T10:00:00Z",
          model_prob: 0.65,
          market_prob: 0.55,
          raw_edge: 0.10,
          adjusted_edge: 0.072,
          stale: false,
        },
        {
          captured_at: "2026-07-16T11:00:00Z",
          model_prob: 0.66,
          market_prob: 0.56,
          raw_edge: 0.10,
          adjusted_edge: 0.075,
          stale: false,
        },
      ],
    },
  ],
};

describe("EdgeTimelineChart", () => {
  beforeEach(() => {
    apiMocks.useEdgeHistory.mockReset();
  });

  it("显示加载状态", () => {
    apiMocks.useEdgeHistory.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<EdgeTimelineChart matchId="nba-2026-g1" />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("加载后渲染三条线", async () => {
    apiMocks.useEdgeHistory.mockReturnValue({
      data: mockHistory,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeTimelineChart matchId="nba-2026-g1" />);
    await waitFor(() =>
      expect(screen.getByTestId("edge-timeline-chart")).toBeTruthy(),
    );
    expect(screen.getByTestId("series-home_win")).toBeTruthy();
    expect(screen.getByTestId("line-model_prob")).toBeTruthy();
    expect(screen.getByTestId("line-market_prob")).toBeTruthy();
    expect(screen.getByTestId("line-adjusted_edge")).toBeTruthy();
  });

  it("将快照数量传给 LineChart", async () => {
    apiMocks.useEdgeHistory.mockReturnValue({
      data: mockHistory,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeTimelineChart matchId="nba-2026-g1" />);
    await waitFor(() => expect(screen.getByTestId("line-chart")).toBeTruthy());
    expect(screen.getByTestId("line-chart").getAttribute("data-count")).toBe("2");
  });

  it("渲染空状态", async () => {
    apiMocks.useEdgeHistory.mockReturnValue({
      data: { match_id: "m1", series: [] },
      error: undefined,
      isLoading: false,
    });
    render(<EdgeTimelineChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeTruthy());
  });

  it("渲染错误状态", async () => {
    apiMocks.useEdgeHistory.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });
    render(<EdgeTimelineChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeTruthy());
  });
});
