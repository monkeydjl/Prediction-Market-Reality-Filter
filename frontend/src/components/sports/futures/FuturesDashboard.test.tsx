import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useAvailableFutures: vi.fn(),
  useLatestSnapshots: vi.fn(),
  useFuturesCoverage: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useAvailableFutures: apiMocks.useAvailableFutures,
  useLatestSnapshots: apiMocks.useLatestSnapshots,
  useFuturesCoverage: apiMocks.useFuturesCoverage,
}));

import { FuturesDashboard } from "./FuturesDashboard";

const emptyCoverage = {
  series_registry: [
    {
      series_prefix: "KXNBACHAMP",
      competition: "nba",
      championship_type: "championship",
    },
    {
      series_prefix: "KXNFLCHAMP",
      competition: "nfl",
      championship_type: "super_bowl",
    },
  ],
  pairs: [],
  pair_count: 0,
  status_counts: {},
  registered_competitions: ["nba", "nfl"],
  linked_competitions: [],
  missing_linked_competitions: ["nba", "nfl"],
};

describe("FuturesDashboard", () => {
  beforeEach(() => {
    apiMocks.useAvailableFutures.mockReset();
    apiMocks.useLatestSnapshots.mockReset();
    apiMocks.useFuturesCoverage.mockReset();
    apiMocks.useLatestSnapshots.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
    apiMocks.useFuturesCoverage.mockReturnValue({
      data: emptyCoverage,
      error: undefined,
      isLoading: false,
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
    expect(screen.getByTestId("futures-coverage")).toBeTruthy();
    expect(screen.getByTestId("series-row-KXNBACHAMP")).toBeTruthy();
  });

  it("renders snapshots table when a pair is selected and data is available", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: {
        pairs: [
          {
            competition: "nba",
            season: "2024-25",
            verified_count: 2,
            integrity: {
              status: "ok",
              leg_count: 2,
              unique_team_count: 2,
              teams: ["BOS", "LAL"],
              duplicate_teams: [],
              missing_price_count: 0,
              sum_implied_prob: 1.05,
              issues: [],
            },
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useLatestSnapshots.mockReturnValue({
      data: {
        competition: "nba",
        season: "2024-25",
        integrity: {
          status: "ok",
          leg_count: 1,
          unique_team_count: 1,
          teams: ["LAL"],
          duplicate_teams: [],
          missing_price_count: 0,
          sum_implied_prob: 0.22,
          issues: ["underround_or_incomplete"],
        },
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
    expect(screen.getByText("0.2200")).toBeTruthy();
    expect(screen.getByTestId("selected-pair-integrity")).toBeTruthy();
  });
});
