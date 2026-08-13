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
import time
from typing import Any

from app.services.world_cup_engines import get_engine
from app.services.elo_ratings_service import get_elo_rating

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
    prediction_cache: dict[tuple[str, str, bool], dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Simulate a single match outcome using the Elo+Odds engine.

    Returns outcome probabilities (home_win, draw, away_win).
    For knockout matches, draw probability is redistributed (extra time).
    """
    cache_key = (home_team, away_team, is_knockout)
    if prediction_cache is not None and cache_key in prediction_cache:
        return dict(prediction_cache[cache_key]["outcome_probabilities"])

    elo_home = elo_cache.get(home_team, 1500.0)
    elo_away = elo_cache.get(away_team, 1500.0)

    odds = None
    if odds_cache:
        key = f"{home_team}_vs_{away_team}"
        odds = odds_cache.get(key)

    prediction = get_engine("elo_odds")(
        home_team=home_team,
        away_team=away_team,
        elo_home=elo_home,
        elo_away=elo_away,
        odds_home=odds["home"] if odds else None,
        odds_draw=odds["draw"] if odds else None,
        odds_away=odds["away"] if odds else None,
        is_knockout=is_knockout,
    )

    probs = dict(prediction["outcome_probabilities"])

    if is_knockout:
        # Redistribute draw probability for knockout (extra time / penalties)
        draw_prob = probs["draw"]
        # Split draw: 50% to each team (simplified penalty shootout model)
        probs = {
            "home_win": probs["home_win"] + draw_prob * 0.5,
            "draw": 0.0,
            "away_win": probs["away_win"] + draw_prob * 0.5,
        }

    if prediction_cache is not None:
        prediction_cache[cache_key] = {
            "outcome_probabilities": dict(probs),
            "predicted_score": dict(prediction.get("predicted_score") or {}),
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
    prediction_cache: dict[tuple[str, str, bool], dict[str, Any]] | None = None,
) -> list[tuple[str, int, int]]:
    """Simulate a single group stage.

    Returns list of (team, points, goal_diff) sorted by standings.
    """
    cache = prediction_cache if prediction_cache is not None else {}
    standings: dict[str, dict[str, int]] = {
        team: {"points": 0, "goals_for": 0, "goals_against": 0}
        for team in group_teams
    }

    # Round robin: each team plays every other team
    for i in range(len(group_teams)):
        for j in range(i + 1, len(group_teams)):
            home = group_teams[i]
            away = group_teams[j]

            probs = _simulate_match(
                home,
                away,
                elo_cache,
                odds_cache,
                is_knockout=False,
                prediction_cache=cache,
            )
            outcome = _sample_outcome(probs)

            # Estimate goals from cached engine output (simplified).
            cache_entry = cache.get((home, away, False), {})
            predicted_score = cache_entry.get("predicted_score") or {}
            home_goals = round(float(predicted_score.get("home", 1.0)))
            away_goals = round(float(predicted_score.get("away", 1.0)))

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
    prediction_cache: dict[tuple[str, str, bool], dict[str, Any]] | None = None,
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

            probs = _simulate_match(
                home,
                away,
                elo_cache,
                odds_cache,
                is_knockout=True,
                prediction_cache=prediction_cache,
            )
            outcome = _sample_outcome(probs)

            if outcome == "home_win":
                next_round.append(home)
            else:
                next_round.append(away)

        current_round = next_round

    return current_round[0]


def _fixture_trace(
    fixture: dict[str, Any],
    *,
    winner: str | None = None,
    loser: str | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    for key in (
        "match_id",
        "stage",
        "status",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "kickoff_utc",
        "utc_date",
        "venue",
        "penalty_score",
    ):
        value = fixture.get(key)
        if value is not None and value != "":
            detail[key] = value
    if winner:
        detail["winner"] = winner
    if loser:
        detail["loser"] = loser
    return detail


def simulate_remaining_knockout(
    *,
    fixtures: list[dict[str, Any]],
    elo_cache: dict[str, float],
    odds_cache: dict[str, dict[str, float]] | None = None,
    num_simulations: int = 10000,
) -> dict[str, Any]:
    """Run Monte Carlo from the current real knockout bracket.

    Finished fixtures are locked to their recorded winner. Only unplayed
    fixtures and future rounds are simulated.
    """

    started_at = time.perf_counter()
    playable_fixtures = [
        fixture for fixture in fixtures
        if str(fixture.get("home_team") or "").strip()
        and str(fixture.get("away_team") or "").strip()
    ]
    locked_winners: list[str] = []
    live_teams: set[str] = set()
    excluded_teams: set[str] = set()
    locked_results: list[dict[str, Any]] = []
    simulated_fixtures: list[dict[str, Any]] = []
    initial_unplayed_count = 0

    for fixture in playable_fixtures:
        home = str(fixture.get("home_team") or "").strip()
        away = str(fixture.get("away_team") or "").strip()
        winner, loser = _fixture_winner_loser(fixture)
        if winner:
            locked_winners.append(winner)
            locked_results.append(_fixture_trace(fixture, winner=winner, loser=loser))
            live_teams.add(winner)
            if loser:
                excluded_teams.add(loser)
            continue
        initial_unplayed_count += 1
        simulated_fixtures.append(_fixture_trace(fixture))
        live_teams.update({home, away})

    win_count = {team: 0 for team in sorted(live_teams)}
    final_count = {team: 0 for team in sorted(live_teams)}
    semifinal_count = {team: 0 for team in sorted(live_teams)}
    prediction_cache: dict[tuple[str, str, bool], dict[str, Any]] = {}
    completed_simulations = 0
    skipped_simulations = 0

    for _sim in range(num_simulations):
        current: list[str] = []
        for fixture in playable_fixtures:
            winner, _loser = _fixture_winner_loser(fixture)
            if winner:
                current.append(winner)
                continue

            home = str(fixture.get("home_team") or "").strip()
            away = str(fixture.get("away_team") or "").strip()
            probs = _simulate_match(
                home,
                away,
                elo_cache,
                odds_cache,
                is_knockout=True,
                prediction_cache=prediction_cache,
            )
            outcome = _sample_outcome(probs)
            current.append(home if outcome == "home_win" else away)

        if len(current) < 2:
            skipped_simulations += 1
            continue

        while len(current) > 1:
            if len(current) == 4:
                for team in current:
                    if team in semifinal_count:
                        semifinal_count[team] += 1
            if len(current) == 2:
                for team in current:
                    if team in final_count:
                        final_count[team] += 1

            next_round: list[str] = []
            for i in range(0, len(current), 2):
                if i + 1 >= len(current):
                    next_round.append(current[i])
                    continue
                home = current[i]
                away = current[i + 1]
                probs = _simulate_match(
                    home,
                    away,
                    elo_cache,
                    odds_cache,
                    is_knockout=True,
                    prediction_cache=prediction_cache,
                )
                outcome = _sample_outcome(probs)
                next_round.append(home if outcome == "home_win" else away)
            current = next_round

        winner = current[0]
        if winner in win_count:
            win_count[winner] += 1
        completed_simulations += 1

    denominator = completed_simulations or num_simulations or 1
    win_probability = {
        team: round(count / denominator, 4)
        for team, count in sorted(win_count.items(), key=lambda x: x[1], reverse=True)
    }
    reach_final = {
        team: round(count / denominator, 4)
        for team, count in final_count.items()
    }
    reach_semifinal = {
        team: round(count / denominator, 4)
        for team, count in semifinal_count.items()
    }
    most_likely_winner = (
        max(win_count, key=lambda team: win_count[team])
        if any(win_count.values()) else None
    )

    return {
        "win_probability": win_probability,
        "reach_final": reach_final,
        "reach_semifinal": reach_semifinal,
        "most_likely_winner": most_likely_winner,
        "most_likely_winner_prob": win_probability.get(most_likely_winner, 0) if most_likely_winner else 0.0,
        "simulations": num_simulations,
        "completed_simulations": completed_simulations,
        "skipped_simulations": skipped_simulations,
        "excluded_teams": sorted(excluded_teams),
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "match_probability_cache_size": len(prediction_cache),
        "locked_result_count": len(locked_winners),
        "simulated_match_count": initial_unplayed_count,
        "locked_results": locked_results,
        "simulated_fixtures": simulated_fixtures,
        "remaining_team_count": len(live_teams),
        "simulation_basis": "knockout_fixtures",
    }


def simulate_tournament(
    groups: dict[str, list[str]],
    elo_cache: dict[str, float] | None = None,
    odds_cache: dict[str, dict[str, float]] | None = None,
    num_simulations: int = 10000,
    eliminated_teams: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run Monte Carlo tournament simulation.

    Args:
        groups: Dict of group_name -> list of 4 team names
        elo_cache: Pre-fetched Elo ratings (team_name -> rating)
        odds_cache: Pre-fetched odds (f"{home}_vs_{away}" -> {home, draw, away})
        num_simulations: Number of simulations to run
        eliminated_teams: Teams that must be excluded from title/progression paths

    Returns:
        {
            "win_probability": {team: prob},
            "reach_final": {team: prob},
            "reach_semifinal": {team: prob},
            "most_likely_winner": str,
            "simulations": int,
        }
    """
    started_at = time.perf_counter()
    eliminated_keys = {str(team).strip().casefold() for team in (eliminated_teams or []) if str(team).strip()}

    group_team_names: set[str] = set()
    for teams in groups.values():
        group_team_names.update(teams)

    active_groups = {
        group_name: [team for team in teams if team.strip().casefold() not in eliminated_keys]
        for group_name, teams in groups.items()
    }

    if elo_cache is None:
        # Fetch Elo ratings for active teams. Eliminated teams are kept in the
        # output with zero probability but do not need live Elo lookups.
        elo_cache = {}
        active_teams = {team for teams in active_groups.values() for team in teams}

        async def _fetch_all_elo_ratings(teams: set[str]) -> dict[str, dict[str, Any]]:
            """Batch-fetch Elo ratings for all teams."""
            results = {}
            for team in teams:
                results[team] = await get_elo_rating(team)
            return results

        try:
            elo_data_map = asyncio.run(_fetch_all_elo_ratings(active_teams))
            for team, data in elo_data_map.items():
                elo_cache[team] = data.get("elo_rating", 1500.0)
        except RuntimeError:
            # Event loop already running (called from async context)
            logger.warning(
                "Cannot fetch Elo ratings synchronously (event loop running). "
                "Using default 1500.0 for all active teams."
            )
            for team in active_teams:
                elo_cache[team] = 1500.0

    known_team_names = set(group_team_names) | set(elo_cache.keys()) | {
        str(team).strip() for team in (eliminated_teams or []) if str(team).strip()
    }
    excluded_teams = sorted(
        team for team in known_team_names if team.strip().casefold() in eliminated_keys
    )

    # Initialize counters only for teams still eligible for title/progression
    # paths. Eliminated teams are reported via excluded_teams/qualification_state,
    # not mixed into probability maps where the UI treats entries as contenders.
    all_teams = sorted(
        team for team in known_team_names if team.strip().casefold() not in eliminated_keys
    )
    win_count = {team: 0 for team in all_teams}
    final_count = {team: 0 for team in all_teams}
    semifinal_count = {team: 0 for team in all_teams}

    prediction_cache: dict[tuple[str, str, bool], dict[str, Any]] = {}
    completed_simulations = 0
    skipped_simulations = 0

    for _sim in range(num_simulations):
        # Simulate group stage. World Cup 2026 advances the top two from each
        # group plus the eight best third-place teams, producing a 32-team
        # knockout field. Keeping the bracket at a power of two avoids the
        # invalid 24 -> 12 -> 6 -> 3 semifinal state.
        qualified: list[str] = []
        third_place_candidates: list[tuple[str, int, int]] = []
        for _group_name, teams in active_groups.items():
            results = _simulate_group(teams, elo_cache, odds_cache, prediction_cache=prediction_cache)
            if len(results) < 2:
                continue
            qualified.append(results[0][0])  # Group winner
            qualified.append(results[1][0])  # Runner-up
            if len(results) >= 3:
                third_place_candidates.append(results[2])

        available_teams = len(qualified) + len(third_place_candidates)
        desired_bracket_size = min(32, available_teams)
        while desired_bracket_size > 4 and desired_bracket_size & (desired_bracket_size - 1):
            desired_bracket_size -= 1

        if len(qualified) < desired_bracket_size:
            third_place_candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            needed_thirds = desired_bracket_size - len(qualified)
            qualified.extend(team for team, _points, _gd in third_place_candidates[:needed_thirds])
        elif len(qualified) > desired_bracket_size:
            qualified = qualified[:desired_bracket_size]

        if len(qualified) < 4:
            skipped_simulations += 1
            continue

        # Shuffle to create bracket (in reality, seeding determines bracket)
        random.shuffle(qualified)

        # Simulate knockout rounds
        # Round of 32 -> R16 -> QF -> SF -> Final
        current = list(qualified)
        while len(current) > 4:
            next_round = []
            for i in range(0, len(current), 2):
                if i + 1 >= len(current):
                    next_round.append(current[i])
                    break
                home = current[i]
                away = current[i + 1]
                probs = _simulate_match(
                    home,
                    away,
                    elo_cache,
                    odds_cache,
                    is_knockout=True,
                    prediction_cache=prediction_cache,
                )
                outcome = _sample_outcome(probs)
                next_round.append(home if outcome == "home_win" else away)
            current = next_round

        if len(current) < 4:
            skipped_simulations += 1
            continue

        # Semifinalists
        for team in current:
            semifinal_count[team] += 1

        # Simulate semifinals
        sf1_winner = _simulate_knockout_bracket([current[0], current[1]], elo_cache, odds_cache, prediction_cache)
        sf2_winner = _simulate_knockout_bracket([current[2], current[3]], elo_cache, odds_cache, prediction_cache)

        final_count[sf1_winner] += 1
        final_count[sf2_winner] += 1

        # Final
        winner = _simulate_knockout_bracket([sf1_winner, sf2_winner], elo_cache, odds_cache, prediction_cache)
        win_count[winner] += 1
        completed_simulations += 1

    # Compute probabilities. If some runs were skipped due to too-small active
    # fields, report probabilities over completed runs; if none completed, all
    # probabilities remain 0.
    denominator = completed_simulations or num_simulations or 1
    win_probability = {
        team: round(count / denominator, 4)
        for team, count in sorted(win_count.items(), key=lambda x: x[1], reverse=True)
    }
    reach_final = {
        team: round(count / denominator, 4)
        for team, count in final_count.items()
    }
    reach_semifinal = {
        team: round(count / denominator, 4)
        for team, count in semifinal_count.items()
    }

    most_likely_winner = (
        max(win_count, key=lambda team: win_count[team])
        if any(win_count.values()) else None
    )

    return {
        "win_probability": win_probability,
        "reach_final": reach_final,
        "reach_semifinal": reach_semifinal,
        "most_likely_winner": most_likely_winner,
        "most_likely_winner_prob": win_probability.get(most_likely_winner, 0) if most_likely_winner else 0.0,
        "simulations": num_simulations,
        "completed_simulations": completed_simulations,
        "skipped_simulations": skipped_simulations,
        "excluded_teams": excluded_teams,
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "match_probability_cache_size": len(prediction_cache),
    }


def _parse_score(value: Any) -> float | None:
    """Parse a fixture score; None when missing or non-numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fixture_winner_loser(fixture: dict[str, Any]) -> tuple[str | None, str | None]:
    status = str(fixture.get("status") or "").strip().lower()
    if status != "finished":
        return None, None

    home = str(fixture.get("home_team") or "").strip()
    away = str(fixture.get("away_team") or "").strip()
    if not home or not away:
        return None, None

    verified_winner = str(fixture.get("winner") or "").strip()
    if verified_winner:
        if verified_winner.casefold() == home.casefold():
            return home, away
        if verified_winner.casefold() == away.casefold():
            return away, home

    home_score = _parse_score(fixture.get("home_score"))
    away_score = _parse_score(fixture.get("away_score"))
    if home_score is None or away_score is None:
        return None, None

    if home_score > away_score:
        return home, away
    if away_score > home_score:
        return away, home
    return None, None
