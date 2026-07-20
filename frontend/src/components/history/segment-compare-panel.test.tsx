import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SegmentComparePanel } from "./segment-compare-panel";
import type { PredictionCalibration, CalibrationBucketSummary } from "@/lib/api";
import type { CategoryDatum } from "./category-accuracy";

const categoryData: CategoryDatum[] = [
  {
    category: "加密资产",
    brier: 0.12,
    skill: 0.4,
    count: 30,
    minSamples: 20,
    qualified: true,
  },
  {
    category: "政治",
    brier: 0.28,
    skill: 0.05,
    count: 8,
    minSamples: 20,
    qualified: false,
  },
];

const predCal: PredictionCalibration = {
  n: 12,
  brier_score: 0.15,
  grade: "GOOD",
  mean_raw_edge: 3,
  realized_edge: 2,
  directional_hit_rate: 0.6,
  segment_min_samples: 20,
  by_category: {
    crypto: { n: 10, brier_score: 0.11, skill_score: 0.35, grade: "GOOD" },
    politics: { n: 2, brier_score: 0.3, skill_score: -0.05, grade: "POOR" },
  },
  segments: {
    crypto: {
      n: 30,
      brier_score: 0.12,
      skill_score: 0.4,
      grade: "GOOD",
      market_brier_score: 0.2,
      qualified: true,
      segment_min_samples: 20,
    },
  },
};

const buckets: CalibrationBucketSummary = {
  n: 15,
  by_edge_bucket: {
    "0-5": { n: 5, brier_score: 0.2, direction_correct_rate: 0.4 },
    "5-10": { n: 10, brier_score: 0.12, direction_correct_rate: 0.7 },
  },
  by_confidence_bucket: {
    high: { n: 8, brier_score: 0.1, direction_correct_rate: 0.8 },
    low: { n: 7, brier_score: 0.22, direction_correct_rate: 0.5 },
  },
  by_edge_x_confidence: {
    "5-10|high": { n: 6, brier_score: 0.09, direction_correct_rate: 0.83 },
  },
};

describe("SegmentComparePanel", () => {
  it("renders segment mode table by default", () => {
    render(
      <SegmentComparePanel
        categoryData={categoryData}
        predCal={predCal}
        buckets={buckets}
      />,
    );
    expect(screen.getByTestId("segment-compare-panel")).toBeInTheDocument();
    expect(screen.getByTestId("compare-table")).toBeInTheDocument();
    expect(screen.getByText("加密资产")).toBeInTheDocument();
    expect(screen.getByText("政治")).toBeInTheDocument();
  });

  it("switches to act_category mode", async () => {
    const user = userEvent.setup();
    render(
      <SegmentComparePanel
        categoryData={categoryData}
        predCal={predCal}
        buckets={buckets}
      />,
    );
    await user.click(screen.getByTestId("compare-mode-act_category"));
    expect(screen.getByText("加密资产")).toBeInTheDocument();
    // act-only has skill from by_category
    expect(screen.getByText("0.35")).toBeInTheDocument();
  });

  it("shows edge buckets and cross product", async () => {
    const user = userEvent.setup();
    render(
      <SegmentComparePanel
        categoryData={categoryData}
        predCal={predCal}
        buckets={buckets}
      />,
    );
    await user.click(screen.getByTestId("compare-mode-edge_bucket"));
    expect(screen.getByText("0-5")).toBeInTheDocument();
    expect(screen.getByText("5-10")).toBeInTheDocument();
    expect(screen.getByText(/Edge × 置信度交叉表/)).toBeInTheDocument();
  });

  it("empty state when no category data", () => {
    render(
      <SegmentComparePanel
        categoryData={[]}
        predCal={null}
        buckets={null}
      />,
    );
    expect(screen.getByTestId("compare-empty")).toBeInTheDocument();
  });
});
