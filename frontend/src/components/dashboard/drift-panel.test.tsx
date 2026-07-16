import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DriftPanel } from "./drift-panel";
import type { QualityMetricsDrift } from "@/lib/api";

const drift: QualityMetricsDrift = {
  recent_window_n: 50,
  drift: {
    drift_score: 0.4,
    recent_mean: 0.25,
    baseline_mean: 0.18,
    recent_n: 5,
    baseline_n: 10,
  },
  ece: { recent: 0.05, baseline: 0.04 },
  degraded_mixing: {
    recent_degraded_count: 1,
    recent_n: 5,
    contaminated: true,
  },
  buckets: {},
  alerts: [
    { code: "brier_relative_drift", severity: "medium", detail: "rose 38%" },
  ],
  alerts_enabled: true,
};

describe("DriftPanel", () => {
  it("renders the loading placeholder when drift is null", () => {
    render(<DriftPanel drift={null} />);
    expect(screen.getByText(/加载漂移数据/)).toBeInTheDocument();
  });

  it("renders drift score, ECE, and alert badge when populated", () => {
    render(<DriftPanel drift={drift} />);

    expect(screen.getByText("校准漂移")).toBeInTheDocument();
    expect(screen.getByText("告警已启用")).toBeInTheDocument();
    // drift_score 0.4 → 40.0%
    expect(screen.getByText("40.0%")).toBeInTheDocument();
    // recent_mean 0.25 → 0.2500
    expect(screen.getByText("0.2500")).toBeInTheDocument();
    // baseline_mean 0.18 → 0.1800
    expect(screen.getByText("0.1800")).toBeInTheDocument();
    // ece recent 0.05 → 0.0500
    expect(screen.getByText("0.0500")).toBeInTheDocument();
    // Triggered alert rendered with code + severity
    expect(screen.getByText(/brier_relative_drift/)).toBeInTheDocument();
    expect(screen.getByText(/\(medium\)/)).toBeInTheDocument();
    // Degraded mixing notice
    expect(screen.getByText(/1 条 LLM 降级样本/)).toBeInTheDocument();
  });

  it("omits the alerts section when there are no alerts", () => {
    render(<DriftPanel drift={{ ...drift, alerts: [] }} />);
    expect(screen.queryByText("触发的告警：")).not.toBeInTheDocument();
  });
});
