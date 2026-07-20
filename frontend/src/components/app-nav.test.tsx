import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import { AppNav } from "./app-nav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));

vi.mock("@/components/operator-key-control", () => ({
  OperatorKeyControl: () => <button type="button">Operator</button>,
}));

vi.mock("@/components/theme-control", () => ({
  ThemeControl: () => <button type="button">Theme</button>,
}));

vi.mock("@/components/live-status-indicator", () => ({
  LiveStatusIndicator: () => <span>LiveStatus</span>,
}));

const moversMock = vi.fn();

vi.mock("@/lib/api", () => ({
  eventsApi: {
    movers: (...args: unknown[]) => moversMock(...args),
  },
}));

describe("AppNav", () => {
  beforeEach(() => {
    moversMock.mockReset();
    moversMock.mockResolvedValue({ movers: [], count: 0 });
  });

  it("renders brand and ticker above main navigation (fallback when no movers)", async () => {
    render(<AppNav />);

    const ticker = await screen.findByTestId("hot-news-ticker");
    const nav = screen.getByRole("navigation", { name: "主导航" });

    expect(within(ticker).getByRole("link", { name: /PROBABILITY/ })).toHaveAttribute(
      "href",
      "/",
    );
    await waitFor(() => {
      expect(ticker).toHaveAttribute("data-live", "false");
    });
    expect(ticker).toHaveTextContent("示例新闻");
    expect(ticker).toHaveTextContent("暂无概率异动");
    expect(within(nav).queryByText(/PROBABILITY/)).not.toBeInTheDocument();
    expect(
      ticker.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows live mover titles when API returns data", async () => {
    moversMock.mockResolvedValue({
      count: 2,
      movers: [
        {
          event_id: "evt-fed",
          event_title: "Fed rate cut September",
          event_title_zh: "美联储 9 月降息概率上修",
          trend: {
            net_change: 8.5,
            latest_probability: 62,
            direction: "up",
          },
        },
        {
          event_id: "evt-btc",
          event_title: "BTC above 100k",
          trend: {
            net_change: -3.2,
            latest_probability: 40,
            direction: "down",
          },
        },
      ],
    });

    render(<AppNav />);

    const ticker = await screen.findByTestId("hot-news-ticker");
    await waitFor(() => {
      expect(ticker).toHaveAttribute("data-live", "true");
    });
    expect(ticker).toHaveTextContent("异动快讯");
    expect(ticker).toHaveTextContent("美联储 9 月降息概率上修");
    expect(ticker).toHaveTextContent("BTC above 100k");
    expect(ticker).toHaveTextContent("+8.5pp");

    const link = screen.getByTestId("ticker-item-evt-fed");
    expect(link).toHaveAttribute(
      "href",
      `/events?id=${encodeURIComponent("evt-fed")}`,
    );
  });

  it("renders two group labels", () => {
    render(<AppNav />);

    expect(screen.getByText("事件情报平台")).toBeInTheDocument();
    expect(screen.getByText("Sports Prediction OS")).toBeInTheDocument();
  });

  it("renders all Event Intelligence entries", () => {
    render(<AppNav />);

    for (const label of [
      "监控面板", "决策机会", "事件 Edge", "人工分析",
      "历史复盘", "质量运营", "质量切片", "模拟交易",
    ]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("renders all Sports Prediction OS entries", () => {
    render(<AppNav />);

    for (const label of [
      "体育预测", "体育 Edge", "期货市场", "学习仪表盘", "体育市场",
      "参数优化", "体育推荐", "体育结算", "竞猜中心",
    ]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("links /quality entry", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /质量运营/ });
    expect(link).toHaveAttribute("href", "/quality");
  });

  it("links /sports/futures entry", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /期货市场/ });
    expect(link).toHaveAttribute("href", "/sports/futures");
  });

  it("links /sports/optimization entry", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /参数优化/ });
    expect(link).toHaveAttribute("href", "/sports/optimization");
  });

  it("links /sports/betting entry (hub page for betting categories)", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /竞猜中心/ });
    expect(link).toHaveAttribute("href", "/sports/betting");
  });

  it("does not link the old /world-cup route", () => {
    render(<AppNav />);
    expect(screen.queryByRole("link", { name: /^世界杯$/ })).not.toBeInTheDocument();
  });

  it("keeps navigation labels on a single line", () => {
    render(<AppNav />);
    const labels = [
      "监控面板", "决策机会", "事件 Edge", "人工分析",
      "历史复盘", "质量运营", "质量切片", "模拟交易",
      "体育预测", "世界杯", "体育 Edge", "期货市场", "学习仪表盘", "体育市场",
      "参数优化", "体育推荐", "体育结算", "竞猜中心",
    ];
    for (const label of labels) {
      const link = screen.getByRole("link", { name: new RegExp(label) });
      expect(link.className).toMatch(/whitespace-nowrap/);
    }
  });
});
