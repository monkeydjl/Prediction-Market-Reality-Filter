# backend/app/kernel/adapters/football_feature_builder.py
"""FootballFeatureBuilder — computes FeatureSet from raw data.

Computes:
- General layer: rest days, travel distance
- Team layer: Elo, form, h2h, market value
- Market layer: odds
- Player layer: injury impact, availability
- Environment layer: weather, venue
- Custom: xG, PPDA, Possession, Shots (football-specific)

This builder is sport-specific (football) but implements the sport-agnostic
FeatureBuilder Protocol. The Kernel never knows it is football-specific — it
just calls ``build(match, raw)`` and gets back a FeatureSet.
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

_FOOTBALL = SportIdentity(code="football", name="Football")


class FootballFeatureBuilder:
    """Builds FeatureSet for football matches.

    Implements the :class:`~app.kernel.protocols.FeatureBuilder` Protocol.
    Consumes a raw dict with keys ``team``, ``market``, ``player``,
    ``environment``, ``general``, and ``custom`` and produces a
    :class:`~app.kernel.domain.FeatureSet`.
    """

    def sport(self) -> SportIdentity:
        """Return the sport this builder serves."""
        return _FOOTBALL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        """Build a :class:`FeatureSet` from raw data.

        Parameters
        ----------
        match:
            The :class:`MatchIdentity` this feature set belongs to.
        raw:
            A dict with optional sub-dicts ``team``, ``market``, ``player``,
            ``environment``, ``general``, and ``custom``. Missing sub-dicts
            are treated as empty, so every field defaults to ``None``.

        Returns
        -------
        FeatureSet
            A frozen feature package with ``data_quality`` set to ``"real"``
            when both Elo and odds are present, otherwise ``"partial"``.
        """
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Determine data quality
        has_elo = team_raw.get("elo_home") is not None
        has_odds = market_raw.get("odds_home") is not None
        quality_notes: list[str] = []
        if not has_odds:
            quality_notes.append("betting_odds_unavailable")
        data_quality = "real" if (has_elo and has_odds) else "partial"

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
                h2h_home_win_rate=team_raw.get("h2h_home_win_rate"),
                h2h_draw_rate=team_raw.get("h2h_draw_rate"),
                market_value_home=team_raw.get("market_value_home"),
                market_value_away=team_raw.get("market_value_away"),
            ),
            market=MarketFeatures(
                odds_home=market_raw.get("odds_home"),
                odds_draw=market_raw.get("odds_draw"),
                odds_away=market_raw.get("odds_away"),
                odds_source=market_raw.get("odds_source"),
                odds_fresh=market_raw.get("odds_fresh", False),
            ),
            player=PlayerFeatures(
                key_players_available_home=player_raw.get("key_players_available_home"),
                key_players_available_away=player_raw.get("key_players_available_away"),
                injury_impact_home=player_raw.get("injury_impact_home"),
                injury_impact_away=player_raw.get("injury_impact_away"),
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=env_raw.get("weather_temp_c"),
                weather_condition=env_raw.get("weather_condition"),
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="1.0",
        )
