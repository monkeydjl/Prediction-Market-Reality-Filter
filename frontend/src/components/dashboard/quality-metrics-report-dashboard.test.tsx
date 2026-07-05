import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QualityMetricsReportDashboard } from "./quality-metrics-report-dashboard";
import { qualityMetricsApi } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  qualityMetricsApi: {
    report: vi.fn().mockResolvedValue({
      overview: {},
      by_source_type: {},
      by_analysis_quality: {},
      by_edge_bucket: {},
      by_source_reliability_bucket: {},
      calibration_deviation: [],
      report_errors: [],
    }),
  },
}));

vi.mock("./report-overview-panel", () => ({
  ReportOverviewPanel: () => <div>overview</div>,
}));

vi.mock("./report-slice-table", () => ({
  ReportSliceTable: () => <div>slice</div>,
}));

vi.mock("./calibration-deviation-chart", () => ({
  CalibrationDeviationChart: () => <div>calibration</div>,
}));

describe("QualityMetricsReportDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
  });

  it("does not auto-refresh while the tab is hidden", async () => {
    vi.useFakeTimers();
    try {
      render(<QualityMetricsReportDashboard />);
      await act(async () => {
        vi.runOnlyPendingTimers();
        await Promise.resolve();
      });

      vi.clearAllMocks();
      Object.defineProperty(document, "hidden", {
        configurable: true,
        value: true,
      });

      await act(async () => {
        vi.advanceTimersByTime(60_000);
        await Promise.resolve();
      });

      expect(qualityMetricsApi.report).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
