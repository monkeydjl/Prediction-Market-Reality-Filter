import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const apiMocks = vi.hoisted(() => ({ useMatches: vi.fn() }));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useMatches: apiMocks.useMatches,
}));

vi.mock("./edgedetailpanel", () => ({
  EdgeDetailPanel: ({ matchId }: { matchId: string }) => (
    <div data-testid="edge-detail-panel">{matchId}</div>
  ),
}));
vi.mock("./edgetimelinechart", () => ({
  EdgeTimelineChart: ({ matchId, mappedOutcome }: { matchId: string; mappedOutcome?: string }) => (
    <div data-testid="edge-timeline-chart" data-outcome={mappedOutcome ?? ""}>
      {matchId}
    </div>
  ),
}));

import { EdgeHistoryExplorer } from "./edge-history-explorer";

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

describe("EdgeHistoryExplorer", () => {
  beforeEach(() => {
    apiMocks.useMatches.mockReturnValue({ data: [MATCH], error: undefined, isLoading: false });
  });

  it("waits for a match selection before loading history", () => {
    render(<EdgeHistoryExplorer />);
    expect(screen.getByTestId("edge-history-no-match")).toBeInTheDocument();
    expect(screen.queryByTestId("edge-detail-panel")).not.toBeInTheDocument();
  });

  it("renders detail and timeline once a match is picked", async () => {
    render(<EdgeHistoryExplorer />);
    await userEvent.selectOptions(screen.getByTestId("edge-history-match"), "m1");

    expect(screen.getByTestId("edge-detail-panel")).toHaveTextContent("m1");
    expect(screen.getByTestId("edge-timeline-chart")).toHaveAttribute("data-outcome", "");
    expect(screen.getByRole("link", { name: /打开比赛详情/ })).toHaveAttribute(
      "href",
      expect.stringContaining("m1"),
    );
  });

  it("passes the outcome filter down to the timeline", async () => {
    render(<EdgeHistoryExplorer />);
    await userEvent.selectOptions(screen.getByTestId("edge-history-match"), "m1");
    await userEvent.selectOptions(screen.getByTestId("edge-history-outcome"), "draw");

    expect(screen.getByTestId("edge-timeline-chart")).toHaveAttribute("data-outcome", "draw");
  });
});
