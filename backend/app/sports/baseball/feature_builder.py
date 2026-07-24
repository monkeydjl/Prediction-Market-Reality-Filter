# backend/app/sports/baseball/feature_builder.py
"""BaseballFeatureBuilder — computes FeatureSet from raw MLB data.

Maps raw dict from MLBAdapter to standardized FeatureSet. Same pattern
as BasketballFeatureBuilder: odds absence does NOT downgrade data quality
because there is no odds source by design, and BaseballEngine does not
use odds.

Feature version: "mlb-1.0" (distinct from football's "1.0" and
basketball's "nba-1.0").
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

from app.kernel.market_liquidity import inject_liquidity_into_custom

logger = logging.getLogger(__name__)

_BASEBALL = SportIdentity(code="baseball", name="Baseball")


class BaseballFeatureBuilder:
    """Builds FeatureSet for MLB baseball matches.

    Implements the FeatureBuilder Protocol. Consumes a raw dict with
    keys ``team``, ``market``, ``player``, ``environment``, ``general``,
    and ``custom`` and produces a FeatureSet.
    """

    def sport(self) -> SportIdentity:
        return _BASEBALL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Data quality: "real" if Elo exists, "partial" otherwise.
        # Odds absence does NOT downgrade quality (no odds source for MLB).
        has_elo = team_raw.get("elo_home") is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = []

        # Pitcher availability flag for player layer
        pitcher_home = player_raw.get("starting_pitcher_home")
        pitcher_away = player_raw.get("starting_pitcher_away")
        pitcher_home_available = pitcher_home is not None
        pitcher_away_available = pitcher_away is not None

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=general_raw.get("travel_distance_km"),
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for baseball
                h2h_draw_rate=None,  # Baseball has no draws
                market_value_home=None,
                market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=market_raw.get("odds_home"),
                odds_draw=market_raw.get("odds_draw"),
                odds_away=market_raw.get("odds_away"),
                odds_source=market_raw.get("odds_source"),
                odds_fresh=bool(market_raw.get("odds_fresh", False)),
            ),
            player=PlayerFeatures(
                key_players_available_home=pitcher_home_available,
                key_players_available_away=pitcher_away_available,
                injury_impact_home=None,
                injury_impact_away=None,
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=env_raw.get("weather_temp_c"),
                weather_condition=env_raw.get("weather_condition"),
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=inject_liquidity_into_custom(
                raw.get("custom", {}),
                match.match_id,
            ),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="mlb-1.0",
        )
