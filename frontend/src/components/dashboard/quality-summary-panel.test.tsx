import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualitySummaryPanel } from "./quality-summary-panel";
import type { QualityMetricsSummary } from "@/lib/api";

const summary: QualityMetricsSummary = {
  timeframe: "24h",
  counts: {
    events: 5,
    resolved_events: 2,
    with_decision_quality: 4,
    with_market_quality: 3,
    with_source_reliability: 3,
    with_llm_telemetry: 4,
  },
  final_direction: { YES: 2, NO: 1, WAIT: 1, AVOID: 1 },
  consensus: { low: 1, medium: 2, high: 1 },
  downgrade: {
    final_downgrade_reason_present: 1,
    build_errors: { decision_quality: 0, market_quality: 0, source_reliability: 0 },
  },
  market_quality: {
    count: 3,
    wide_spread_flag_count: 0,
    thin_market_flag_count: 1,
    score_avg: 0.7,
    score_min: 0.4,
    score_max: 0.9,
  },
  source_reliability: {
    count: 3,
    overall_score_avg: 0.75,
    source_count_avg: 2.5,
    domain_diversity_avg: 2,
  },
  llm_telemetry: {
    count: 4,
    degraded_mode_count: 1,
    estimated_token_cost_total: 0.0123,
  },
  calibration: { brier_score: 0.18, grade: "GOOD", n: 2 },
  calibration_buckets: {},
  scheduler: {
    last_runs: {},
    recent_failed_count: 0,
    recent_runs_count: 5,
  },
};

describe("QualitySummaryPanel", () => {
  it("renders the loading placeholder when summary is null", () => {
    render(<QualitySummaryPanel summary={null} />);
    expect(screen.getByText(/加载汇总/)).toBeInTheDocument();
  });

  it("renders event counts and direction distribution from the summary", () => {
    render(<QualitySummaryPanel summary={summary} />);

    expect(screen.getByText("质量汇总")).toBeInTheDocument();
    // Event counts section
    expect(screen.getByText("在库事件")).toBeInTheDocument();
    // Multiple elements render "5" (events, with_decision_quality, lt.count);
    // use getAllByText to assert presence without ambiguity.
    expect(screen.getAllByText("5").length).toBeGreaterThan(0);
    expect(screen.getByText("已结算")).toBeInTheDocument();
    // "2" appears multiple times (resolved_events=2, YES=2, calibration n=2);
    // use getAllByText to assert presence without ambiguity.
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    // Final direction distribution
    expect(screen.getByText("YES")).toBeInTheDocument();
    expect(screen.getByText("NO")).toBeInTheDocument();
    expect(screen.getByText("WAIT")).toBeInTheDocument();
    expect(screen.getByText("AVOID")).toBeInTheDocument();
    // Calibration section
    expect(screen.getByText("Brier")).toBeInTheDocument();
    expect(screen.getByText("GOOD")).toBeInTheDocument();
  });
});
