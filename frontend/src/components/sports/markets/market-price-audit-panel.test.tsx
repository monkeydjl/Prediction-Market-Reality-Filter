import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({ useMatchAudit: vi.fn() }));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useMatchAudit: apiMocks.useMatchAudit,
}));

import { MarketPriceAuditPanel } from "./market-price-audit-panel";

describe("MarketPriceAuditPanel", () => {
  beforeEach(() => apiMocks.useMatchAudit.mockReset());

  it("经 useMatchAudit 取数并渲染 Δ 与最大回撤", () => {
    apiMocks.useMatchAudit.mockReturnValue({
      data: {
        match_id: "m1",
        link_count: 1,
        audits: [
          {
            link_id: 7,
            source: "polymarket",
            mapped_outcome: "home_win",
            market_id: "0xabc",
            available: true,
            snapshot_count: 12,
            first_price: 0.4,
            last_price: 0.52,
            delta_pp: 12,
            max_drawdown_pp: 3.5,
            flags: ["thin_liquidity"],
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<MarketPriceAuditPanel matchId="m1" />);
    // 关键：key 由 hook 拥有，面板不再自己拼 URL
    expect(apiMocks.useMatchAudit).toHaveBeenCalledWith("m1");
    expect(screen.getByTestId("audit-link-7")).toBeInTheDocument();
    expect(screen.getByText("+12.0pp")).toBeInTheDocument();
    expect(screen.getByText("3.5pp")).toBeInTheDocument();
    expect(screen.getByText("thin_liquidity")).toBeInTheDocument();
  });

  it("无关联快照时渲染空态", () => {
    apiMocks.useMatchAudit.mockReturnValue({
      data: { match_id: "m1", link_count: 0, audits: [] },
      error: undefined,
      isLoading: false,
    });
    render(<MarketPriceAuditPanel matchId="m1" />);
    expect(screen.getByTestId("market-price-audit-empty")).toBeInTheDocument();
  });

  it("matchId 为空时传 null，不发起请求", () => {
    apiMocks.useMatchAudit.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
    });
    const { container } = render(<MarketPriceAuditPanel matchId="" />);
    expect(apiMocks.useMatchAudit).toHaveBeenCalledWith(null);
    expect(container.textContent).toBe("");
  });
});
