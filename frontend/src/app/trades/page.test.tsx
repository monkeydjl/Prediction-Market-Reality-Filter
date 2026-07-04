import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TradesPage from "./page";

const api = vi.hoisted(() => ({
  tradeStats: vi.fn(),
  openTrades: vi.fn(),
  closedTrades: vi.fn(),
  closeTrade: vi.fn(),
}));

vi.mock("@/components/app-nav", () => ({
  AppNav: () => <nav aria-label="app nav" />,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    eventsApi: api,
  };
});

describe("TradesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.tradeStats.mockResolvedValue({
      total_closed: 0,
      win_rate: null,
      total_pnl_pct: 0,
      avg_pnl_pct: null,
      avg_edge_at_entry: null,
      by_direction: {},
      by_decision: {},
    });
    api.openTrades.mockResolvedValue({
      count: 1,
      trades: [{
        trade_id: "tr-1",
        event_id: "evt-1",
        event_title: "Test event",
        direction: "YES",
        entry_prob: 61,
        market_prob: 52,
        entry_edge: 9,
        entry_time: "2026-07-04T00:00:00Z",
        position_pct: 2,
        confidence: 0.8,
        trust_weight: 0.9,
        decision: "act",
        exit_prob: null,
        exit_market: null,
        exit_time: null,
        exit_reason: null,
        actual_outcome: null,
        pnl_pct: null,
        is_win: null,
        status: "open",
      }],
    });
    api.closedTrades.mockResolvedValue({ count: 0, trades: [] });
    api.closeTrade.mockResolvedValue({ event_id: "evt-1", status: "closed" });
  });

  it("lets an operator manually close an open simulated trade", async () => {
    const user = userEvent.setup();
    render(<TradesPage />);

    await screen.findByText("Test event");
    await user.click(screen.getByRole("button", { name: /手动平仓/ }));

    await waitFor(() => {
      expect(api.closeTrade).toHaveBeenCalledWith("evt-1", {
        exit_prob: 61,
        exit_reason: "manual",
      });
    });
    expect(api.openTrades).toHaveBeenCalledTimes(2);
  });
});
