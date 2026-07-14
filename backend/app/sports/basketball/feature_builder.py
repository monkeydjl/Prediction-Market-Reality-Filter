# backend/app/sports/basketball/feature_builder.py
"""BasketballFeatureBuilder — computes FeatureSet from raw NBA data.

Maps raw dict from NBAAdapter to standardized FeatureSet. Unlike
FootballFeatureBuilder, odds absence does NOT downgrade data quality
because the balldontlie.io free tier has no odds by design, and
BasketballEngine does not use odds.

Feature version: "nba-1.0" (distinct from football's "1.0").
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

_BASKETBALL = SportIdentity(code="basketball", name="Basketball")


class BasketballFeatureBuilder:
    """Builds FeatureSet for NBA basketball matches.

    Implements the FeatureBuilder Protocol. Consumes a raw dict with
    keys ``team``, ``market``, ``player``, ``environment``, ``general``,
    and ``custom`` and produces a FeatureSet.
    """

    def sport(self) -> SportIdentity:
        return _BASKETBALL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Data quality: "real" if Elo exists, "partial" otherwise.
        # Unlike football, odds absence does NOT downgrade quality.
        has_elo = team_raw.get("elo_home") is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = []

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=None,  # Not tracked for basketball
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for basketball
                h2h_draw_rate=None,  # Basketball has no draws
                market_value_home=None,  # Not applicable
                market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=None,  # Free tier has no odds
                odds_draw=None,
                odds_away=None,
                odds_source=None,
                odds_fresh=False,
            ),
            player=PlayerFeatures(
                key_players_available_home=None,  # Free tier has no injuries
                key_players_available_away=None,
                injury_impact_home=None,
                injury_impact_away=None,
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,  # Indoor sport
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="nba-1.0",
        )
