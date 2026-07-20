import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

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
  useEdgeLatest: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useEdgeLatest: apiMocks.useEdgeLatest,
}));

import { EdgeDetailPanel } from "./edgedetailpanel";

const mockLatest = {
  match_id: "nba-2026-g1",
  engine_name: "BasketballEngine",
  competition: "nba",
  prediction_timestamp: "2026-07-16T10:00:00Z",
  skipped: false,
  skip_reason: null,
  outcomes: [
    {
      mapped_outcome: "home_win",
      model_prob: 0.65,
      market_prob: 0.55,
      raw_edge: 0.10,
      trust: 0.72,
      liquidity_factor: 1.0,
      adjusted_edge: 0.072,
      spread: 0.01,
      sources_count: 2,
      stale: false,
      captured_at: "2026-07-16T10:00:00Z",
      sources: [
        {
          link_id: 1,
          source: "polymarket",
          contract_id: "c1",
          implied_prob: 0.55,
          liquidity: 5000,
          volume: 1200,
          weight: 0.6,
          link_confidence: 0.95,
        },
        {
          link_id: 2,
          source: "kashi",
          contract_id: "c2",
          implied_prob: 0.56,
          liquidity: null,
          volume: null,
          weight: 0.4,
          link_confidence: 0.80,
        },
      ],
    },
    {
      mapped_outcome: "away_win",
      model_prob: 0.35,
      market_prob: 0.45,
      raw_edge: -0.10,
      trust: 0.60,
      liquidity_factor: 0.9,
      adjusted_edge: -0.081,
      spread: null,
      sources_count: 0,
      stale: true,
      captured_at: "2026-07-16T09:00:00Z",
      sources: [],
    },
  ],
};

describe("EdgeDetailPanel", () => {
  beforeEach(() => {
    apiMocks.useEdgeLatest.mockReset();
  });

  it("显示加载状态", () => {
    apiMocks.useEdgeLatest.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<EdgeDetailPanel matchId="nba-2026-g1" />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("加载后渲染每个 outcome 卡片", async () => {
    apiMocks.useEdgeLatest.mockReturnValue({
      data: mockLatest,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDetailPanel matchId="nba-2026-g1" />);
    await waitFor(() =>
      expect(screen.getByTestId("edge-detail-panel")).toBeTruthy(),
    );
    expect(screen.getByTestId("outcome-home_win")).toBeTruthy();
    expect(screen.getByTestId("outcome-away_win")).toBeTruthy();
  });

  it("展示 model_prob 与 market_prob 对比", async () => {
    apiMocks.useEdgeLatest.mockReturnValue({
      data: mockLatest,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDetailPanel matchId="nba-2026-g1" />);
    await waitFor(() =>
      expect(screen.getByTestId("model-prob-home_win")).toBeTruthy(),
    );
    expect(screen.getByTestId("model-prob-home_win").textContent).toBe("65.0%");
    expect(screen.getByTestId("market-prob-home_win").textContent).toBe("55.0%");
    expect(screen.getByTestId("adjusted-edge-home_win").textContent).toBe("+7.2%");
    expect(screen.getByTestId("trust-home_win").textContent).toBe("72.0%");
    expect(screen.getByTestId("liquidity-factor-home_win").textContent).toBe("1.00");
  });

  it("展示来源详情", async () => {
    apiMocks.useEdgeLatest.mockReturnValue({
      data: mockLatest,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDetailPanel matchId="nba-2026-g1" />);
    await waitFor(() => expect(screen.getByTestId("source-1")).toBeTruthy());
    expect(screen.getByTestId("source-2")).toBeTruthy();
  });

  it("skipped 状态展示 skip_reason", async () => {
    apiMocks.useEdgeLatest.mockReturnValue({
      data: {
        match_id: "m1",
        engine_name: null,
        competition: null,
        prediction_timestamp: null,
        skipped: true,
        skip_reason: "no_market_links",
        outcomes: [],
      },
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDetailPanel matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("skipped")).toBeTruthy());
    expect(screen.getByTestId("skip-reason").textContent).toBe("no_market_links");
  });

  it("渲染错误状态", async () => {
    apiMocks.useEdgeLatest.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });
    render(<EdgeDetailPanel matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeTruthy());
  });
});
