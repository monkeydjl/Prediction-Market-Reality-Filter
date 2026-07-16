import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useCalibrations: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useCalibrations: apiMocks.useCalibrations,
}));

import { MarketCalibrationPanel } from "./MarketCalibrationPanel";

const calData = {
  items: [
    {
      id: 1, engine: "BasketballEngine", competition: "nba",
      slope: 0.95, intercept: 0.02, sample_count: 15,
      avg_brier: 0.034, avg_signed_error: -0.01, direction_accuracy: 0.73,
      last_updated: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

describe("MarketCalibrationPanel", () => {
  it("renders cards after load", async () => {
    apiMocks.useCalibrations.mockReturnValue({
      data: calData,
      error: undefined,
      isLoading: false,
    });
    render(<MarketCalibrationPanel />);
    await waitFor(() =>
      expect(screen.getByTestId("calibration-panel")).toBeInTheDocument(),
    );
    expect(screen.getByText("BasketballEngine")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.useCalibrations.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.useCalibrations.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("shows calibration metrics", async () => {
    apiMocks.useCalibrations.mockReturnValue({
      data: calData,
      error: undefined,
      isLoading: false,
    });
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("cal-card-1")).toBeInTheDocument());
    const card = screen.getByTestId("cal-card-1");
    expect(card.textContent).toContain("0.950");  // slope
    expect(card.textContent).toContain("15");  // sample_count
  });
});
