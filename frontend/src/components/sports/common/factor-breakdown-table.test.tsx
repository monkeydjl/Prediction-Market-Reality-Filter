import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FactorBreakdownTable } from "./factor-breakdown-table";
import type { ContributionItem } from "@/lib/sports-api";

function makeItem(overrides: Partial<ContributionItem> = {}): ContributionItem {
  return {
    factor: "elo",
    direction: "support",
    weight: 0.3,
    available: true,
    detail: "P(home_win)=0.65",
    predicted_outcome: "home_win",
    ...overrides,
  };
}

describe("FactorBreakdownTable", () => {
  it("renders all items", () => {
    const items = [
      makeItem({ factor: "elo" }),
      makeItem({ factor: "home_court" }),
      makeItem({ factor: "rest" }),
      makeItem({ factor: "form" }),
      makeItem({ factor: "starting_pitcher" }),
    ];
    render(<FactorBreakdownTable items={items} />);
    expect(screen.getAllByRole("row")).toHaveLength(6); // 1 header + 5 data
  });

  it("displays factor name zh mapping", () => {
    render(<FactorBreakdownTable items={[makeItem({ factor: "elo" })]} />);
    expect(screen.getByText("Elo 等级分")).toBeDefined();
  });

  it("displays unmapped factor as-is", () => {
    render(<FactorBreakdownTable items={[makeItem({ factor: "unknown_factor" })]} />);
    expect(screen.getByText("unknown_factor")).toBeDefined();
  });

  it("unavailable factor row is greyed", () => {
    render(<FactorBreakdownTable items={[makeItem({ available: false })]} />);
    const row = screen.getAllByRole("row")[1];
    expect(row.className).toContain("opacity");
  });

  it("direction is translated", () => {
    render(<FactorBreakdownTable items={[makeItem({ direction: "support" })]} />);
    expect(screen.getByText(/支持/)).toBeDefined();
  });

  it("predicted_outcome shown in brackets", () => {
    render(<FactorBreakdownTable items={[makeItem({ predicted_outcome: "home_win" })]} />);
    expect(screen.getByText(/主胜/)).toBeDefined();
  });
});
