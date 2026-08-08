import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QualityAlertsPanel } from "./quality-alerts-panel";
import { qualityMetricsApi } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  qualityMetricsApi: {
    alerts: vi.fn(),
  },
}));

const alertsMock = vi.mocked(qualityMetricsApi.alerts);

describe("QualityAlertsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    alertsMock.mockResolvedValue({
      alerts: [
        {
          code: "direction_accuracy_low",
          severity: "high",
          scope: "overview",
          dimension: null,
          slice: null,
          metric: "direction_accuracy",
          value: 0.47,
          threshold: 0.5,
          n: 42,
        },
        {
          code: "brier_score_high",
          severity: "medium",
          scope: "slice",
          dimension: "by_source_type",
          slice: "prediction_market",
          metric: "brier_score",
          value: 0.31,
          threshold: 0.25,
          n: 18,
        },
      ],
      alert_count: 2,
    });
  });

  it("renders triggered alerts with values and thresholds", async () => {
    render(<QualityAlertsPanel />);

    // The <section> renders during loading, so await the loaded content itself
    // rather than the test id — otherwise the assertions race the deferred load.
    await screen.findByText("direction_accuracy_low");
    const panel = screen.getByTestId("quality-alerts-panel");
    expect(panel).toHaveTextContent("质量告警");
    expect(panel).toHaveTextContent("0.470");
    expect(panel).toHaveTextContent("0.500");
    expect(panel).toHaveTextContent("brier_score_high");
  });

  it("renders the empty state when no alerts trigger", async () => {
    alertsMock.mockResolvedValue({ alerts: [], alert_count: 0 });
    render(<QualityAlertsPanel />);

    expect(await screen.findByText(/当前阈值下无触发告警/)).toBeInTheDocument();
  });

  it("shows an error state on API failure", async () => {
    alertsMock.mockRejectedValue(new Error("boom"));
    render(<QualityAlertsPanel />);

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });
});
