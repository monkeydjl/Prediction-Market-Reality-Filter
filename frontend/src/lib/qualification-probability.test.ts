import { describe, expect, it } from "vitest";
import { calculateQualificationProbabilities } from "./qualification-probability";
import type { TeamStanding } from "./group-standings";
import type { MatchFixture } from "./world-cup-predictions";

function standing(team: string, points: number, played = 3): TeamStanding {
  return {
    team,
    played,
    won: 0,
    drawn: 0,
    lost: 0,
    goalsFor: 0,
    goalsAgainst: 0,
    goalDifference: 0,
    points,
  };
}

function scheduledMatch(group: string, home: string, away: string): MatchFixture {
  return {
    match_id: `${home}-${away}`,
    fixture_id: 1,
    home_team: home,
    away_team: away,
    kickoff_utc: "2026-06-20T00:00:00Z",
    venue: "Test Stadium",
    stage: "GROUP_STAGE",
    group,
    status: "scheduled",
  };
}

describe("calculateQualificationProbabilities", () => {
  it("marks the top two as qualified and the rest eliminated when a group has no remaining matches", () => {
    const probabilities = calculateQualificationProbabilities([], [
      {
        group: "A",
        teams: [
          standing("Brazil", 7),
          standing("Switzerland", 6),
          standing("Cameroon", 4),
          standing("Serbia", 1),
        ],
      },
    ]);

    expect(probabilities.map((p) => [p.team, p.qualificationStatus, p.qualificationProbability])).toEqual([
      ["Brazil", "qualified", 1],
      ["Switzerland", "qualified", 1],
      ["Cameroon", "eliminated", 0],
      ["Serbia", "eliminated", 0],
    ]);
  });

  it("keeps teams pending while their group still has scheduled matches", () => {
    const probabilities = calculateQualificationProbabilities(
      [scheduledMatch("B", "Team A", "Team C")],
      [
        {
          group: "B",
          teams: [
            standing("Team A", 4, 2),
            standing("Team B", 4, 2),
            standing("Team C", 3, 2),
            standing("Team D", 0, 2),
          ],
        },
      ]
    );

    expect(probabilities.every((p) => p.qualificationStatus === "pending")).toBe(true);
    expect(probabilities[0].qualificationProbability).toBeGreaterThan(0);
    expect(probabilities[0].qualificationProbability).toBeLessThan(1);
  });
});
