import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useOptimizationParams: vi.fn(),
  useTaskStatus: vi.fn(),
  triggerOptimization: vi.fn(),
  triggerIngest: vi.fn(),
  applyParams: vi.fn(),
}));

vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useOptimizationParams: apiMocks.useOptimizationParams,
  useTaskStatus: apiMocks.useTaskStatus,
  triggerOptimization: apiMocks.triggerOptimization,
  triggerIngest: apiMocks.triggerIngest,
  applyParams: apiMocks.applyParams,
}));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({
    data: undefined,
    error: undefined,
    isLoading: true,
  }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { OptimizationDashboard } from "./OptimizationDashboard";

describe("OptimizationDashboard", () => {
  beforeEach(() => {
    apiMocks.useOptimizationParams.mockReset();
    apiMocks.useTaskStatus.mockReset();
    apiMocks.triggerOptimization.mockReset();
    apiMocks.triggerIngest.mockReset();
    apiMocks.applyParams.mockReset();
    apiMocks.useTaskStatus.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
    });
  });

  it("shows loading state initially", () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    render(<OptimizationDashboard />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows run optimization button", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("run-optimization-button")).toBeTruthy();
    });
  });

  it("shows ingest button", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("ingest-button")).toBeTruthy();
    });
  });

  it("shows empty state when no params", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("displays params table when data available", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [
        {
          id: 1,
          sport: "nba",
          competition: "nba",
          factor_weights: '{"elo": 0.50}',
          elo_params: '{"hfa": 110}',
          score: 0.75,
          accuracy: 0.7,
          brier_score: 0.2,
          mae: 0.3,
          sample_count: 100,
          trial_number: null,
          status: "applied",
          created_at: "2026-07-16T10:00:00Z",
          applied_at: "2026-07-16T12:00:00Z",
        },
      ],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("params-table")).toBeTruthy();
    });
    expect(screen.getAllByText("nba").length).toBeGreaterThan(0);
    expect(screen.getByText("0.7500")).toBeTruthy();
    expect(screen.getByTestId("apply-button-1")).toBeTruthy();
  });

  it("shows phase9 disabled banner on 503", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: undefined,
      error: new Error("503"),
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("phase9-disabled")).toBeTruthy();
    });
  });

  it("shows generic error on non-phase9 failure", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: undefined,
      error: new Error("network down"),
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });

  it("renders optimization-dashboard wrapper when loaded", async () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("optimization-dashboard")).toBeTruthy();
    });
  });
});
