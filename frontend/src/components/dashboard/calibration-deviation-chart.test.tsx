import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CalibrationDeviationChart } from "./calibration-deviation-chart";
import type { CalibrationDeviationRow } from "@/lib/api";

// Mock recharts — 组件直接从 "recharts" 导入 Bar/BarChart/XAxis/YAxis/CartesianGrid/ReferenceLine，
// 而 ChartFrame/DarkTooltip 内部又通过 "recharts" 使用 ResponsiveContainer/Tooltip，
// mock 整个 "recharts" 模块同时覆盖两处。
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  BarChart: ({ children, data }: { children: React.ReactNode; data: unknown[] }) => (
    <div data-testid="bar-chart" data-count={data.length}>
      {children}
    </div>
  ),
  Bar: ({ name }: { name: string }) => <div data-testid="bar" data-name={name} />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  ReferenceLine: () => <div data-testid="reference-line" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

describe("CalibrationDeviationChart", () => {
  it("rows 为空时渲染无数据占位", () => {
    render(<CalibrationDeviationChart rows={[]} />);
    expect(screen.getByText("校准偏差")).toBeInTheDocument();
    expect(screen.getByText("无校准数据")).toBeInTheDocument();
    expect(screen.queryByTestId("bar-chart")).not.toBeInTheDocument();
  });

  it("过滤 n=0 的桶，仅渲染有数据的桶", () => {
    const rows: CalibrationDeviationRow[] = [
      { bucket: "0-20%", n: 0, predicted_mean: 0.1, actual_mean: 0.15, deviation: -0.05 },
      { bucket: "20-40%", n: 5, predicted_mean: 0.3, actual_mean: 0.32, deviation: -0.02 },
      { bucket: "40-60%", n: 8, predicted_mean: 0.5, actual_mean: 0.45, deviation: 0.05 },
    ];
    render(<CalibrationDeviationChart rows={rows} />);

    // n=0 的桶被过滤掉，不渲染
    expect(screen.queryByText(/0-20%/)).not.toBeInTheDocument();
    // 有数据的桶渲染出来（桶名与 n=、偏差等文本被多个子元素拆分，用 regex 匹配）
    expect(screen.getByText(/20-40%/)).toBeInTheDocument();
    expect(screen.getByText(/40-60%/)).toBeInTheDocument();
    // BarChart 接收到 2 条数据
    expect(screen.getByTestId("bar-chart").getAttribute("data-count")).toBe("2");
    // 两个 Bar（预测均值 + 实际均值）
    expect(screen.getAllByTestId("bar")).toHaveLength(2);
  });

  it("偏差图例展示正/负/零三种符号", () => {
    const rows: CalibrationDeviationRow[] = [
      { bucket: "low", n: 3, predicted_mean: 0.3, actual_mean: 0.2, deviation: 0.1 },
      { bucket: "mid", n: 4, predicted_mean: 0.5, actual_mean: 0.6, deviation: -0.1 },
      { bucket: "high", n: 2, predicted_mean: 0.7, actual_mean: 0.7, deviation: 0 },
    ];
    render(<CalibrationDeviationChart rows={rows} />);
    // 正偏差显示 +，负偏差不带 +，零偏差显示 ±0
    expect(screen.getByText(/\+0\.1/)).toBeInTheDocument();
    expect(screen.getByText(/^-0\.1$/)).toBeInTheDocument();
    expect(screen.getByText("±0")).toBeInTheDocument();
  });
});
