/**
 * Calculate group standings from match results
 */

import type { MatchFixture } from "./predictions-api";

export interface TeamStanding {
  team: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goalsFor: number;
  goalsAgainst: number;
  goalDifference: number;
  points: number;
}

export interface GroupStanding {
  group: string;
  teams: TeamStanding[];
}

/**
 * Calculate standings for all groups based on finished matches
 */
export function calculateGroupStandings(matches: MatchFixture[]): GroupStanding[] {
  // Filter only GROUP_STAGE matches
  const groupMatches = matches.filter(
    (m) => m.stage === "GROUP_STAGE" && m.group
  );

  // Group by group letter
  const byGroup = groupMatches.reduce((acc, match) => {
    const group = match.group!;
    if (!acc[group]) acc[group] = [];
    acc[group].push(match);
    return acc;
  }, {} as Record<string, MatchFixture[]>);

  // Calculate standings for each group
  const standings: GroupStanding[] = [];

  for (const [group, groupMatches] of Object.entries(byGroup)) {
    const teamStats: Record<string, TeamStanding> = {};

    // Initialize all teams in this group
    groupMatches.forEach((match) => {
      if (!teamStats[match.home_team]) {
        teamStats[match.home_team] = {
          team: match.home_team,
          played: 0,
          won: 0,
          drawn: 0,
          lost: 0,
          goalsFor: 0,
          goalsAgainst: 0,
          goalDifference: 0,
          points: 0,
        };
      }
      if (!teamStats[match.away_team]) {
        teamStats[match.away_team] = {
          team: match.away_team,
          played: 0,
          won: 0,
          drawn: 0,
          lost: 0,
          goalsFor: 0,
          goalsAgainst: 0,
          goalDifference: 0,
          points: 0,
        };
      }
    });

    // Process finished matches
    groupMatches
      .filter((m) => m.status === "finished" && m.home_score != null && m.away_score != null)
      .forEach((match) => {
        const homeTeam = teamStats[match.home_team];
        const awayTeam = teamStats[match.away_team];

        homeTeam.played += 1;
        awayTeam.played += 1;

        homeTeam.goalsFor += match.home_score!;
        homeTeam.goalsAgainst += match.away_score!;
        awayTeam.goalsFor += match.away_score!;
        awayTeam.goalsAgainst += match.home_score!;

        if (match.home_score! > match.away_score!) {
          // Home win
          homeTeam.won += 1;
          homeTeam.points += 3;
          awayTeam.lost += 1;
        } else if (match.home_score! < match.away_score!) {
          // Away win
          awayTeam.won += 1;
          awayTeam.points += 3;
          homeTeam.lost += 1;
        } else {
          // Draw
          homeTeam.drawn += 1;
          homeTeam.points += 1;
          awayTeam.drawn += 1;
          awayTeam.points += 1;
        }

        homeTeam.goalDifference = homeTeam.goalsFor - homeTeam.goalsAgainst;
        awayTeam.goalDifference = awayTeam.goalsFor - awayTeam.goalsAgainst;
      });

    // Sort teams by points, then goal difference, then goals scored
    const sortedTeams = Object.values(teamStats).sort((a, b) => {
      if (a.points !== b.points) return b.points - a.points;
      if (a.goalDifference !== b.goalDifference) return b.goalDifference - a.goalDifference;
      if (a.goalsFor !== b.goalsFor) return b.goalsFor - a.goalsFor;
      return a.team.localeCompare(b.team);
    });

    standings.push({
      group,
      teams: sortedTeams,
    });
  }

  // Sort by group name
  standings.sort((a, b) => a.group.localeCompare(b.group));

  return standings;
}
