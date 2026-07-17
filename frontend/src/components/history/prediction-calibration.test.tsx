import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PredictionCalibrationCard } from "./prediction-calibration";
import type { PredictionCalibration } from "@/lib/api";

describe("PredictionCalibrationCard", () => {
  it("无已结算样本时 edge 与命中率显示破折号、样本数为 0", () => {
    const data: PredictionCalibration = {
      n: 0,
      brier_score: null,
      grade: "no_data",
      mean_raw_edge: null,
      realized_edge: null,
      directional_hit_rate: null,
      by_category: {},
    };
    render(<PredictionCalibrationCard data={data} />);

    expect(screen.getByText("0")).toBeInTheDocument();
    // 已实现 edge 与方向命中率两张卡都应显示 "—"
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("有已结算样本时渲染数值化的 edge 与命中率", () => {
    const data: PredictionCalibration = {
      n: 12,
      brier_score: 0.18,
      grade: "GOOD",
      mean_raw_edge: 3.2,
      realized_edge: 4.5,
      directional_hit_rate: 0.667,
      by_category: {},
    };
    render(<PredictionCalibrationCard data={data} />);

    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("4.5pt")).toBeInTheDocument();
    // 0.667 * 100 = 66.7 → toFixed(0) = "67"
    expect(screen.getByText("67%")).toBeInTheDocument();
  });

  it("渲染三张卡片的标题", () => {
    const data: PredictionCalibration = {
      n: 1,
      brier_score: 0.2,
      grade: "GOOD",
      mean_raw_edge: 1,
      realized_edge: 1,
      directional_hit_rate: 0.5,
      by_category: {},
    };
    render(<PredictionCalibrationCard data={data} />);

    expect(screen.getByText("已行动样本")).toBeInTheDocument();
    expect(screen.getByText("已实现 edge")).toBeInTheDocument();
    expect(screen.getByText("方向命中率")).toBeInTheDocument();
  });
});
