import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QualityOperationsDashboard } from "./quality-operations-dashboard";

// Mock the API client so the dashboard renders with deterministic data.
vi.mock("@/lib/api", () => ({
  qualityMetricsApi: {
    summary: vi.fn().mockResolvedValue({
      timeframe: "24h",
      counts: { events: 5, resolved_events: 2, with_decision_quality: 5,
                with_market_quality: 3, with_source_reliability: 4, with_llm_telemetry: 5 },
      final_direction: { YES: 2, NO: 1, WAIT: 1, AVOID: 1 },
      consensus: { none: 1, low: 1, medium: 2, high: 1 },
      downgrade: { final_downgrade_reason_present: 1,
        build_errors: { decision_quality: 0, market_quality: 0, source_reliability: 0 } },
      market_quality: { count: 3, wide_spread_flag_count: 0, thin_market_flag_count: 0,
        score_avg: 0.7, score_min: 0.6, score_max: 0.8 },
      source_reliability: { count: 4, overall_score_avg: 0.75,
        source_count_avg: 3, domain_diversity_avg: 2 },
      llm_telemetry: { count: 5, degraded_mode_count: 0, estimated_token_cost_total: 0.01 },
      calibration: { n: 2, brier_score: 0.15, grade: "GOOD" },
      calibration_buckets: {},
      scheduler: { last_runs: {}, recent_failed_count: 0, recent_runs_count: 10 },
    }),
    timeseries: vi.fn().mockResolvedValue({ window: "7d", points: [] }),
    anomalies: vi.fn().mockResolvedValue({ count: 0, anomalies: [] }),
    alerts: vi.fn().mockResolvedValue({
      alert_count: 1,
      alerts: [{ code: "direction_accuracy_low", severity: "high", detail: { slice: "overview" } }],
    }),
    domainReliability: vi.fn().mockResolvedValue({
      total_domains: 1,
      total_rows: 1,
      domains: [{
        domain: "reuters.com",
        category: "_all",
        sample_count: 12,
        correct_count: 8,
        wrong_count: 4,
        credibility_sum: 9.6,
        reliability_score: 0.667,
        credibility_avg: 0.8,
        insufficient_samples: false,
      }],
    }),
    drift: vi.fn().mockResolvedValue({
      recent_window_n: 50,
      drift: { drift_score: 0.1, recent_mean: 0.2, baseline_mean: 0.18,
               recent_n: 5, baseline_n: 10 },
      ece: { recent: 0.05, baseline: 0.04 },
      degraded_mixing: { recent_degraded_count: 0, recent_n: 5, contaminated: false },
      buckets: {},
      alerts: [],
      alerts_enabled: false,
    }),
  },
}));

describe("QualityOperationsDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the summary panel with event count", async () => {
    render(<QualityOperationsDashboard />);
    await waitFor(() => {
      // Brief's verbatim `/5/` regex over-matches (multiple stats render "5":
      // events=5, with_decision_quality=5, with_llm_telemetry=5, lt.count=5).
      // Use exact match + getAllByText to verify at least one "5" renders.
      expect(screen.getAllByText(/^5$/)[0]).toBeInTheDocument();
    });
    expect(screen.getByText(/质量运营仪表盘/)).toBeInTheDocument();
  });

  it("renders the anomaly banner with zero anomalies", async () => {
    render(<QualityOperationsDashboard />);
    await waitFor(() => {
      expect(screen.getByText(/无异常/)).toBeInTheDocument();
    });
  });

  it("renders the drift panel with drift score", async () => {
    render(<QualityOperationsDashboard />);
    await waitFor(() => {
      // Brief's verbatim `/0.1/` regex matches "0.1800" (baseline_mean) and
      // "0.15" (calibration brier) but NOT the actual rendered drift score.
      // The brief's DriftPanel implementation formats driftScore as
      // `${(driftScore * 100).toFixed(1)}%` → "10.0%". Match that format.
      expect(screen.getByText(/10\.0%/)).toBeInTheDocument();
    });
  });

  it("renders quality alerts and domain reliability entrypoints", async () => {
    render(<QualityOperationsDashboard />);

    await waitFor(() => {
      expect(screen.getByText("质量告警")).toBeInTheDocument();
    });
    expect(screen.getByText("direction_accuracy_low")).toBeInTheDocument();
    expect(screen.getByText("域名可靠性")).toBeInTheDocument();
    expect(screen.getByText("reuters.com")).toBeInTheDocument();
  });
});
