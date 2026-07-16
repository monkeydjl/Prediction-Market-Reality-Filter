"""EloTimeMachine — replays Elo ratings from season start with given parameters.

Reuses the stateless Elo functions from app.sports._shared.elo_calculator
(verbatim copy of NBA's elo_calculator). The time machine records Elo
snapshots BEFORE each match so the BacktestRunner can use them for prediction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sports._shared.elo_calculator import (
    compute_expected_score,
    update_elo,
    apply_season_regression,
)


@dataclass(frozen=True)
class EloParams:
    """Elo computation parameters for backtesting."""
    hfa: float
    k_regular: float
    k_playoff: float
    season_carry: float  # 0.0 = full reset, 1.0 = no regression
    initial: float       # default 1500
    league_avg_total: float  # only used for display, not Elo computation


class EloTimeMachine:
    """Replays Elo ratings from season start with given parameters."""

    def replay(
        self,
        sport: str,
        matches: list[dict[str, Any]],
        elo_params: EloParams,
    ) -> dict[str, dict[str, float]]:
        """Replay Elo from season start with given params.

        Args:
            sport: "nba" / "mlb" / "nhl" (affects nothing — formula is same)
            matches: List of match dicts with keys:
                - match_id (str)
                - home_team (str)
                - away_team (str)
                - home_score (int)
                - away_score (int)
                - season (int)
                - is_playoff (bool, optional)
                Matches MUST be in chronological order.
            elo_params: EloParams with HFA, K-factors, season_carry, initial.

        Returns:
            {match_id: {"home_elo": float, "away_elo": float}} snapshot
            BEFORE each match (for prediction).
        """
        ratings: dict[str, float] = {}
        current_season: int | None = None
        snapshots: dict[str, dict[str, float]] = {}

        for match in matches:
            season = match["season"]
            # Apply regression at season boundary
            if current_season is not None and season != current_season:
                for team in ratings:
                    ratings[team] = apply_season_regression(
                        ratings[team], mean=elo_params.initial, carry=elo_params.season_carry,
                    )
            current_season = season

            home = match["home_team"]
            away = match["away_team"]
            # Initialize new teams at initial Elo
            if home not in ratings:
                ratings[home] = elo_params.initial
            if away not in ratings:
                ratings[away] = elo_params.initial

            elo_home = ratings[home]
            elo_away = ratings[away]

            # Record snapshot BEFORE match
            snapshots[match["match_id"]] = {
                "home_elo": elo_home,
                "away_elo": elo_away,
            }

            # Update Elo after match
            expected = compute_expected_score(elo_home, elo_away, elo_params.hfa)
            home_won = match["home_score"] > match["away_score"]
            actual_home = 1.0 if home_won else 0.0
            actual_away = 1.0 - actual_home

            k = elo_params.k_playoff if match.get("is_playoff") else elo_params.k_regular
            ratings[home] = update_elo(elo_home, expected, actual_home, k)
            ratings[away] = update_elo(elo_away, 1.0 - expected, actual_away, k)

        return snapshots
