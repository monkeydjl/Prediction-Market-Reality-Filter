"""Monte Carlo tournament simulation for World Cup.

Simulates the entire tournament (group stage + knockout bracket) thousands
of times to estimate:
- Probability of winning the tournament for each team
- Probability of reaching each round (group exit, R16, QF, SF, Final)
- Most likely final matchups

Uses single-match predictions from the prediction pipeline to determine
match outcomes in each simulation iteration.
"""

import asyncio
import logging
import random
from typing import Any

from app.services.world_cup_elo_odds_engine import predict_match_elo_odds
from app.services.elo_ratings_service import get_elo_rating
from app.services.odds_cache_service import get_cached_odds

logger = logging.getLogger(__name__)

# World Cup 2026 format: 48 teams, 12 groups of 4
# Top 2 from each group + 8 best 3rd-place teams advance to R32 (new format)
# For simplicity, we simulate group stage as a round-robin and use
# top 2 finishers to fill a 32-team bracket


def _simulate_match(
    home_team: str,
    away_team: str,
    elo_cache: dict[str, float],
    odds_cache: dict[str, dict[str, float]] | None = None,
    is_knockout: bool = False,
) -> dict[str, float]:
    """Simulate a single match outcome using the Elo+Odds engine.

    Returns outcome probabilities (home_win, draw, away_win).
    For knockout matches, draw probability is redistributed (extra time).
    """
    elo_home = elo_cache.get(home_team, 1500.0)
    elo_away = elo_cache.get(away_team, 1500.0)

    odds = None
    if odds_cache:
        key = f"{home_team}_vs_{away_team}"
        odds = odds_cache.get(key)

    prediction = predict_match_elo_odds(
        home_team=home_team,
        away_team=away_team,
        elo_home=elo_home,
        elo_away=elo_away,
        odds_home=odds["home"] if odds else None,
        odds_draw=odds["draw"] if odds else None,
        odds_away=odds["away"] if odds else None,
        is_knockout=is_knockout,
    )

    probs = prediction["outcome_probabilities"]

    if is_knockout:
        # Redistribute draw probability for knockout (extra time / penalties)
        draw_prob = probs["draw"]
        # Split draw: 50% to each team (simplified penalty shootout model)
        probs = {
            "home_win": probs["home_win"] + draw_prob * 0.5,
            "draw": 0.0,
            "away_win": probs["away_win"] + draw_prob * 0.5,
        }

    return probs


def _sample_outcome(probs: dict[str, float]) -> str:
    """Sample a match outcome from probability distribution."""
    r = random.random()
    if r < probs["home_win"]:
        return "home_win"
    elif r < probs["home_win"] + probs["draw"]:
        return "draw"
    else:
        return "away_win"


def _simulate_group(
    group_teams: list[str],
    elo_cache: dict[str, float],
    odds_cache: dict[str, dict[str, float]] | None = None,
) -> list[tuple[str, int, int]]:
    """Simulate a single group stage.

    Returns list of (team, points, goal_diff) sorted by standings.
    """
    standings: dict[str, dict[str, int]] = {
        team: {"points": 0, "goals_for": 0, "goals_against": 0}
        for team in group_teams
    }

    # Round robin: each team plays every other team
    for i in range(len(group_teams)):
        for j in range(i + 1, len(group_teams)):
            home = group_teams[i]
            away = group_teams[j]

            probs = _simulate_match(home, away, elo_cache, odds_cache, is_knockout=False)
            outcome = _sample_outcome(probs)

            # Estimate goals from Poisson (simplified)
            pred = predict_match_elo_odds(
                home_team=home,
                away_team=away,
                elo_home=elo_cache.get(home, 1500.0),
                elo_away=elo_cache.get(away, 1500.0),
            )
            home_goals = round(pred["predicted_score"]["home"])
            away_goals = round(pred["predicted_score"]["away"])

            # Adjust based on outcome
            if outcome == "home_win" and home_goals <= away_goals:
                home_goals = away_goals + 1
            elif outcome == "away_win" and away_goals <= home_goals:
                away_goals = home_goals + 1
            elif outcome == "draw":
                away_goals = home_goals

            standings[home]["goals_for"] += home_goals
            standings[home]["goals_against"] += away_goals
            standings[away]["goals_for"] += away_goals
            standings[away]["goals_against"] += home_goals

            if outcome == "home_win":
                standings[home]["points"] += 3
            elif outcome == "draw":
                standings[home]["points"] += 1
                standings[away]["points"] += 1
            else:
                standings[away]["points"] += 3

    # Sort by points, then goal difference
    result = [
        (team, data["points"], data["goals_for"] - data["goals_against"])
        for team, data in standings.items()
    ]
    result.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return result


def _simulate_knockout_bracket(
    bracket: list[str],
    elo_cache: dict[str, float],
    odds_cache: dict[str, dict[str, float]] | None = None,
) -> str:
    """Simulate a single-elimination bracket. Returns winner."""
    current_round = list(bracket)

    while len(current_round) > 1:
        next_round = []
        for i in range(0, len(current_round), 2):
            if i + 1 >= len(current_round):
                next_round.append(current_round[i])
                break

            home = current_round[i]
            away = current_round[i + 1]

            probs = _simulate_match(home, away, elo_cache, odds_cache, is_knockout=True)
            outcome = _sample_outcome(probs)

            if outcome == "home_win":
                next_round.append(home)
            else:
                next_round.append(away)

        current_round = next_round

    return current_round[0]


def simulate_tournament(
    groups: dict[str, list[str]],
    elo_cache: dict[str, float] | None = None,
    odds_cache: dict[str, dict[str, float]] | None = None,
    num_simulations: int = 10000,
) -> dict[str, Any]:
    """Run Monte Carlo tournament simulation.

    Args:
        groups: Dict of group_name -> list of 4 team names
        elo_cache: Pre-fetched Elo ratings (team_name -> rating)
        odds_cache: Pre-fetched odds (f"{home}_vs_{away}" -> {home, draw, away})
        num_simulations: Number of simulations to run

    Returns:
        {
            "win_probability": {team: prob},
            "reach_final": {team: prob},
            "reach_semifinal": {team: prob},
            "most_likely_winner": str,
            "simulations": int,
        }
    """
    if elo_cache is None:
        # Fetch Elo ratings for all teams
        elo_cache = {}
        all_teams = set()
        for teams in groups.values():
            all_teams.update(teams)

        async def _fetch_all_elo_ratings(teams: set[str]) -> dict[str, dict[str, Any]]:
            """Batch-fetch Elo ratings for all teams."""
            results = {}
            for team in teams:
                results[team] = await get_elo_rating(team)
            return results

        try:
            elo_data_map = asyncio.run(_fetch_all_elo_ratings(all_teams))
            for team, data in elo_data_map.items():
                elo_cache[team] = data.get("elo_rating", 1500.0)
        except RuntimeError:
            # Event loop already running (called from async context)
            logger.warning(
                "Cannot fetch Elo ratings synchronously (event loop running). "
                "Using default 1500.0 for all teams."
            )
            for team in all_teams:
                elo_cache[team] = 1500.0

    # Initialize counters
    all_teams = list(elo_cache.keys())
    win_count = {team: 0 for team in all_teams}
    final_count = {team: 0 for team in all_teams}
    semifinal_count = {team: 0 for team in all_teams}

    for sim in range(num_simulations):
        # Simulate group stage
        qualified = []
        for group_name, teams in groups.items():
            results = _simulate_group(teams, elo_cache, odds_cache)
            # Top 2 advance
            qualified.append(results[0][0])  # Group winner
            qualified.append(results[1][0])  # Runner-up

        # Shuffle to create bracket (in reality, seeding determines bracket)
        random.shuffle(qualified)

        # Simulate knockout rounds
        # Round of 32 -> R16 -> QF -> SF -> Final
        bracket = qualified

        # Track semifinalists (last 4)
        current = list(bracket)
        while len(current) > 4:
            next_round = []
            for i in range(0, len(current), 2):
                if i + 1 >= len(current):
                    next_round.append(current[i])
                    break
                home = current[i]
                away = current[i + 1]
                probs = _simulate_match(home, away, elo_cache, odds_cache, is_knockout=True)
                outcome = _sample_outcome(probs)
                next_round.append(home if outcome == "home_win" else away)
            current = next_round

        # Semifinalists
        for team in current:
            semifinal_count[team] += 1

        # Simulate semifinals
        sf1_winner = _simulate_knockout_bracket([current[0], current[1]], elo_cache, odds_cache)
        sf2_winner = _simulate_knockout_bracket([current[2], current[3]], elo_cache, odds_cache)

        final_count[sf1_winner] += 1
        final_count[sf2_winner] += 1

        # Final
        winner = _simulate_knockout_bracket([sf1_winner, sf2_winner], elo_cache, odds_cache)
        win_count[winner] += 1

    # Compute probabilities
    win_probability = {
        team: round(count / num_simulations, 4)
        for team, count in sorted(win_count.items(), key=lambda x: x[1], reverse=True)
    }
    reach_final = {
        team: round(count / num_simulations, 4)
        for team, count in final_count.items()
    }
    reach_semifinal = {
        team: round(count / num_simulations, 4)
        for team, count in semifinal_count.items()
    }

    most_likely_winner = max(win_count, key=win_count.get)

    return {
        "win_probability": win_probability,
        "reach_final": reach_final,
        "reach_semifinal": reach_semifinal,
        "most_likely_winner": most_likely_winner,
        "most_likely_winner_prob": win_probability.get(most_likely_winner, 0),
        "simulations": num_simulations,
    }
