import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarketCalibrationPanel } from "./MarketCalibrationPanel";
import type { CalibrationList } from "@/lib/sport-settlements-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchCalibrations: vi.fn(),
}));
vi.mock("@/lib/sport-settlements-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-settlements-api")>()),
  fetchCalibrations: apiMocks.fetchCalibrations,
}));

const calData: CalibrationList = {
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
    apiMocks.fetchCalibrations.mockResolvedValue(calData);
    render(<MarketCalibrationPanel />);
    await waitFor(() =>
      expect(screen.getByTestId("calibration-panel")).toBeInTheDocument(),
    );
    expect(screen.getByText("BasketballEngine")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchCalibrations.mockResolvedValue({ items: [], total: 0 });
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchCalibrations.mockRejectedValue(new Error("boom"));
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("shows calibration metrics", async () => {
    apiMocks.fetchCalibrations.mockResolvedValue(calData);
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("cal-card-1")).toBeInTheDocument());
    const card = screen.getByTestId("cal-card-1");
    expect(card.textContent).toContain("0.950");  // slope
    expect(card.textContent).toContain("15");  // sample_count
  });
});
