import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RecommendationCard } from "./RecommendationCard";
import type { SportRecommendation } from "@/lib/sports-api";

const baseRec: SportRecommendation = {
  match_id: "m1",
  mapped_outcome: "home_win",
  direction: "YES",
  decision: "act",
  confidence: "high",
  risk_level: "low",
  edge_pct: 7.2,
  raw_edge_pct: 10.0,
  trust: 0.72,
  liquidity_factor: 1.0,
  stale: false,
  suggested_allocation_pct: 2.0,
  calibration_status: "calibrated",
  rationale: "模型看好主胜，调整后边际 +7.20pp。决策建议：act。本分析仅供参考，不构成投资建议。",
  engine_name: "BasketballEngine",
  competition: "nba",
  prediction_timestamp: "2026-07-16T10:00:00Z",
  model_prob: 0.65,
  market_prob: 0.55,
  sources_count: 1,
  captured_at: "2026-07-16T10:00:00Z",
};

describe("RecommendationCard", () => {
  it("renders direction badge", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("direction-m1").textContent).toBe("YES");
  });

  it("renders edge with + sign for positive", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("edge-m1").textContent).toBe("+7.20pp");
  });

  it("renders rationale in expanded mode", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("rationale-m1").textContent).toContain("主胜");
    expect(screen.getByTestId("rationale-m1").textContent).toContain("仅供参考");
  });

  it("renders allocation when > 0", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("allocation-m1").textContent).toContain("2%");
  });

  it("hides AVOID in summary mode", () => {
    const avoidRec = { ...baseRec, direction: "AVOID" };
    const { container } = render(<RecommendationCard rec={avoidRec} summary={true} />);
    expect(container.firstChild).toBeNull();
  });
});
