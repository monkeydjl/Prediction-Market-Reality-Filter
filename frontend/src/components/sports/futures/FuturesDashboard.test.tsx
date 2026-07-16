import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/futures-api", () => ({
  fetchAvailableFutures: vi.fn(),
  fetchLatestSnapshots: vi.fn(),
}));

import { FuturesDashboard } from "./FuturesDashboard";
import { fetchAvailableFutures, fetchLatestSnapshots } from "@/lib/futures-api";

describe("FuturesDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows empty state when no futures pairs available", async () => {
    vi.mocked(fetchAvailableFutures).mockResolvedValue({ pairs: [] });
    render(<FuturesDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders snapshots table when a pair is selected and data is available", async () => {
    vi.mocked(fetchAvailableFutures).mockResolvedValue({
      pairs: [{ competition: "nba", season: "2024-25" }],
    });
    vi.mocked(fetchLatestSnapshots).mockResolvedValue({
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
