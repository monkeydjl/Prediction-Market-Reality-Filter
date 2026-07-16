import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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

describe("AppNav", () => {
  it("renders the brand and hot news ticker above the main navigation", () => {
    render(<AppNav />);

    const ticker = screen.getByRole("region", { name: "示例新闻" });
    const nav = screen.getByRole("navigation", { name: "主导航" });

    expect(within(ticker).getByRole("link", { name: /PROBABILITY/ })).toHaveAttribute(
      "href",
      "/",
    );
    expect(ticker).toHaveTextContent("示例新闻");
    expect(ticker).toHaveTextContent("美联储");
    expect(within(nav).queryByText(/PROBABILITY/)).not.toBeInTheDocument();
    expect(
      ticker.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders two group labels", () => {
    render(<AppNav />);

    expect(screen.getByText("事件情报平台")).toBeInTheDocument();
    expect(screen.getByText("Sports Prediction OS")).toBeInTheDocument();
  });

  it("renders all Event Intelligence entries", () => {
    render(<AppNav />);

    for (const label of [
      "监控面板", "决策机会", "Edge 监测", "人工分析",
      "历史复盘", "质量运营", "质量切片", "模拟交易",
    ]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("renders all Sports Prediction OS entries", () => {
    render(<AppNav />);

    for (const label of [
      "体育预测", "期货市场", "学习仪表盘", "体育市场",
      "参数优化", "体育推荐", "体育结算", "世界杯专属",
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

  it("links /sports/world-cup entry (migrated from /world-cup)", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /世界杯专属/ });
    expect(link).toHaveAttribute("href", "/sports/world-cup");
  });

  it("does not link the old /world-cup route", () => {
    render(<AppNav />);
    expect(screen.queryByRole("link", { name: /^世界杯$/ })).not.toBeInTheDocument();
  });

  it("keeps navigation labels on a single line", () => {
    render(<AppNav />);

    for (const label of [
      "监控面板", "决策机会", "Edge 监测", "人工分析",
      "历史复盘", "质量运营", "质量切片", "模拟交易",
      "体育预测", "期货市场", "学习仪表盘", "体育市场",
      "参数优化", "体育推荐", "体育结算", "世界杯专属",
    ]) {
      const link = screen.getByRole("link", { name: new RegExp(label) });
      expect(link).toHaveClass("whitespace-nowrap");
      expect(link).toHaveClass("shrink-0");
    }
  });
});
