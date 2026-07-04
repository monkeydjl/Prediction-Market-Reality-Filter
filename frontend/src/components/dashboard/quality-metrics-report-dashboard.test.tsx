import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QualityMetricsReportDashboard } from "./quality-metrics-report-dashboard";

vi.mock("@/lib/api", () => ({
  qualityMetricsApi: {
    report: vi.fn().mockResolvedValue({
      overview: { total_resolved: 2, with_calibration: 2, missing_calibration: 0 },
      by_source_type: {},
      by_analysis_quality: {},
      by_edge_bucket: {},
      by_source_reliability_bucket: {},
      calibration_deviation: [],
      report_errors: [],
    }),
    alerts: vi.fn().mockResolvedValue({
      alert_count: 1,
      alerts: [{ code: "brier_score_high", severity: "medium", detail: {} }],
    }),
    domainReliability: vi.fn().mockResolvedValue({
      total_domains: 1,
      total_rows: 1,
      domains: [{
        domain: "reuters.com",
        category: "_all",
        sample_count: 20,
        correct_count: 14,
        wrong_count: 6,
        credibility_sum: 15,
        reliability_score: 0.7,
        credibility_avg: 0.75,
        insufficient_samples: false,
      }],
    }),
  },
}));

describe("QualityMetricsReportDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders quality alerts and domain reliability on the linked quality page", async () => {
    render(<QualityMetricsReportDashboard />);

    await waitFor(() => expect(screen.getByText("质量告警")).toBeInTheDocument());
    expect(screen.getByText("brier_score_high")).toBeInTheDocument();
    expect(screen.getByText("域名可靠性")).toBeInTheDocument();
    expect(screen.getByText("reuters.com")).toBeInTheDocument();
  });
});
