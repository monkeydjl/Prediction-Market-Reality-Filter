import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DecisionReport } from "@/lib/api";
import { DecisionCard } from "./decision-card";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

function baseReport(overrides: Partial<DecisionReport> = {}): DecisionReport {
  return {
    event_id: "evt-1",
    event: { title: "Test event", summary: "s" },
    probability: { estimated: 60, baseline: 50, change: 10, direction: "up" },
    market_view: {
      market_probability: 50,
      platform: "polymarket",
      liquidity: 1000,
      volume: 2000,
    },
    edge: { raw: 12, adjusted: 8, trust: 0.7 },
    diagnosis: {
      qualified: true,
      segment_n: 20,
      segment_min_samples: 10,
      segment_skill: 0.25,
      liquidity_factor: 0.9,
      reason: "已合格类别 + 调整后 edge 达到行动阈值",
    },
    confidence: { level: "medium", score: 0.7, confidence: 0.7 },
    recommendation: {
      decision: "act",
      action: "escalate",
      calibration_status: "calibrated",
    },
    risk: { level: "low", flags: ["thin_book"] },
    category: "politics",
    status: "open",
    actionable_recommendation: {
      direction: "YES",
      confidence: "high",
      suggested_allocation_pct: 2,
      edge: 8,
      risk_level: "low",
      rationale: "模型相对市场偏高",
      calibration_status: "calibrated",
    },
    final_displayed_direction: "YES",
    final_downgrade_reason: null,
    decision_quality: {
      decision_rationale_zh: "类别 skill 为正且流动性充足",
      downgrade_reason: null,
    },
    ...overrides,
  };
}

describe("DecisionCard", () => {
  it("shows diagnosis reason and final direction", () => {
    render(<DecisionCard report={baseReport()} />);
    expect(screen.getByText(/已合格类别/)).toBeInTheDocument();
    expect(screen.getByTestId("final-direction")).toHaveTextContent("YES");
  });

  it("expands diagnosis skill / samples and quality overlays", async () => {
    const user = userEvent.setup();
    render(
      <DecisionCard
        report={baseReport({
          decision_quality: {
            decision_rationale_zh: "类别 skill 为正",
            downgrade_reason: "evidence_thin",
          },
          market_quality: { downgrade_reason: "low_liquidity" },
        })}
      />,
    );
    await user.click(screen.getByTestId("decision-expand"));
    expect(screen.getByTestId("decision-diagnosis-detail")).toBeInTheDocument();
    expect(screen.getByTestId("calibration-status")).toHaveTextContent("已校准");
    expect(screen.getByTestId("decision-quality-rationale")).toHaveTextContent(
      "类别 skill 为正",
    );
    expect(screen.getByTestId("decision-quality-downgrade")).toHaveTextContent(
      "evidence_thin",
    );
    expect(screen.getByTestId("market-quality-downgrade")).toHaveTextContent(
      "low_liquidity",
    );
    expect(screen.getByTestId("risk-flags")).toHaveTextContent("thin_book");
    expect(screen.getByTestId("actionable-full")).toHaveTextContent("YES");
  });

  it("shows provisional calibration label", async () => {
    const user = userEvent.setup();
    render(
      <DecisionCard
        report={baseReport({
          recommendation: {
            decision: "provisional_act",
            action: "track",
            calibration_status: "uncalibrated_provisional",
          },
          diagnosis: {
            qualified: false,
            segment_n: 2,
            segment_min_samples: 10,
            segment_skill: null,
            liquidity_factor: 1,
            reason: "类别样本不足",
          },
        })}
      />,
    );
    await user.click(screen.getByTestId("decision-expand"));
    expect(screen.getByTestId("calibration-status")).toHaveTextContent("未校准");
  });
});
