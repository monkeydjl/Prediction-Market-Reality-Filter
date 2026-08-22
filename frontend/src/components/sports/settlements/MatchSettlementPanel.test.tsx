import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({ useSettlement: vi.fn() }));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useSettlement: apiMocks.useSettlement,
}));

import { MatchSettlementPanel } from "./MatchSettlementPanel";

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    match_id: "m1",
    mapped_outcome: "home_win",
    engine: "BasketballEngine",
    competition: "nba",
    settlement_implied_prob: 0.9,
    settlement_captured_at: "2026-01-01T00:00:00Z",
    link_id: 1,
    model_prob: 0.65,
    market_prob_at_detection: 0.6,
    raw_edge: 0.05,
    adjusted_edge: 0.04,
    brier_score: 0.0625,
    signed_error: -0.25,
    direction_correct: 1,
    status: "processed",
    skip_reason: null,
    match_finished_at: "2026-01-01T00:00:00Z",
    processed_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function mockData(items: ReturnType<typeof row>[]) {
  apiMocks.useSettlement.mockReturnValue({
    data: { items, total: items.length },
    error: undefined,
    isLoading: false,
  });
}

describe("MatchSettlementPanel", () => {
  beforeEach(() => apiMocks.useSettlement.mockReset());

  it("渲染本场结算行的模型概率、结算概率与 Brier", () => {
    mockData([row()]);
    render(<MatchSettlementPanel matchId="m1" />);
    expect(apiMocks.useSettlement).toHaveBeenCalledWith("m1");
    expect(screen.getByTestId("match-settlement-panel")).toBeInTheDocument();
    expect(screen.getByText("home_win")).toBeInTheDocument();
    expect(screen.getByText("0.650")).toBeInTheDocument();
    expect(screen.getByText("0.900")).toBeInTheDocument();
    expect(screen.getByText("0.0625")).toBeInTheDocument();
  });

  it("方向三态：1 为 ✓，0 为 ✗，null 为 —（无方向判断，不是判错）", () => {
    mockData([
      row({ id: 1, direction_correct: 1 }),
      row({ id: 2, direction_correct: 0 }),
      row({ id: 3, direction_correct: null, raw_edge: 0 }),
    ]);
    render(<MatchSettlementPanel matchId="m1" />);
    expect(screen.getByTestId("match-dir-1").textContent).toBe("✓");
    expect(screen.getByTestId("match-dir-2").textContent).toBe("✗");
    // 这一行是关键：模型与市场同价时不得渲染为 ✗，否则与
    // direction_accuracy 排除该行的口径自相矛盾。
    expect(screen.getByTestId("match-dir-3").textContent).toBe("—");
    expect(screen.getByTestId("match-dir-3").getAttribute("title")).toContain(
      "未形成方向判断",
    );
  });

  it("skipped 行显示 skip_reason 且概率列降级为 —", () => {
    mockData([
      row({
        status: "skipped_no_links",
        skip_reason: "No verified link for outcome home_win.",
        settlement_implied_prob: null,
        brier_score: null,
        signed_error: null,
        direction_correct: null,
      }),
    ]);
    render(<MatchSettlementPanel matchId="m1" />);
    expect(screen.getByText("skipped_no_links")).toBeInTheDocument();
    expect(
      screen.getByText(/No verified link for outcome home_win\./),
    ).toBeInTheDocument();
  });

  it("404 视为尚无结算记录，不报错", () => {
    apiMocks.useSettlement.mockReturnValue({
      data: undefined,
      error: new ApiError(404, "未找到"),
      isLoading: false,
    });
    render(<MatchSettlementPanel matchId="m1" />);
    expect(screen.getByTestId("match-settlement-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("match-settlement-error")).toBeNull();
  });

  it("503 提示 phase 开关未启用", () => {
    apiMocks.useSettlement.mockReturnValue({
      data: undefined,
      error: new ApiError(503, "disabled"),
      isLoading: false,
    });
    render(<MatchSettlementPanel matchId="m1" />);
    expect(screen.getByTestId("match-settlement-disabled").textContent).toContain(
      "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED",
    );
  });

  it("其他错误仍然报错，不被 404/503 分支吞掉", () => {
    apiMocks.useSettlement.mockReturnValue({
      data: undefined,
      error: new ApiError(500, "boom"),
      isLoading: false,
    });
    render(<MatchSettlementPanel matchId="m1" />);
    expect(screen.getByTestId("match-settlement-error").textContent).toContain("boom");
  });

  it("matchId 为空时不发起请求也不渲染", () => {
    apiMocks.useSettlement.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
    });
    const { container } = render(<MatchSettlementPanel matchId="" />);
    expect(apiMocks.useSettlement).toHaveBeenCalledWith(null);
    expect(container.textContent).toBe("");
  });
});
