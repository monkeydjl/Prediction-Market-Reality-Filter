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

describe("AppNav", () => {
  it("renders the brand and hot news ticker above the main navigation", () => {
    render(<AppNav />);

    const ticker = screen.getByRole("region", { name: "热点新闻" });
    const nav = screen.getByRole("navigation", { name: "主导航" });

    expect(within(ticker).getByRole("link", { name: /PROBABILITY/ })).toHaveAttribute(
      "href",
      "/",
    );
    expect(ticker).toHaveTextContent("热点新闻");
    expect(ticker).toHaveTextContent("美联储");
    expect(within(nav).queryByText(/PROBABILITY/)).not.toBeInTheDocument();
    expect(
      ticker.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps navigation labels on a single line", () => {
    render(<AppNav />);

    for (const label of ["监控面板", "决策机会", "Edge 监测", "人工分析", "历史复盘", "质量切片", "模拟交易", "世界杯"]) {
      const link = screen.getByRole("link", { name: new RegExp(label) });
      expect(link).toHaveClass("whitespace-nowrap");
      expect(link).toHaveClass("shrink-0");
    }
  });
});
