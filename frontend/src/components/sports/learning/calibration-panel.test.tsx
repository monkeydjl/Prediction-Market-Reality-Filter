import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { CalibrationPanel } from "./calibration-panel";

// Mock recharts (needed because ReliabilityChart is rendered)
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
  ReferenceLine: () => <div data-testid="reference-line" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

vi.mock("@/lib/learning-api", () => ({
  fetchCalibration: vi.fn(),
  fetchReliability: vi.fn(),
}));

import { fetchCalibration, fetchReliability } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchCalibration).mockReset();
  vi.mocked(fetchReliability).mockReset();
});

const mockCal = {
  engine: "basketball",
  competition: "nba",
  slope: 0.85,
  intercept: 0.05,
  sample_count: 48,
  avg_confidence: 0.62,
  avg_accuracy: 0.625,
  last_updated: "2026-07-14T18:30:00Z",
};

const mockReliability = {
  engine: null,
  competition: null,
  bins: [
    { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
  ],
  total_samples: 48,
};

describe("CalibrationPanel", () => {
  it("renders parameter table with calibration data", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([mockCal]);
    vi.mocked(fetchReliability).mockResolvedValueOnce(mockReliability);
    render(<CalibrationPanel />);
    await waitFor(() => {
      // Use getByRole("cell") to disambiguate from <option> elements with same text
      expect(screen.getByRole("cell", { name: "basketball" })).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "0.85" })).toBeInTheDocument();
    });
  });

  it("renders reliability chart", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([mockCal]);
    vi.mocked(fetchReliability).mockResolvedValueOnce(mockReliability);
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("scatter")).toBeInTheDocument();
    });
  });

  it("renders empty state for calibration when no data", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([]);
    vi.mocked(fetchReliability).mockResolvedValueOnce({ ...mockReliability, total_samples: 0, bins: [] });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("暂无校准数据，需 ≥ MIN_SAMPLES_FOR_CALIBRATION 条记录")).toBeInTheDocument();
    });
  });

  it("renders filter dropdowns", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([]);
    vi.mocked(fetchReliability).mockResolvedValueOnce({ ...mockReliability, total_samples: 0, bins: [] });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("引擎")).toBeInTheDocument();
    });
  });
});
