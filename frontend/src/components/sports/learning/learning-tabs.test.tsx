import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LearningTabs } from "./learning-tabs";

// Mock child panels
vi.mock("./engine-performance-panel", () => ({
  EnginePerformancePanel: () => <div data-testid="engine-panel">Engine Panel</div>,
}));
vi.mock("./prediction-history-list", () => ({
  PredictionHistoryList: () => <div data-testid="history-panel">History Panel</div>,
}));
vi.mock("./calibration-panel", () => ({
  CalibrationPanel: () => <div data-testid="calibration-panel">Calibration Panel</div>,
}));
vi.mock("./applied-weights-panel", () => ({
  AppliedWeightsPanel: () => <div data-testid="weights-panel">Weights Panel</div>,
}));

describe("LearningTabs", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
  });

  it("renders 4 tab buttons", () => {
    render(<LearningTabs />);
    expect(screen.getByText("性能对比")).toBeInTheDocument();
    expect(screen.getByText("预测历史")).toBeInTheDocument();
    expect(screen.getByText("校准诊断")).toBeInTheDocument();
    expect(screen.getByText("已应用权重")).toBeInTheDocument();
  });

  it("renders engine panel by default", () => {
    render(<LearningTabs />);
    expect(screen.getByTestId("engine-panel")).toBeInTheDocument();
  });

  it("switches to history panel on tab click", () => {
    render(<LearningTabs />);
    fireEvent.click(screen.getByText("预测历史"));
    expect(screen.getByTestId("history-panel")).toBeInTheDocument();
  });

  it("switches to calibration panel on tab click", () => {
    render(<LearningTabs />);
    fireEvent.click(screen.getByText("校准诊断"));
    expect(screen.getByTestId("calibration-panel")).toBeInTheDocument();
  });

  it("switches to applied weights panel on tab click", () => {
    render(<LearningTabs />);
    fireEvent.click(screen.getByText("已应用权重"));
    expect(screen.getByTestId("weights-panel")).toBeInTheDocument();
  });

  it("renders refresh button", () => {
    render(<LearningTabs />);
    expect(screen.getByText("刷新")).toBeInTheDocument();
  });
});
