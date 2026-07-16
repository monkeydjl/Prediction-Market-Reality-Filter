import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReliabilityChart } from "./reliability-chart";
import type { ReliabilityBin } from "@/lib/sports-api";

// Mock recharts
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  ScatterChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="scatter-chart">{children}</div>
  ),
  Scatter: ({ data }: { data: unknown[] }) => (
    <div data-testid="scatter" data-count={data.length} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  ReferenceLine: (props: { segment?: unknown[] }) => (
    <div data-testid="reference-line" data-has-segment={!!props.segment} />
  ),
  Tooltip: () => <div data-testid="tooltip" />,
}));

// Mock chart-lite
vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

describe("ReliabilityChart", () => {
  const nonEmptyBins: ReliabilityBin[] = [
    { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
    { lower: 0.6, upper: 0.7, center: 0.65, avg_predicted: 0.62, actual_frequency: 0.70, count: 8 },
  ];

  it("renders chart frame and scatter", () => {
    render(<ReliabilityChart bins={nonEmptyBins} />);
    expect(screen.getByTestId("chart-frame")).toBeInTheDocument();
    expect(screen.getByTestId("scatter")).toBeInTheDocument();
  });

  it("passes non-empty bins to Scatter as data points", () => {
    render(<ReliabilityChart bins={nonEmptyBins} />);
    const scatter = screen.getByTestId("scatter");
    expect(scatter.getAttribute("data-count")).toBe("2");
  });

  it("renders diagonal reference line", () => {
    render(<ReliabilityChart bins={nonEmptyBins} />);
    const refLine = screen.getByTestId("reference-line");
    expect(refLine.getAttribute("data-has-segment")).toBe("true");
  });

  it("renders with empty bins without crashing", () => {
    const emptyBins: ReliabilityBin[] = Array.from({ length: 10 }, (_, i) => ({
      lower: i * 0.1,
      upper: (i + 1) * 0.1,
      center: i * 0.1 + 0.05,
      avg_predicted: null,
      actual_frequency: null,
      count: 0,
    }));
    render(<ReliabilityChart bins={emptyBins} />);
    expect(screen.getByTestId("scatter").getAttribute("data-count")).toBe("0");
  });

  it("skips empty bins (null avg_predicted) in scatter data", () => {
    const mixedBins: ReliabilityBin[] = [
      { lower: 0.0, upper: 0.1, center: 0.05, avg_predicted: null, actual_frequency: null, count: 0 },
      { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
      { lower: 0.9, upper: 1.0, center: 0.95, avg_predicted: null, actual_frequency: null, count: 0 },
    ];
    render(<ReliabilityChart bins={mixedBins} />);
    expect(screen.getByTestId("scatter").getAttribute("data-count")).toBe("1");
  });
});
