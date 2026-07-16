import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportSliceTable } from "./report-slice-table";
import type { QualityReportSlice } from "@/lib/api";

function makeSlice(overrides: Partial<QualityReportSlice> = {}): QualityReportSlice {
  return {
    n: 5,
    direction_correct_true: 3,
    direction_correct_false: 2,
    direction_correct_none: 0,
    direction_accuracy: 0.6,
    brier: { brier_score: 0.18, skill_score: 0.1, grade: "GOOD", n: 5 },
    ...overrides,
  };
}

describe("ReportSliceTable", () => {
  it("renders the empty-state message when slices is empty", () => {
    render(
      <ReportSliceTable title="按来源" subtitle="by_source_type" slices={{}} />,
    );
    expect(screen.getByText("按来源")).toBeInTheDocument();
    expect(screen.getByText("无数据")).toBeInTheDocument();
  });

  it("renders one row per slice, sorted by n descending, with grade label", () => {
    const slices = {
      prediction_market: makeSlice({ n: 5 }),
      unknown: makeSlice({ n: 12, brier: { brier_score: 0.32, skill_score: -0.05, grade: "POOR", n: 12 } }),
    };
    render(<ReportSliceTable title="按来源" subtitle="by_source_type" slices={slices} />);

    const rows = screen.getAllByRole("row");
    // 1 header + 2 data rows
    expect(rows).toHaveLength(3);

    // Sorted by n desc → "unknown" (12) appears first, "prediction_market" (5) second
    expect(rows[1]).toHaveTextContent("未知");
    expect(rows[1]).toHaveTextContent("12");
    expect(rows[1]).toHaveTextContent("差");
    expect(rows[2]).toHaveTextContent("预测市场");
    expect(rows[2]).toHaveTextContent("5");
    expect(rows[2]).toHaveTextContent("良好");
  });

  it("emits dashes for null accuracy and null brier", () => {
    const slices = {
      prediction_market: makeSlice({
        direction_accuracy: null,
        brier: { brier_score: null, skill_score: null, grade: "no_data", n: 0 },
      }),
    };
    render(<ReportSliceTable title="t" subtitle="s" slices={slices} />);
    // Multiple "—" elements (accuracy, brier). Assert at least one is present.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("无数据")).toBeInTheDocument();
  });
});
