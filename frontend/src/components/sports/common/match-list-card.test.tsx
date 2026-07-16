import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MatchListCard } from "./match-list-card";
import type { MatchSummary } from "@/lib/sports-api";

vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

const mockMatch: MatchSummary = {
  match_id: "nba-12345",
  sport: "basketball",
  competition: "nba",
  home_team: "Los Angeles Lakers",
  away_team: "Boston Celtics",
  home_code: "LAL",
  away_code: "BOS",
  kickoff_utc: "2026-07-15T02:00:00Z",
  stage: "regular_season",
  has_prediction: false,
};

describe("MatchListCard", () => {
  it("renders team names", () => {
    render(<MatchListCard match={mockMatch} />);
    expect(screen.getByText("Los Angeles Lakers")).toBeDefined();
    expect(screen.getByText("Boston Celtics")).toBeDefined();
  });

  it("renders kickoff local time", () => {
    render(<MatchListCard match={mockMatch} />);
    // The exact local time depends on timezone, but it should render a time string
    expect(screen.getByText(/\d/)).toBeDefined();
  });

  it("renders sport icon for basketball", () => {
    render(<MatchListCard match={mockMatch} />);
    expect(screen.getByText("🏀")).toBeDefined();
  });

  it("renders predicted badge when has_prediction is true", () => {
    render(<MatchListCard match={{ ...mockMatch, has_prediction: true }} />);
    expect(screen.getByText("已预测")).toBeDefined();
  });

  it("renders not predicted badge when has_prediction is false", () => {
    render(<MatchListCard match={mockMatch} />);
    expect(screen.getByText("未预测")).toBeDefined();
  });

  it("card is a link to detail page", () => {
    render(<MatchListCard match={mockMatch} />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/sports/nba-12345/");
  });
});
