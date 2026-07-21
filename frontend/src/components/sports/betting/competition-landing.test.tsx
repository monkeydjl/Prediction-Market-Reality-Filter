import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { CompetitionLanding } from "./competition-landing";
import type { BettingCompetition } from "@/lib/betting/competition-catalog";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useBettingCatalog = vi.fn();
const useMatches = vi.fn();

vi.mock("@/lib/sports-api", () => ({
  useBettingCatalog: () => useBettingCatalog(),
  useMatches: (...args: unknown[]) => useMatches(...args),
}));

const epl: BettingCompetition = {
  id: "epl",
  sport: "football",
  label: "英超",
  shortLabel: "英超",
  description: "EPL",
  status: "kernel",
  href: "/sports?sport=football&competition=epl",
  kernelSport: "football",
  competitionCode: "epl",
  track: "kernel",
  section: "football",
};

const esports: BettingCompetition = {
  id: "esports",
  sport: "esports",
  label: "电竞",
  shortLabel: "电竞",
  description: "placeholder",
  status: "coming_soon",
  href: "/sports/betting/esports",
  track: "placeholder",
  section: "esports",
};

describe("CompetitionLanding", () => {
  beforeEach(() => {
    useBettingCatalog.mockReturnValue({
      data: {
        version: 1,
        competitions: [{ id: "epl", adapter_likely: true }],
        tools: [],
        sections: {},
        flags: { kernel_prediction_enabled: true, epl_data_enabled: true },
      },
      error: undefined,
      isLoading: false,
    });
    useMatches.mockReturnValue({
      data: [{ match_id: "epl-1" }, { match_id: "epl-2" }],
      error: undefined,
      isLoading: false,
    });
  });

  it("shows adapter badge overlay and match count for kernel league", () => {
    render(<CompetitionLanding competition={epl} />);
    expect(screen.getByTestId("landing-adapter-status")).toHaveAttribute(
      "data-adapter-likely",
      "true",
    );
    expect(screen.getByTestId("landing-match-count-n")).toHaveTextContent("2");
    expect(useMatches).toHaveBeenCalledWith({
      sport: "football",
      competition: "epl",
    });
  });

  it("does not poll matches for coming_soon esports", () => {
    useBettingCatalog.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
    });
    useMatches.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
    });
    render(<CompetitionLanding competition={esports} />);
    expect(useMatches).toHaveBeenCalledWith(null);
    expect(screen.getByRole("status")).toHaveTextContent(/不会展示占位赔率/);
  });
});
