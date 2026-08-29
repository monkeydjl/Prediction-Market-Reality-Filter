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
from app.kernel.feature_provenance import resolve_elo_provenance
from app.kernel.market_liquidity import inject_liquidity_into_custom

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
        # The NBA adapter reads kernel_elo_ratings, which returns None for an
        # unknown team, so it reports no provenance and nothing is dropped here;
        # the call keeps one definition of "usable Elo" across all five sports.
        elo = resolve_elo_provenance(team_raw)
        has_elo = elo.elo_home is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = list(elo.notes)

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=general_raw.get("travel_distance_km"),
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=elo.elo_home,
                elo_rating_away=elo.elo_away,
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for basketball
                h2h_draw_rate=None,  # Basketball has no draws
                market_value_home=None,  # Not applicable
                market_value_away=None,
                elo_source=elo.elo_source,
            ),
            market=MarketFeatures(
                odds_home=market_raw.get("odds_home"),
                odds_draw=market_raw.get("odds_draw"),
                odds_away=market_raw.get("odds_away"),
                odds_source=market_raw.get("odds_source"),
                odds_fresh=bool(market_raw.get("odds_fresh", False)),
            ),
            player=PlayerFeatures(
                key_players_available_home=player_raw.get("key_players_available_home"),
                key_players_available_away=player_raw.get("key_players_available_away"),
                injury_impact_home=player_raw.get("injury_impact_home"),
                injury_impact_away=player_raw.get("injury_impact_away"),
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,  # Indoor sport
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=inject_liquidity_into_custom(
                raw.get("custom", {}),
                match.match_id,
            ),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="nba-1.0",
        )
