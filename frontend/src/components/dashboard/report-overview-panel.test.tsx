import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportOverviewPanel } from "./report-overview-panel";
import type { QualityMetricsReport } from "@/lib/api";

const report: QualityMetricsReport = {
  overview: {
    total_resolved: 12,
    with_calibration: 8,
    missing_calibration: 4,
  },
  by_source_type: {},
  by_analysis_quality: {},
  by_edge_bucket: {},
  by_source_reliability_bucket: {},
  calibration_deviation: [],
  report_errors: [],
};

describe("ReportOverviewPanel", () => {
  it("report 为 null 时渲染加载占位符", () => {
    render(<ReportOverviewPanel report={null} />);
    expect(screen.getByText(/加载汇总/)).toBeInTheDocument();
  });

  it("report 存在时渲染概览标题和已结算事件统计", () => {
    render(<ReportOverviewPanel report={report} />);
    expect(screen.getByText("概览")).toBeInTheDocument();
    expect(screen.getByText("已结算事件")).toBeInTheDocument();
    // total_resolved=12, with_calibration=8, missing_calibration=4
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    // 标签列
    expect(screen.getByText("总数")).toBeInTheDocument();
    expect(screen.getByText("含校准快照")).toBeInTheDocument();
    expect(screen.getByText("缺失校准")).toBeInTheDocument();
  });

  it("渲染说明文案", () => {
    render(<ReportOverviewPanel report={report} />);
    expect(screen.getByText(/按 4 个维度切片统计方向准确率与 Brier 分数/)).toBeInTheDocument();
    expect(screen.getByText("analysis_quality")).toBeInTheDocument();
  });
});
