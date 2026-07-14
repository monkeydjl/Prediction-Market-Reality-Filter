# backend/app/sports/hockey/feature_builder.py
"""HockeyFeatureBuilder — computes FeatureSet from raw NHL data.

Maps raw dict from NHLAdapter to standardized FeatureSet. Same pattern
as BaseballFeatureBuilder / BasketballFeatureBuilder: odds absence does
NOT downgrade data quality because there is no odds source by design,
and HockeyEngine does not use odds.

Feature version: "nhl-1.0" (distinct from football's "1.0",
basketball's "nba-1.0", and baseball's "mlb-1.0").

Overtime/shootout flags (Constraint 22) are carried through unchanged
from raw["custom"] into FeatureSet.custom — they do NOT affect
MatchOutcome.outcome (which stays binary home_win/away_win).
"""
from __future__ import annotations

import logging

from app.kernel.domain import (
    SportIdentity,
    MatchIdentity,
    FeatureSet,
    GeneralFeatures,
    TeamFeatures,
    MarketFeatures,
    PlayerFeatures,
    EnvironmentFeatures,
)

logger = logging.getLogger(__name__)

_HOCKEY = SportIdentity(code="hockey", name="Hockey")


class HockeyFeatureBuilder:
    """Builds FeatureSet for NHL hockey matches.

    Implements the FeatureBuilder Protocol. Consumes a raw dict with
    keys ``team``, ``market``, ``player``, ``environment``, ``general``,
    and ``custom`` and produces a FeatureSet.
    """

    def sport(self) -> SportIdentity:
        return _HOCKEY

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Data quality: "real" if Elo exists, "partial" otherwise.
        # Odds absence does NOT downgrade quality (no odds source for NHL).
        has_elo = team_raw.get("elo_home") is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = []

        # Goalie availability flag for player layer
        goalie_home = player_raw.get("starting_goalie_home")
        goalie_away = player_raw.get("starting_goalie_away")
        goalie_home_available = goalie_home is not None
        goalie_away_available = goalie_away is not None

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=None,  # Not tracked for hockey
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for hockey
                h2h_draw_rate=None,  # Hockey has no draws (binary outcome)
                market_value_home=None,
                market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=None,  # No odds source
                odds_draw=None,
                odds_away=None,
                odds_source=None,
                odds_fresh=False,
            ),
            player=PlayerFeatures(
                key_players_available_home=goalie_home_available,
                key_players_available_away=goalie_away_available,
                injury_impact_home=None,
                injury_impact_away=None,
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,  # Indoor sport — not applicable
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="nhl-1.0",
        )
