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

describe("QualitySummaryPanel overlay flags", () => {
  it("says a layer is off only when the server explicitly says so", () => {
    const mixed: QualityMetricsSummary = {
      ...summary,
      overlay_flags: {
        decision_quality: false,
        market_quality: false,
        source_reliability: true,
        llm_telemetry: false,
      },
    };
    render(<QualitySummaryPanel summary={mixed} />);

    // decision_quality has no section of its own — it only gates the
    // "含决策质量" event-count row, so it shows 未启用 there rather than a note.
    expect(screen.getAllByText("未启用")).toHaveLength(3);
    // market_quality and llm_telemetry each own a gated section with a note.
    expect(screen.getByText(/该层未启用（MARKET_QUALITY_ENABLED=false）/)).toBeInTheDocument();
    expect(screen.getByText(/该层未启用（LLM_TELEMETRY_ENABLED=false）/)).toBeInTheDocument();
    expect(screen.queryByText(/该层未启用（DECISION_QUALITY_ENABLED=false）/)).toBeNull();
    // source_reliability is on, so it keeps its own numbers and no note.
    expect(screen.queryByText(/该层未启用（SOURCE_RELIABILITY_ENABLED=false）/)).toBeNull();
    expect(screen.getByText("平均来源数")).toBeInTheDocument();
  });

  it("gives every overlay section a note when all four layers are off", () => {
    const allOff: QualityMetricsSummary = {
      ...summary,
      overlay_flags: {
        decision_quality: false,
        market_quality: false,
        source_reliability: false,
        llm_telemetry: false,
      },
    };
    render(<QualitySummaryPanel summary={allOff} />);

    expect(screen.getAllByText(/该层未启用（/)).toHaveLength(3);
    // The three event-count rows flip to 未启用 as well.
    expect(screen.getAllByText("未启用")).toHaveLength(3);
  });

  it("treats an absent overlay_flags as unknown, never as disabled", () => {
    // `summary` carries no overlay_flags at all — the shape an older server
    // returns. The panel must keep its plain numbers rather than claim a layer
    // is off, otherwise a stale backend looks like a disabled deployment.
    render(<QualitySummaryPanel summary={summary} />);
    expect(screen.queryByText(/该层未启用/)).toBeNull();
    expect(screen.queryByText("未启用")).toBeNull();
  });

  it("keeps the numbers when every flag is explicitly true", () => {
    const allOn: QualityMetricsSummary = {
      ...summary,
      overlay_flags: {
        decision_quality: true,
        market_quality: true,
        source_reliability: true,
        llm_telemetry: true,
      },
    };
    render(<QualitySummaryPanel summary={allOn} />);
    expect(screen.queryByText(/该层未启用/)).toBeNull();
    expect(screen.queryByText("未启用")).toBeNull();
  });
});
