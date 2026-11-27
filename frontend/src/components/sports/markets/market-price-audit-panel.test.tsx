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

  // ── The three zeroes (audit 53.180) ───────────────────────────────────────
  // Before this the panel answered 「无可用价格序列」 for a body whose
  // `available` was false for *two* different reasons: a link with no
  // snapshots, and a snapshot table the backend could not read. The backend
  // now names the second one (`read_failed`); these pin that the panel reads it
  // and that it still reads the first one the old way.

  function mountAudit(entry: Record<string, unknown>) {
    apiMocks.useMatchAudit.mockReturnValue({
      data: {
        match_id: "m1",
        link_count: 1,
        audits: [
          {
            link_id: 7,
            source: "polymarket",
            mapped_outcome: "home_win",
            snapshot_count: 0,
            ...entry,
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<MarketPriceAuditPanel matchId="m1" />);
  }

  it("读失败时说出自己的名字，而不是「无可用价格序列」", () => {
    mountAudit({ available: false, read_failed: true, flags: ["read_failed"] });
    const line = screen.getByTestId("audit-read-failed-7");
    expect(line.textContent).toContain("这不是「尚无快照」");
    expect(screen.queryByText(/无可用价格序列/)).toBeNull();
  });

  it("读失败不再额外渲染一个英文 flag 药丸", () => {
    mountAudit({ available: false, read_failed: true, flags: ["read_failed"] });
    // The pill row would have printed the raw key: this panel does not
    // translate flags, and the state line already carries the meaning.
    expect(screen.queryByText("read_failed")).toBeNull();
  });

  it("读成功但确实没有数据时仍然是「尚无」", () => {
    mountAudit({ available: false, read_failed: false, flags: ["no_snapshots"] });
    expect(screen.getByText(/尚无可用价格序列/)).toBeInTheDocument();
    expect(screen.queryByTestId("audit-read-failed-7")).toBeNull();
  });

  it("老响应没有这个键时报「状态未知」，不当成成功", () => {
    mountAudit({ available: false, flags: [] });
    expect(screen.getByText(/价格序列状态未知/)).toBeInTheDocument();
    expect(screen.queryByTestId("audit-read-failed-7")).toBeNull();
  });
});
