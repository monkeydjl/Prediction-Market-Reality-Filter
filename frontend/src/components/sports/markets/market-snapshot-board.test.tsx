import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const apiMocks = vi.hoisted(() => ({
  useLatestLinks: vi.fn(),
  useMarketLinksByMatch: vi.fn(),
  useTraditionalOddsLatest: vi.fn(),
  useLinkAudit: vi.fn(),
  useMatches: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useLatestLinks: apiMocks.useLatestLinks,
  useMarketLinksByMatch: apiMocks.useMarketLinksByMatch,
  useTraditionalOddsLatest: apiMocks.useTraditionalOddsLatest,
  useLinkAudit: apiMocks.useLinkAudit,
  useMatches: apiMocks.useMatches,
}));

import { MarketSnapshotBoard } from "./market-snapshot-board";

const LINK = {
  id: 7,
  match_id: "m1",
  contract_id: "c1",
  source: "polymarket",
  outcome_label: "YES",
  mapped_outcome: "home_win",
  link_method: "auto",
  link_confidence: 0.9,
  verified: true,
  market_question: "Will home win?",
  implied_prob: 0.55,
  latest_snapshot: { id: 3, implied_prob: 0.61, price: 0.61, captured_at: "2026-08-01T12:30:00Z" },
};

const MATCH = {
  match_id: "m1",
  sport: "football",
  competition: "epl",
  home_team: "Arsenal",
  away_team: "Chelsea",
  home_code: "ARS",
  away_code: "CHE",
  kickoff_utc: "2026-08-02T18:00:00Z",
  stage: "regular",
  has_prediction: true,
};

describe("MarketSnapshotBoard", () => {
  beforeEach(() => {
    apiMocks.useLatestLinks.mockReturnValue({ data: undefined, error: undefined, isLoading: false });
    apiMocks.useMarketLinksByMatch.mockReturnValue({ data: undefined });
    apiMocks.useTraditionalOddsLatest.mockReturnValue({ data: undefined });
    apiMocks.useLinkAudit.mockReturnValue({ data: undefined, error: undefined, isLoading: true });
    apiMocks.useMatches.mockReturnValue({ data: [MATCH], error: undefined, isLoading: false });
  });

  it("prompts for a match instead of asking for a pasted id", () => {
    render(<MarketSnapshotBoard />);
    expect(screen.getByTestId("snapshot-no-match")).toBeInTheDocument();
    expect(screen.getByTestId("snapshot-match")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Arsenal vs Chelsea/ })).toBeInTheDocument();
  });

  it("leaves realtime polling off until the operator opts in", async () => {
    apiMocks.useLatestLinks.mockReturnValue({ data: { items: [LINK], total: 1 }, error: undefined, isLoading: false });
    render(<MarketSnapshotBoard />);

    await userEvent.selectOptions(screen.getByTestId("snapshot-match"), "m1");
    expect(apiMocks.useLatestLinks).toHaveBeenLastCalledWith("m1", undefined);

    const toggle = screen.getByTestId("realtime-toggle");
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(apiMocks.useLatestLinks).toHaveBeenLastCalledWith("m1", 30_000);
  });

  it("renders the latest snapshot price and expands the audit trail", async () => {
    apiMocks.useLatestLinks.mockReturnValue({ data: { items: [LINK], total: 1 }, error: undefined, isLoading: false });
    apiMocks.useLinkAudit.mockReturnValue({
      data: { link_id: 7, available: true, snapshot_count: 12, first_price: 0.5, last_price: 0.61, delta_pp: 11, flags: ["large_move"] },
      error: undefined,
      isLoading: false,
    });
    render(<MarketSnapshotBoard />);

    const board = screen.getByTestId("market-snapshot-board");
    expect(board).toHaveTextContent("61.0%");
    expect(board).toHaveTextContent("主胜");

    await userEvent.click(screen.getByTestId("audit-toggle-7"));
    const audit = screen.getByTestId("link-audit-7");
    expect(audit).toHaveTextContent("+11.0pp");
    expect(audit).toHaveTextContent("大幅移动");
  });

  it("points at the pending queue when only unverified links exist", async () => {
    apiMocks.useLatestLinks.mockReturnValue({ data: { items: [], total: 0 }, error: undefined, isLoading: false });
    apiMocks.useMarketLinksByMatch.mockReturnValue({
      data: { items: [{ ...LINK, verified: false }], total: 1 },
    });
    render(<MarketSnapshotBoard />);

    await userEvent.selectOptions(screen.getByTestId("snapshot-match"), "m1");
    expect(screen.getByTestId("snapshot-empty")).toHaveTextContent("1 条待核验");
  });

  it("shows traditional odds when the match is not skipped", () => {
    apiMocks.useTraditionalOddsLatest.mockReturnValue({
      data: {
        match_id: "m1",
        skipped: false,
        skip_reason: null,
        outcomes: [
          { mapped_outcome: "draw", implied_prob: 0.24, decimal_odds: 4.1, bookmaker: "pinnacle", bookmakers_count: 3, captured_at: null },
        ],
      },
    });
    render(<MarketSnapshotBoard />);

    const odds = screen.getByTestId("traditional-odds-latest");
    expect(odds).toHaveTextContent("平局");
    expect(odds).toHaveTextContent("24.0%");
    expect(odds).toHaveTextContent("4.10");
  });
});
