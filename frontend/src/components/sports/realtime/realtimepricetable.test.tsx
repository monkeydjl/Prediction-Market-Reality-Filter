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
    expect(screen.getByText("等待实时数据...")).toBeInTheDocument();
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
});
