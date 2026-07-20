import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const apiMocks = vi.hoisted(() => ({
  useOptimizationParams: vi.fn(),
}));

vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useOptimizationParams: apiMocks.useOptimizationParams,
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

import { AppliedWeightsPanel } from "./applied-weights-panel";

describe("AppliedWeightsPanel", () => {
  beforeEach(() => {
    apiMocks.useOptimizationParams.mockReset();
  });

  it("shows loading", () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
      mutate: vi.fn(),
    });
    render(<AppliedWeightsPanel />);
    expect(screen.getByTestId("applied-weights-loading")).toBeInTheDocument();
  });

  it("shows empty when no applied", () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [
        {
          id: 1,
          sport: "nba",
          competition: "nba",
          factor_weights: '{"elo":0.5}',
          elo_params: "{}",
          score: 0.7,
          accuracy: 0.6,
          brier_score: 0.2,
          mae: 0.3,
          sample_count: 10,
          trial_number: 1,
          status: "candidate",
          created_at: null,
          applied_at: null,
        },
      ],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });
    render(<AppliedWeightsPanel />);
    expect(screen.getByTestId("applied-weights-empty")).toBeInTheDocument();
  });

  it("renders applied weight rows", () => {
    apiMocks.useOptimizationParams.mockReturnValue({
      data: [
        {
          id: 9,
          sport: "nba",
          competition: "nba",
          factor_weights: '{"elo":0.45,"form":0.25}',
          elo_params: '{"hfa":100}',
          score: 0.72,
          accuracy: 0.68,
          brier_score: 0.21,
          mae: 0.32,
          sample_count: 100,
          trial_number: 5,
          status: "applied",
          created_at: null,
          applied_at: "2026-07-18T00:00:00+00:00",
        },
      ],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });
    render(<AppliedWeightsPanel />);
    expect(screen.getByTestId("applied-weights-panel")).toBeInTheDocument();
    expect(screen.getByTestId("applied-weights-9")).toBeInTheDocument();
    expect(screen.getByTestId("weight-row-elo")).toBeInTheDocument();
    expect(screen.getByTestId("applied-elo-9")).toBeInTheDocument();
  });
});
