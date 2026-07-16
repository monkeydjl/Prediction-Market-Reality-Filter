import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnomalyBanner } from "./anomaly-banner";
import type { QualityMetricsAnomaly } from "@/lib/api";

describe("AnomalyBanner", () => {
  it("renders the healthy state when there are no anomalies", () => {
    render(<AnomalyBanner anomalies={[]} />);
    expect(screen.getByText(/无异常/)).toBeInTheDocument();
    expect(screen.queryByText(/调度器未运行/)).not.toBeInTheDocument();
  });

  it("renders one banner per anomaly with code label and severity", () => {
    const anomalies: QualityMetricsAnomaly[] = [
      { code: "scheduler_not_running", severity: "high", detail: "down for 5m" },
      { code: "calibration_brier_high", severity: "medium", detail: { brier: 0.32 } },
    ];
    render(<AnomalyBanner anomalies={anomalies} />);

    expect(screen.getByText("调度器未运行")).toBeInTheDocument();
    expect(screen.getByText("Brier 分数过高")).toBeInTheDocument();
    expect(screen.getByText("高")).toBeInTheDocument();
    expect(screen.getByText("中")).toBeInTheDocument();
    // String detail rendered verbatim; object detail JSON-serialized
    expect(screen.getByText("down for 5m")).toBeInTheDocument();
    expect(screen.getByText(/"brier"/)).toBeInTheDocument();
  });

  it("falls back to raw code and severity for unknown values", () => {
    render(
      <AnomalyBanner
        anomalies={[{ code: "custom_code", severity: "critical", detail: "x" }]}
      />,
    );
    expect(screen.getByText("custom_code")).toBeInTheDocument();
    expect(screen.getByText("critical")).toBeInTheDocument();
  });
});
