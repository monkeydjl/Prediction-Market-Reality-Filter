import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  BarChart: ({ children, data }: { children: React.ReactNode; data: unknown[] }) => (
    <div data-testid="bar-chart" data-count={data.length}>
      {children}
    </div>
  ),
  Bar: ({ children }: { children: React.ReactNode }) => <div data-testid="bar">{children}</div>,
  Cell: ({ fill }: { fill?: string }) => <div data-testid="cell" data-fill={fill ?? ""} />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  ReferenceLine: () => <div data-testid="reference-line" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

import { CategoryAccuracyChart } from "./category-accuracy-chart";
import type { CategoryDatum } from "./category-accuracy";

const data: CategoryDatum[] = [
  { category: "加密资产", brier: 0.12, skill: 0.4, count: 30, minSamples: 20, qualified: true },
  { category: "政治", brier: 0.25, skill: 0.1, count: 8, minSamples: 20, qualified: false },
  { category: "宏观", brier: 0.3, skill: -0.15, count: 12, minSamples: null, qualified: null },
];

describe("CategoryAccuracyChart", () => {
  it("渲染柱状图并将数据条数透传给 BarChart", () => {
    render(<CategoryAccuracyChart data={data} />);

    expect(screen.getByTestId("chart-frame")).toBeInTheDocument();
    const chart = screen.getByTestId("bar-chart");
    expect(chart).toBeInTheDocument();
    expect(chart.getAttribute("data-count")).toBe("3");
  });

  it("为每条数据渲染一个 Cell", () => {
    render(<CategoryAccuracyChart data={data} />);

    const cells = screen.getAllByTestId("cell");
    expect(cells).toHaveLength(3);
  });

  it("根据 skill 高低选择不同的填充色", () => {
    render(<CategoryAccuracyChart data={data} />);

    const cells = screen.getAllByTestId("cell");
    // skill >= 0.25 → var(--pos)；0 <= skill < 0.25 → var(--warn)；负值 → var(--neg)
    expect(cells[0].getAttribute("data-fill")).toBe("var(--pos)");
    expect(cells[1].getAttribute("data-fill")).toBe("var(--warn)");
    expect(cells[2].getAttribute("data-fill")).toBe("var(--neg)");
  });
});
