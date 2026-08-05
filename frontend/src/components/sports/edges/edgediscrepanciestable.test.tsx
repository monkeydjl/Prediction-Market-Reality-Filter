import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

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
  useEdgeDiscrepancies: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useEdgeDiscrepancies: apiMocks.useEdgeDiscrepancies,
}));

import { EdgeDiscrepanciesTable } from "./edgediscrepanciestable";

const mockItems = {
  items: [
    {
      match_id: "nba-2026-g1",
      mapped_outcome: "home_win",
      model_prob: 0.65,
      market_prob: 0.55,
      raw_edge: 0.10,
      adjusted_edge: 0.072,
      stale: false,
      captured_at: "2026-07-16T10:00:00Z",
    },
    {
      match_id: "nba-2026-g2",
      mapped_outcome: "away_win",
      model_prob: 0.40,
      market_prob: 0.50,
      raw_edge: -0.10,
      adjusted_edge: -0.08,
      stale: true,
      captured_at: "2026-07-16T09:00:00Z",
    },
  ],
  total: 2,
};

describe("EdgeDiscrepanciesTable", () => {
  beforeEach(() => {
    apiMocks.useEdgeDiscrepancies.mockReset();
  });

  it("显示加载状态", () => {
    apiMocks.useEdgeDiscrepancies.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<EdgeDiscrepanciesTable />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("加载后渲染表格行", async () => {
    apiMocks.useEdgeDiscrepancies.mockReturnValue({
      data: mockItems,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDiscrepanciesTable />);
    await waitFor(() =>
      expect(screen.getByTestId("edge-discrepancies-table")).toBeTruthy(),
    );
    expect(screen.getByTestId("row-nba-2026-g1-home_win")).toBeTruthy();
    expect(screen.getByTestId("row-nba-2026-g2-away_win")).toBeTruthy();
  });

  it("渲染比赛链接指向详情页", async () => {
    apiMocks.useEdgeDiscrepancies.mockReturnValue({
      data: mockItems,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDiscrepanciesTable />);
    await waitFor(() =>
      expect(screen.getByTestId("link-nba-2026-g1")).toBeTruthy(),
    );
    expect(screen.getByTestId("link-nba-2026-g1").getAttribute("href")).toBe(
      "/sports/match/?id=nba-2026-g1&tab=edge",
    );
  });

  it("渲染活跃与过期状态徽标", async () => {
    apiMocks.useEdgeDiscrepancies.mockReturnValue({
      data: mockItems,
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDiscrepanciesTable />);
    await waitFor(() =>
      expect(screen.getByTestId("status-nba-2026-g1-home_win")).toBeTruthy(),
    );
    expect(screen.getByTestId("status-nba-2026-g1-home_win").textContent).toBe("活跃");
    expect(screen.getByTestId("status-nba-2026-g2-away_win").textContent).toBe("过期");
  });

  it("渲染空状态", async () => {
    apiMocks.useEdgeDiscrepancies.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<EdgeDiscrepanciesTable />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeTruthy());
  });

  it("渲染错误状态", async () => {
    apiMocks.useEdgeDiscrepancies.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });
    render(<EdgeDiscrepanciesTable />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeTruthy());
  });
});
