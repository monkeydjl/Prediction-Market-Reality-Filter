/**
 * Calculate qualification probabilities for World Cup teams
 */

import type { MatchFixture } from "./world-cup-predictions";
import type { TeamStanding } from "./group-standings";

export type QualificationStatus = "qualified" | "eliminated" | "pending";

export interface QualificationProbability {
  team: string;
  group: string;
  currentPoints: number;
  currentPosition: number;
  gamesPlayed: number;
  gamesRemaining: number;
  qualificationProbability: number;
  projectedPoints: number;
  qualificationStatus: QualificationStatus;
}

/**
 * Calculate qualification probabilities based on current standings and predictions
 */
export function calculateQualificationProbabilities(
  matches: MatchFixture[],
  currentStandings: Array<{ group: string; teams: TeamStanding[] }>
): QualificationProbability[] {
  const probabilities: QualificationProbability[] = [];

  // Process each group
  for (const groupStanding of currentStandings) {
    const group = groupStanding.group;

    // Get remaining matches for this group
    const remainingMatches = matches.filter(
      (m) =>
        m.stage === "GROUP_STAGE" &&
        m.group === group &&
        m.status === "scheduled" &&
        m.home_score == null &&
        m.away_score == null
    );

    const groupComplete = remainingMatches.length === 0;

    // Calculate probabilities for each team
    for (let i = 0; i < groupStanding.teams.length; i++) {
      const team = groupStanding.teams[i];

      // Count games remaining for this team
      const teamRemainingMatches = remainingMatches.filter(
        (m) => m.home_team === team.team || m.away_team === team.team
      );

      const qualificationStatus = groupComplete
        ? i < 2 ? "qualified" : "eliminated"
        : "pending";

      // Simple heuristic: probability based on current position and games remaining
      let qualProb = 0;

      if (groupComplete) {
        qualProb = i < 2 ? 1 : 0;
      } else if (i === 0) {
        // 1st place
        qualProb = 0.95 - (teamRemainingMatches.length * 0.05);
      } else if (i === 1) {
        // 2nd place
        qualProb = 0.80 - (teamRemainingMatches.length * 0.08);
      } else if (i === 2) {
        // 3rd place
        const pointsGap = groupStanding.teams[1].points - team.points;
        qualProb = Math.max(0.05, 0.40 - pointsGap * 0.10);
      } else {
        // 4th place
        const pointsGap = groupStanding.teams[1].points - team.points;
        qualProb = Math.max(0.01, 0.15 - pointsGap * 0.10);
      }

      // Adjust for games played vs remaining
      if (!groupComplete && team.played === 0) {
        // No games played yet - more uncertainty
        qualProb = i < 2 ? 0.50 : 0.25;
      }

      // Cap between 0 and 1
      qualProb = Math.max(0, Math.min(1, qualProb));

      // Projected points (simple: current + 1.5 per remaining game for top 2)
      const projectedPoints = team.points + (i < 2 ? teamRemainingMatches.length * 1.5 : teamRemainingMatches.length * 1.0);

      probabilities.push({
        team: team.team,
        group,
        currentPoints: team.points,
        currentPosition: i + 1,
        gamesPlayed: team.played,
        gamesRemaining: teamRemainingMatches.length,
        qualificationProbability: qualProb,
        projectedPoints: Math.round(projectedPoints * 10) / 10,
        qualificationStatus,
      });
    }
  }

  return probabilities;
}
