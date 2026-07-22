import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MatchListCard } from "./match-list-card";
import type { MatchSummary } from "@/lib/sports-api";

const base: MatchSummary = {
  match_id: "epl-1",
  sport: "football",
  competition: "epl",
  home_team: "Arsenal",
  away_team: "Chelsea",
  home_code: "ARS",
  away_code: "CHE",
  kickoff_utc: "2026-07-22T15:00:00Z",
  stage: "league",
  has_prediction: false,
};

describe("MatchListCard", () => {
  it("links competition badge to betting landing for known codes", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      assign,
    });
    render(<MatchListCard match={base} />);
    const badge = screen.getByTestId("match-competition-badge");
    expect(badge).toHaveAttribute("data-competition-id", "epl");
    expect(badge).toHaveTextContent("英超");
    await userEvent.click(badge);
    expect(assign).toHaveBeenCalledWith("/sports/betting/epl");
    vi.unstubAllGlobals();
  });

  it("shows raw competition when not in catalog", () => {
    render(
      <MatchListCard
        match={{ ...base, competition: "obscure_league_xyz" }}
      />,
    );
    expect(screen.getByTestId("match-competition-badge")).toHaveTextContent(
      "obscure_league_xyz",
    );
  });
});
