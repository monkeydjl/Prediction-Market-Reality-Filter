import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/sport-odds-api", () => ({
  fetchTraditionalOddsHistory: vi.fn(),
}));

import { TraditionalOddsChart } from "./TraditionalOddsChart";
import { fetchTraditionalOddsHistory } from "@/lib/sport-odds-api";

describe("TraditionalOddsChart", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(fetchTraditionalOddsHistory).mockReturnValue(new Promise(() => {}));
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when no data", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "m1",
      series: [],
      skipped: true,
      skip_reason: "no_odds",
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders chart with odds data", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "nba-2026-g1",
      series: [
        {
          mapped_outcome: "home_win",
          snapshots: [
            { implied_prob: 0.60, decimal_odds: 1.667, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T10:00:00Z" },
            { implied_prob: 0.65, decimal_odds: 1.538, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T11:00:00Z" },
          ],
        },
      ],
      skipped: false,
      skip_reason: null,
    });
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.getByText("home_win")).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockRejectedValue(new Error("404"));
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });
});
