import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SeriesPoint } from "./probability-chart";
import { ProbabilityChartRenderer, EdgeChartRenderer } from "./probability-chart-recharts";

// 概率图用到的 recharts 组件全部 mock 成带 testid 的占位元素，
// 便于断言渲染次数 / 是否出现。
vi.mock("recharts", () => {
  const React = require("react") as typeof import("react");
  const make = (testId: string) => (props: { children?: React.ReactNode }) =>
    React.createElement("div", { "data-testid": testId }, props.children);
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "responsive-container" }, children),
    LineChart: ({ children, data }: { children: React.ReactNode; data: unknown[] }) =>
      React.createElement(
        "div",
        { "data-testid": "line-chart", "data-count": data.length },
        children,
      ),
    Line: make("line"),
    XAxis: make("x-axis"),
    YAxis: make("y-axis"),
    CartesianGrid: make("cartesian-grid"),
    ReferenceLine: ({ y }: { y?: number }) =>
      React.createElement("div", { "data-testid": "reference-line", "data-y": y }),
    Tooltip: make("tooltip"),
    Brush: make("brush"),
  };
});

function makePoints(n: number): SeriesPoint[] {
  return Array.from({ length: n }, (_, i) => ({
    label: `t${i}`,
    model: 50 + i,
    market: 48 + i,
    edge: 2,
  }));
}

describe("ProbabilityChartRenderer", () => {
  it("渲染基础图表结构并传入数据点数量", () => {
    render(<ProbabilityChartRenderer data={makePoints(3)} baseline={50} />);

    expect(screen.getByTestId("line-chart")).toBeInTheDocument();
    expect(screen.getByTestId("line-chart").getAttribute("data-count")).toBe("3");
    // 模型估计与市场基准两条线
    expect(screen.getAllByTestId("line")).toHaveLength(2);
    // 基准参考线传入 baseline
    expect(screen.getByTestId("reference-line").getAttribute("data-y")).toBe("50");
  });

  it("数据点超过 10 时显示 Brush 缩放控件", () => {
    const { rerender } = render(<ProbabilityChartRenderer data={makePoints(10)} baseline={50} />);
    // 等于 10 时不显示（条件是 data.length > 10）
    expect(screen.queryByTestId("brush")).not.toBeInTheDocument();

    rerender(<ProbabilityChartRenderer data={makePoints(11)} baseline={50} />);
    expect(screen.getByTestId("brush")).toBeInTheDocument();
  });
});

describe("EdgeChartRenderer", () => {
  it("渲染 edge 趋势图与零基准线", () => {
    render(<EdgeChartRenderer data={makePoints(4)} />);

    expect(screen.getByTestId("line-chart").getAttribute("data-count")).toBe("4");
    // EdgeChart 只有一条 edge 线
    expect(screen.getAllByTestId("line")).toHaveLength(1);
    // 零基准参考线 y=0
    expect(screen.getByTestId("reference-line").getAttribute("data-y")).toBe("0");
  });
});
