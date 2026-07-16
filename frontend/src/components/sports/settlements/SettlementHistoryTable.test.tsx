import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SettlementHistoryTable } from "./SettlementHistoryTable";
import type { SettlementList } from "@/lib/sport-settlements-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchSettlementHistory: vi.fn(),
}));
vi.mock("@/lib/sport-settlements-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-settlements-api")>()),
  fetchSettlementHistory: apiMocks.fetchSettlementHistory,
}));

const historyData: SettlementList = {
  items: [
    {
      id: 1, match_id: "m1", mapped_outcome: "home_win", engine: "BasketballEngine",
      competition: "nba", settlement_implied_prob: 0.9, settlement_captured_at: "2026-01-01T00:00:00Z",
      link_id: 1, model_prob: 0.65, market_prob_at_detection: 0.6, raw_edge: 0.05,
      adjusted_edge: 0.04, brier_score: 0.0625, signed_error: -0.25, direction_correct: 1,
      status: "processed", skip_reason: null, match_finished_at: "2026-01-01T00:00:00Z",
      processed_at: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

describe("SettlementHistoryTable", () => {
  it("renders rows after load", async () => {
    apiMocks.fetchSettlementHistory.mockResolvedValue(historyData);
    render(<SettlementHistoryTable />);
    await waitFor(() =>
      expect(screen.getByTestId("settlements-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("m1")).toBeInTheDocument();
    expect(screen.getByText("BasketballEngine")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchSettlementHistory.mockResolvedValue({ items: [], total: 0 });
    render(<SettlementHistoryTable />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchSettlementHistory.mockRejectedValue(new Error("boom"));
    render(<SettlementHistoryTable />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("renders direction correct checkmark", async () => {
    apiMocks.fetchSettlementHistory.mockResolvedValue(historyData);
    render(<SettlementHistoryTable />);
    await waitFor(() => expect(screen.getByTestId("dir-1")).toBeInTheDocument());
    expect(screen.getByTestId("dir-1").textContent).toBe("✓");
  });
});
