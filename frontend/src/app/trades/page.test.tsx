import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TradesPage from "./page";
import type { SimTrade, TradeStats } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
  tradeStats: vi.fn(),
  openTrades: vi.fn(),
  closedTrades: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  eventsApi: apiMocks,
}));

vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

const stats: TradeStats = {
  total_closed: 1,
  win_rate: 1,
  total_pnl_pct: 9.14,
  avg_pnl_pct: 9.14,
  avg_edge_at_entry: 25.18,
  by_direction: {},
  by_decision: {},
};

const closedTrade: SimTrade = {
  trade_id: "sim-1",
  event_id: "evt-1",
  event_title: "Closed trade event",
  direction: "NO",
  entry_prob: 57.14,
  market_prob: 82.32,
  entry_edge: -25.18,
  entry_time: "2026-07-05T02:41:27.000Z",
  position_pct: 5,
  confidence: null,
  trust_weight: null,
  decision: "watch",
  exit_prob: null,
  exit_market: null,
  exit_time: "2026-07-05T03:00:00.000Z",
  exit_reason: "resolved_partial",
  actual_outcome: 50,
  pnl_pct: 9.14,
  is_win: 1,
  status: "closed",
};

const openTrade: SimTrade = {
  ...closedTrade,
  trade_id: "sim-open",
  event_id: "evt-open",
  event_title: "Open trade event",
  direction: "NO",
  entry_prob: 28.5,
  market_prob: 37.6,
  entry_edge: -9.1,
  entry_time: "2026-07-05T02:25:00.000Z",
  exit_time: null,
  exit_reason: null,
  actual_outcome: null,
  pnl_pct: null,
  is_win: null,
  status: "open",
};

function formattedEntryTime() {
  const d = new Date(closedTrade.entry_time);
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" }) + " " +
    d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

describe("TradesPage", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
  });

  it("shows entry time for closed trades", async () => {
    apiMocks.tradeStats.mockResolvedValue(stats);
    apiMocks.openTrades.mockResolvedValue({ count: 0, trades: [] });
    apiMocks.closedTrades.mockResolvedValue({ count: 1, trades: [closedTrade] });

    render(<TradesPage />);

    const closedSection = await screen.findByRole("heading", { name: "已平仓 (1)" });
    const section = closedSection.closest("section");
    expect(section).not.toBeNull();

    expect(within(section as HTMLElement).getByRole("columnheader", { name: "入场时间" }))
      .toBeInTheDocument();
    expect(within(section as HTMLElement).getByText(formattedEntryTime()))
      .toBeInTheDocument();
  });

  it("shows market probability as the entry probability for open trades", async () => {
    apiMocks.tradeStats.mockResolvedValue(stats);
    apiMocks.openTrades.mockResolvedValue({ count: 1, trades: [openTrade] });
    apiMocks.closedTrades.mockResolvedValue({ count: 0, trades: [] });

    render(<TradesPage />);

    const eventCell = await screen.findByText("Open trade event");
    const row = eventCell.closest("tr");
    expect(row).not.toBeNull();

    expect(row).toHaveTextContent("37.6%");
    expect(row).not.toHaveTextContent("28.5%");
  });

  it("shows market probability instead of system probability for closed entry-to-settlement", async () => {
    apiMocks.tradeStats.mockResolvedValue(stats);
    apiMocks.openTrades.mockResolvedValue({ count: 0, trades: [] });
    apiMocks.closedTrades.mockResolvedValue({ count: 1, trades: [closedTrade] });

    render(<TradesPage />);

    const eventCell = await screen.findByText("Closed trade event");
    const row = eventCell.closest("tr");
    expect(row).not.toBeNull();

    expect(row).toHaveTextContent("82% → 50%");
    expect(row).not.toHaveTextContent("57% → 50%");
  });

  it("shows closed trade performance split by decision", async () => {
    apiMocks.tradeStats.mockResolvedValue({
      ...stats,
      total_closed: 5,
      by_decision: {
        act: { total: 2, wins: 2, win_rate: 1, avg_pnl: 12.5 },
        watch: { total: 3, wins: 1, win_rate: 0.333, avg_pnl: -4.25 },
      },
    });
    apiMocks.openTrades.mockResolvedValue({ count: 0, trades: [] });
    apiMocks.closedTrades.mockResolvedValue({ count: 0, trades: [] });

    render(<TradesPage />);

    const breakdown = await screen.findByTestId("decision-performance-breakdown");
    expect(within(breakdown).getByText(new RegExp("watch \u662f\u63a2\u7d22\u89c2\u5bdf\u6837\u672c"))).toBeInTheDocument();

    const actRow = within(breakdown).getByTestId("decision-performance-act");
    expect(actRow).toHaveTextContent("act");
    expect(actRow).toHaveTextContent("\u6b63\u5f0f\u884c\u52a8");
    expect(actRow).toHaveTextContent("2");
    expect(actRow).toHaveTextContent("100.0%");
    expect(actRow).toHaveTextContent("+12.50pt%");

    const watchRow = within(breakdown).getByTestId("decision-performance-watch");
    expect(watchRow).toHaveTextContent("watch");
    expect(watchRow).toHaveTextContent("\u63a2\u7d22\u89c2\u5bdf");
    expect(watchRow).toHaveTextContent("3");
    expect(watchRow).toHaveTextContent("33.3%");
    expect(watchRow).toHaveTextContent("-4.25pt%");
  });

  it("paginates open and closed trade events with ten rows per page", async () => {
    const user = userEvent.setup();
    apiMocks.tradeStats.mockResolvedValue({ ...stats, total_closed: 12 });
    apiMocks.openTrades.mockImplementation(async (limit = 10, offset = 0) => ({
      count: offset === 0 ? 10 : 1,
      total: 11,
      limit,
      offset,
      trades: offset === 0
        ? [{ ...openTrade, trade_id: "open-page-1", event_title: "Open trade page 1" }]
        : [{ ...openTrade, trade_id: "open-page-2", event_title: "Open trade page 2" }],
    }));
    apiMocks.closedTrades.mockImplementation(async (limit = 10, offset = 0) => ({
      count: offset === 0 ? 10 : 2,
      total: 12,
      limit,
      offset,
      trades: offset === 0
        ? [{ ...closedTrade, trade_id: "closed-page-1", event_title: "Closed trade page 1" }]
        : [{ ...closedTrade, trade_id: "closed-page-2", event_title: "Closed trade page 2" }],
    }));

    render(<TradesPage />);

    const openHeading = await screen.findByRole("heading", { name: "当前持仓 (11)" });
    const closedHeading = await screen.findByRole("heading", { name: "已平仓 (12)" });
    expect(apiMocks.openTrades).toHaveBeenCalledWith(10, 0);
    expect(apiMocks.closedTrades).toHaveBeenCalledWith(10, 0);

    const closedSection = closedHeading.closest("section");
    expect(closedSection).not.toBeNull();
    expect(within(closedSection as HTMLElement).getByText("第 1 / 2 页 · 共 12 条")).toBeInTheDocument();

    await user.click(within(closedSection as HTMLElement).getByRole("button", { name: /\u4e0b\u4e00\u9875/ }));

    await waitFor(() => expect(apiMocks.closedTrades).toHaveBeenLastCalledWith(10, 10));
    expect(await within(closedSection as HTMLElement).findByText("Closed trade page 2")).toBeInTheDocument();
    expect(within(closedSection as HTMLElement).getByText("第 2 / 2 页 · 共 12 条")).toBeInTheDocument();

    const openSection = openHeading.closest("section");
    expect(openSection).not.toBeNull();
    await user.click(within(openSection as HTMLElement).getByRole("button", { name: /\u4e0b\u4e00\u9875/ }));

    await waitFor(() => expect(apiMocks.openTrades).toHaveBeenLastCalledWith(10, 10));
    expect(await within(openSection as HTMLElement).findByText("Open trade page 2")).toBeInTheDocument();
  });

});
