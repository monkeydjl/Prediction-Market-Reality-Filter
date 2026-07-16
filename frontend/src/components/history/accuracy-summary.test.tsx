import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AccuracySummary } from "./accuracy-summary";
import type { CalibrationAgg } from "@/lib/api";

describe("AccuracySummary", () => {
  it("renders dashes for skill and brier when no resolved samples", () => {
    const overall: CalibrationAgg = {
      brier_score: null,
      skill_score: null,
      grade: "no_data",
      n: 0,
    };
    render(<AccuracySummary overall={overall} />);

    // Two cards emit "—" (skill + brier); n card shows "0".
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("renders numeric skill, sample count, and brier when populated", () => {
    const overall: CalibrationAgg = {
      brier_score: 0.15,
      skill_score: 0.32,
      grade: "GOOD",
      n: 42,
    };
    render(<AccuracySummary overall={overall} />);

    expect(screen.getByText("0.32")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("0.150")).toBeInTheDocument();
  });

  it("labels the three cards", () => {
    render(
      <AccuracySummary
        overall={{ brier_score: 0.2, skill_score: 0.1, grade: "GOOD", n: 5 }}
      />,
    );
    expect(screen.getByText("技巧分数")).toBeInTheDocument();
    expect(screen.getByText("已结算样本")).toBeInTheDocument();
    expect(screen.getByText("平均 Brier 分数")).toBeInTheDocument();
  });
});
