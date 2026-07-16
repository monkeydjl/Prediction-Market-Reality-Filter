import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useOpenDecisions: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useOpenDecisions: apiMocks.useOpenDecisions,
}));

import { OpenDecisionsList } from "./OpenDecisionsList";

const mockData = {
  items: [
    {
      match_id: "m1",
      mapped_outcome: "home_win",
      direction: "YES",
      decision: "act",
      confidence: "high",
      risk_level: "low",
      edge_pct: 7.2,
      raw_edge_pct: 10.0,
      trust: 0.72,
      liquidity_factor: 1.0,
      stale: false,
      suggested_allocation_pct: 2.0,
      calibration_status: "calibrated",
      rationale: "模型看好主胜",
      engine_name: "BasketballEngine",
      competition: "nba",
      prediction_timestamp: "2026-07-16T10:00:00Z",
      model_prob: 0.65,
      market_prob: 0.55,
      sources_count: 1,
      captured_at: "2026-07-16T10:00:00Z",
    },
  ],
  total: 1,
};

describe("OpenDecisionsList", () => {
  beforeEach(() => {
    apiMocks.useOpenDecisions.mockReset();
  });

  it("renders list after load", async () => {
    apiMocks.useOpenDecisions.mockReturnValue({
      data: mockData,
      error: undefined,
      isLoading: false,
    });
    render(<OpenDecisionsList />);
    await waitFor(() =>
      expect(screen.getByTestId("open-decisions-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("rec-card-m1")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.useOpenDecisions.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<OpenDecisionsList />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.useOpenDecisions.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });
    render(<OpenDecisionsList />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("renders filter buttons", async () => {
    apiMocks.useOpenDecisions.mockReturnValue({
      data: mockData,
      error: undefined,
      isLoading: false,
    });
    render(<OpenDecisionsList />);
    await waitFor(() =>
      expect(screen.getByTestId("open-decisions-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("filter-all")).toBeInTheDocument();
    expect(screen.getByTestId("filter-act")).toBeInTheDocument();
  });
});
