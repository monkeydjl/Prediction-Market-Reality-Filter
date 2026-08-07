import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({ useTopPicks: vi.fn() }));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useTopPicks: apiMocks.useTopPicks,
}));

import { TopPicksList } from "./TopPicksList";

const REC = {
  match_id: "m1",
  mapped_outcome: "home_win",
  direction: "BUY",
  decision: "act",
  confidence: "high",
  risk_level: "medium",
  edge_pct: 6.4,
  raw_edge_pct: 7.0,
  trust: 0.82,
  liquidity_factor: 0.9,
  stale: false,
  suggested_allocation_pct: 2,
  calibration_status: "ok",
  rationale: "模型显著高于市场",
  engine_name: "poisson_v2",
  competition: "epl",
  prediction_timestamp: null,
  model_prob: 0.61,
  market_prob: 0.55,
  sources_count: 3,
  captured_at: null,
};

describe("TopPicksList", () => {
  beforeEach(() => apiMocks.useTopPicks.mockReset());

  it("requests the top 10 discrepancies", () => {
    apiMocks.useTopPicks.mockReturnValue({ data: undefined, error: undefined, isLoading: true });
    render(<TopPicksList />);
    expect(apiMocks.useTopPicks).toHaveBeenCalledWith({ limit: 10 });
    expect(screen.getByTestId("top-picks-loading")).toBeInTheDocument();
  });

  it("renders picks with a link into the match", () => {
    apiMocks.useTopPicks.mockReturnValue({
      data: { items: [REC], total: 1 },
      error: undefined,
      isLoading: false,
    });
    render(<TopPicksList />);

    expect(screen.getByTestId("top-picks-list")).toHaveTextContent("+6.40pp");
    expect(screen.getByRole("link", { name: /查看比赛 m1/ })).toHaveAttribute(
      "href",
      expect.stringContaining("m1"),
    );
  });

  it("shows the flag hint when the feature is off", () => {
    apiMocks.useTopPicks.mockReturnValue({
      data: undefined,
      error: Object.assign(new Error("503"), { status: 503 }),
      isLoading: false,
    });
    render(<TopPicksList />);
    expect(screen.getByTestId("top-picks-disabled")).toBeInTheDocument();
  });

  it("shows the empty state when no edges are computed", () => {
    apiMocks.useTopPicks.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<TopPicksList />);
    expect(screen.getByTestId("top-picks-empty")).toHaveTextContent("暂无高偏离推荐");
  });
});
