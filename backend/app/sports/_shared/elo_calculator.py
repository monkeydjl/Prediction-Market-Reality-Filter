# backend/app/sports/_shared/elo_calculator.py
"""Stateless Elo computation functions shared across sports.

This is a verbatim copy of ``app/sports/basketball/elo_calculator.py`` so
that MLB/NHL engines can import the same stateless utilities without a
cross-sport import dependency on the basketball module. The NBA original
remains untouched (Phase 5 Constraint 21).

All sports pass their own HFA / K-factors / season-carry at call time, so
the defaults here match basketball (HFA=100, K=20/30, carry=0.75) but are
overridden by callers:
    - MLB: HFA=50, K=20/30, carry=0.7
    - NHL: HFA=55, K=20/30, carry=0.75

``hfa`` and ``k`` are annotated ``float`` rather than the NBA original's
``int``: the formulas are float arithmetic throughout, and the backtest /
parameter-optimizer paths tune both as continuous values (EloParams.hfa,
EloParams.k_regular). The bodies are otherwise unchanged.
"""
from __future__ import annotations


def compute_expected_score(
    elo_home: float, elo_away: float, hfa: float = 100,
) -> float:
    """Compute expected probability that home team wins.

    Uses standard Elo formula with home field advantage:
        E_home = 1 / (1 + 10^((elo_away - elo_home - hfa) / 400))

    Args:
        elo_home: Home team Elo rating.
        elo_away: Away team Elo rating.
        hfa: Home field advantage in Elo points (default 100).

    Returns:
        Expected probability (0.0 to 1.0) that home team wins.
    """
    exponent = (elo_away - elo_home - hfa) / 400.0
    return 1.0 / (1.0 + 10.0 ** exponent)


def update_elo(
    elo: float, expected: float, actual: float, k: float = 20,
) -> float:
    """Update Elo rating after a single game.

    Args:
        elo: Current Elo rating.
        expected: Expected score (from compute_expected_score).
        actual: Actual score (1.0 for win, 0.0 for loss).
        k: K-factor (default 20 for regular season, 30 for playoff).

    Returns:
        New Elo rating.
    """
    return elo + k * (actual - expected)


def apply_season_regression(
    elo: float, mean: float = 1500.0, carry: float = 0.75,
) -> float:
    """Apply season-start regression toward league mean.

    new_elo = carry * old_elo + (1 - carry) * mean

    Args:
        elo: Previous season's final Elo.
        mean: League average Elo (default 1500).
        carry: Fraction of previous Elo to retain (default 0.75).

    Returns:
        Regressed Elo for the new season.
    """
    return carry * elo + (1.0 - carry) * mean


def seed_elo_from_games(
    games: list[dict],
    hfa: int = 100,
    k_regular: int = 20,
    k_playoff: int = 30,
) -> dict[str, float]:
    """Compute final Elo ratings by processing games chronologically.

    All teams start at 1500. Season regression (carry=0.75) is applied
    when the ``season`` field changes between consecutive games.

    Args:
        games: List of game dicts, each with keys:
            - home_team (str)
            - away_team (str)
            - home_score (int)
            - away_score (int)
            - is_playoff (bool)
            - season (int)
            Games MUST be in chronological order.
        hfa: Home field advantage (default 100).
        k_regular: K-factor for regular season (default 20).
        k_playoff: K-factor for playoff (default 30).

    Returns:
        Dict mapping team name to final Elo rating.
    """
    ratings: dict[str, float] = {}
    current_season: int | None = None

    for game in games:
        season = game["season"]
        # Apply regression at season boundary
        if current_season is not None and season != current_season:
            for team in ratings:
                ratings[team] = apply_season_regression(ratings[team])
        current_season = season

        home = game["home_team"]
        away = game["away_team"]
        # Initialize new teams at 1500
        if home not in ratings:
            ratings[home] = 1500.0
        if away not in ratings:
            ratings[away] = 1500.0

        elo_home = ratings[home]
        elo_away = ratings[away]
        expected = compute_expected_score(elo_home, elo_away, hfa)

        home_won = game["home_score"] > game["away_score"]
        actual_home = 1.0 if home_won else 0.0
        actual_away = 1.0 - actual_home

        k = k_playoff if game.get("is_playoff") else k_regular
        ratings[home] = update_elo(elo_home, expected, actual_home, k)
        ratings[away] = update_elo(elo_away, 1.0 - expected, actual_away, k)

    return ratings
