import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useAvailableFutures: vi.fn(),
  useLatestSnapshots: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useAvailableFutures: apiMocks.useAvailableFutures,
  useLatestSnapshots: apiMocks.useLatestSnapshots,
}));

import { FuturesDashboard } from "./FuturesDashboard";

describe("FuturesDashboard", () => {
  beforeEach(() => {
    apiMocks.useAvailableFutures.mockReset();
    apiMocks.useLatestSnapshots.mockReset();
    // Default safe fallback for snapshots hook (not yet selected).
    apiMocks.useLatestSnapshots.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
  });

  it("shows empty state when no futures pairs available", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: { pairs: [] },
      error: undefined,
      isLoading: false,
    });
    render(<FuturesDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders snapshots table when a pair is selected and data is available", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: { pairs: [{ competition: "nba", season: "2024-25" }] },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useLatestSnapshots.mockReturnValue({
      data: {
        competition: "nba",
        season: "2024-25",
        snapshots: [
          {
            id: 100,
            link_id: 1,
            team: "LAL",
            implied_prob: 0.22,
            price: 0.18,
            liquidity: 51000,
            volume: 12100,
            captured_at: "2026-07-16T11:00:00Z",
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<FuturesDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("snapshots-table")).toBeTruthy();
    });
    expect(screen.getByText("LAL")).toBeTruthy();
    // 0.22 renders via toFixed(4) as "0.2200"
    expect(screen.getByText("0.2200")).toBeTruthy();
  });
});
