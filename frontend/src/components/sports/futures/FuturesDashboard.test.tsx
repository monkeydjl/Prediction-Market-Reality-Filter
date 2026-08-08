import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const apiMocks = vi.hoisted(() => ({
  useAvailableFutures: vi.fn(),
  useLatestSnapshots: vi.fn(),
  useFuturesCoverage: vi.fn(),
  useFuturesSeries: vi.fn(),
  useFuturesLinks: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useAvailableFutures: apiMocks.useAvailableFutures,
  useLatestSnapshots: apiMocks.useLatestSnapshots,
  useFuturesCoverage: apiMocks.useFuturesCoverage,
  useFuturesSeries: apiMocks.useFuturesSeries,
  useFuturesLinks: apiMocks.useFuturesLinks,
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

const seriesRegistry = {
  series: emptyCoverage.series_registry,
  competition_count: 2,
  series_count: 2,
  competitions: ["nba", "nfl"],
};

const NBA_PAIR = {
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
};

const NBA_PAIR_OLD = { ...NBA_PAIR, season: "2023-24", verified_count: 1 };

describe("FuturesDashboard", () => {
  beforeEach(() => {
    apiMocks.useAvailableFutures.mockReset();
    apiMocks.useLatestSnapshots.mockReset();
    apiMocks.useFuturesCoverage.mockReset();
    apiMocks.useFuturesSeries.mockReset();
    apiMocks.useFuturesLinks.mockReset();
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
    apiMocks.useFuturesSeries.mockReturnValue({
      data: seriesRegistry,
      error: undefined,
      isLoading: false,
    });
    apiMocks.useFuturesLinks.mockReturnValue({
      data: undefined,
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
      data: { pairs: [NBA_PAIR] },
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

  it("lets the query param pick the pair over the first-pair default", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: { pairs: [NBA_PAIR, NBA_PAIR_OLD] },
      error: undefined,
      isLoading: false,
    });
    render(<FuturesDashboard competition="nba" season="2023-24" />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /2023-24/ }).getAttribute("aria-pressed"),
      ).toBe("true");
    });
    expect(
      screen.getByRole("button", { name: /2024-25/ }).getAttribute("aria-pressed"),
    ).toBe("false");
    // The snapshot hook is keyed off the deep-linked pair, not the default.
    expect(apiMocks.useLatestSnapshots).toHaveBeenCalledWith("nba", "2023-24");
  });

  it("drills into a series and links its linked seasons", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: { pairs: [NBA_PAIR] },
      error: undefined,
      isLoading: false,
    });
    render(<FuturesDashboard />);

    await userEvent.click(await screen.findByTestId("futures-series-open-KXNBACHAMP"));
    const detail = screen.getByTestId("futures-series-detail");
    expect(detail.textContent).toContain("championship");
    // next/link normalizes the helper's trailing slash away in jsdom; the
    // deep-link contract that matters is the path plus both query keys.
    expect(screen.getByRole("link", { name: /2024-25/ }).getAttribute("href")).toMatch(
      /^\/sports\/futures\/?\?competition=nba&season=2024-25$/,
    );
  });

  it("names a registered series that has no linked season", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: { pairs: [NBA_PAIR] },
      error: undefined,
      isLoading: false,
    });
    render(<FuturesDashboard />);

    await userEvent.click(await screen.findByTestId("futures-series-open-KXNFLCHAMP"));
    expect(screen.getByTestId("futures-series-detail-empty")).toBeTruthy();
  });

  it("shows the contract legs behind the selected pair", async () => {
    apiMocks.useAvailableFutures.mockReturnValue({
      data: { pairs: [NBA_PAIR] },
      error: undefined,
      isLoading: false,
    });
    apiMocks.useFuturesLinks.mockReturnValue({
      data: {
        competition: "nba",
        season: "2024-25",
        links: [
          {
            id: 7,
            competition: "nba",
            season: "2024-25",
            team: "BOS",
            contract_id: "KXNBACHAMP-25-BOS",
            source: "kalshi",
            market_question: "Will the Celtics win the title?",
            implied_prob: 0.31,
            verified: true,
          },
          {
            id: 8,
            competition: "nba",
            season: "2024-25",
            team: "LAL",
            contract_id: "KXNBACHAMP-25-LAL",
            source: "kalshi",
            market_question: null,
            implied_prob: null,
            verified: false,
          },
        ],
      },
      error: undefined,
      isLoading: false,
    });
    render(<FuturesDashboard />);

    expect(await screen.findByTestId("futures-legs")).toBeTruthy();
    expect(screen.getByText("1/2 已核验")).toBeTruthy();
    expect(screen.getByTestId("futures-leg-8").textContent).toContain("待核验");
  });
});
