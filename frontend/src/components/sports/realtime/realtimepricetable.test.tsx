import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { UsePriceStreamResult } from "@/lib/use-price-stream";

const apiMocks = vi.hoisted(() => ({
  usePriceStream: vi.fn(),
}));
vi.mock("@/lib/use-price-stream", () => ({
  usePriceStream: apiMocks.usePriceStream,
}));

import { RealtimePriceTable } from "./realtimepricetable";

function mockStream(
  overrides: Partial<UsePriceStreamResult> = {},
): UsePriceStreamResult {
  return {
    updates: [],
    isConnected: false,
    error: null,
    ...overrides,
  };
}

describe("RealtimePriceTable", () => {
  beforeEach(() => {
    apiMocks.usePriceStream.mockReset();
  });

  it("未连接时显示 OFFLINE", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({ isConnected: false, updates: [] }),
    );
    render(<RealtimePriceTable matchId="m1" />);
    expect(screen.getByTestId("realtime-indicator")).toBeDefined();
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
    expect(screen.getByText("未连接到实时数据源")).toBeInTheDocument();
  });

  it("连接但无数据时显示等待实时数据", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({ isConnected: true, updates: [] }),
    );
    render(<RealtimePriceTable matchId="m1" />);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("已连接，等待价格快照（调度器抓取后会推送）...")).toBeInTheDocument();
  });

  it("有数据时渲染价格表格并按倒序显示", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({
        isConnected: true,
        updates: [
          {
            type: "market_snapshot",
            implied_prob: 0.6,
            price: 0.6,
            outcome: "home_win",
            decimal_odds: 1.67,
            bookmaker: "polymarket",
            captured_at: "2026-07-17T10:00:00Z",
          },
          {
            type: "odds_snapshot",
            implied_prob: 0.65,
            price: 0.65,
            outcome: "home_win",
            decimal_odds: 1.54,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:01:00Z",
          },
        ],
      }),
    );
    render(<RealtimePriceTable matchId="m1" />);

    const table = screen.getByTestId("price-table");
    expect(table).toBeInTheDocument();
    // 表头 + 2 行数据
    expect(screen.getAllByRole("row")).toHaveLength(3);

    // 倒序：第一条数据行应是最新的 odds_snapshot
    const rows = screen.getAllByRole("row");
    const firstDataRow = rows[1];
    expect(firstDataRow.textContent).toContain("odds_snapshot");
    expect(firstDataRow.textContent).toContain("kambi");

    // 验证具体内容渲染
    expect(screen.getByText("65.0%")).toBeInTheDocument();
    expect(screen.getByText("60.0%")).toBeInTheDocument();
    // 两行 outcome 都是 home_win，应渲染两次
    expect(screen.getAllByText("home_win")).toHaveLength(2);
  });

  it("不同序列之间不比较（type/bookmaker 隔离）", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({
        isConnected: true,
        updates: [
          {
            type: "market_snapshot",
            implied_prob: 0.6,
            price: 0.6,
            outcome: "home_win",
            decimal_odds: 1.67,
            bookmaker: "polymarket",
            captured_at: "2026-07-17T10:00:00Z",
          },
          {
            type: "odds_snapshot",
            implied_prob: 0.65,
            price: 0.65,
            outcome: "home_win",
            decimal_odds: 1.54,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:01:00Z",
          },
        ],
      }),
    );
    render(<RealtimePriceTable matchId="m1" />);
    // 两个序列各是首样本 ⇒ 无任何 delta chip
    expect(screen.queryByText("▲+5.0pt")).not.toBeInTheDocument();
    expect(screen.queryByText("▼-5.0pt")).not.toBeInTheDocument();
    expect(screen.queryByTestId("prob-delta-0")).not.toBeInTheDocument();
  });

  // Locks the direction-color convention 涨红跌绿 (CN market convention):
  // probability rising vs the same-series previous snapshot = red (--up),
  // falling = green (--down). Valence --pos/--neg must not appear. See
  // globals.css for the token split.
  it("同序列概率上行显示红色 delta（up 令牌，非 pos）", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({
        isConnected: true,
        updates: [
          {
            type: "odds_snapshot",
            implied_prob: 0.6,
            price: 0.6,
            outcome: "home_win",
            decimal_odds: 1.67,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:00:00Z",
          },
          {
            type: "odds_snapshot",
            implied_prob: 0.65,
            price: 0.65,
            outcome: "home_win",
            decimal_odds: 1.54,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:01:00Z",
          },
        ],
      }),
    );
    render(<RealtimePriceTable matchId="m1" />);

    // 倒序：第一数据行是最新快照，带 delta；旧行（首样本）无
    const newerDelta = screen.getByTestId("prob-delta-0");
    expect(newerDelta).toHaveTextContent("▲+5.0pt");
    expect(newerDelta).toHaveClass("text-up");
    expect(newerDelta).not.toHaveClass("text-pos");
    expect(newerDelta).not.toHaveClass("text-neg");
    expect(screen.queryByTestId("prob-delta-1")).not.toBeInTheDocument();
  });

  it("同序列概率下行显示绿色 delta（down 令牌，非 neg）", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({
        isConnected: true,
        updates: [
          {
            type: "odds_snapshot",
            implied_prob: 0.65,
            price: 0.65,
            outcome: "home_win",
            decimal_odds: 1.54,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:00:00Z",
          },
          {
            type: "odds_snapshot",
            implied_prob: 0.6,
            price: 0.6,
            outcome: "home_win",
            decimal_odds: 1.67,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:01:00Z",
          },
        ],
      }),
    );
    render(<RealtimePriceTable matchId="m1" />);

    const newerDelta = screen.getByTestId("prob-delta-0");
    expect(newerDelta).toHaveTextContent("▼-5.0pt");
    expect(newerDelta).toHaveClass("text-down");
    expect(newerDelta).not.toHaveClass("text-neg");
    expect(newerDelta).not.toHaveClass("text-pos");
  });

  it("死区（<0.5pt）不显示 delta", () => {
    apiMocks.usePriceStream.mockReturnValue(
      mockStream({
        isConnected: true,
        updates: [
          {
            type: "odds_snapshot",
            implied_prob: 0.6,
            price: 0.6,
            outcome: "home_win",
            decimal_odds: 1.67,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:00:00Z",
          },
          {
            type: "odds_snapshot",
            implied_prob: 0.603,
            price: 0.603,
            outcome: "home_win",
            decimal_odds: 1.66,
            bookmaker: "kambi",
            captured_at: "2026-07-17T10:01:00Z",
          },
        ],
      }),
    );
    render(<RealtimePriceTable matchId="m1" />);
    expect(screen.queryByTestId("prob-delta-0")).not.toBeInTheDocument();
  });
});
