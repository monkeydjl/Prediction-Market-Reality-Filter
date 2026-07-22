# backend/app/sports/lol/feature_builder.py
"""LolFeatureBuilder — computes FeatureSet from raw LoL series data.

Maps raw dict (from LolAdapter) to standardized FeatureSet with
market-only custom fields for binary series outcomes.

Feature version: "lol-market-0.1".
"""
from __future__ import annotations

import logging
import re

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

_LOL = SportIdentity(code="lol", name="League of Legends")
_BO_RE = re.compile(r"Bo(\d+)", re.IGNORECASE)


def _as_prob(value: object) -> float | None:
    if value is None:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 < p < 1.0:
        return p
    return None


def _resolve_best_of(raw: dict, env_raw: dict, custom_raw: dict) -> int | None:
    for candidate in (
        custom_raw.get("best_of"),
        raw.get("best_of"),
        env_raw.get("best_of"),
    ):
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue

    venue = env_raw.get("venue") or raw.get("venue")
    if isinstance(venue, str):
        match = _BO_RE.search(venue.strip())
        if match:
            return int(match.group(1))
    return None


def _extract_mkt_probs(raw: dict, market_raw: dict, custom_raw: dict) -> tuple[float | None, float | None]:
    mkt_home = _as_prob(custom_raw.get("mkt_home"))
    mkt_away = _as_prob(custom_raw.get("mkt_away"))
    if mkt_home is None:
        mkt_home = _as_prob(market_raw.get("mkt_home"))
    if mkt_away is None:
        mkt_away = _as_prob(market_raw.get("mkt_away"))
    if mkt_home is None:
        mkt_home = _as_prob(raw.get("mkt_home"))
    if mkt_away is None:
        mkt_away = _as_prob(raw.get("mkt_away"))
    return mkt_home, mkt_away


class LolFeatureBuilder:
    """Builds FeatureSet for League of Legends series (market-first).

    Implements the FeatureBuilder Protocol. Team Elo/xG are not required;
    data quality is driven by market probability presence.
    """

    feature_version = "lol-market-0.1"

    def sport(self) -> SportIdentity:
        return _LOL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {}) or {}
        market_raw = raw.get("market", {}) or {}
        player_raw = raw.get("player", {}) or {}
        env_raw = raw.get("environment", {}) or {}
        general_raw = raw.get("general", {}) or {}
        custom_raw = dict(raw.get("custom", {}) or {})

        best_of = _resolve_best_of(raw, env_raw, custom_raw)
        series_format = f"Bo{best_of}" if best_of else "Bo?"

        mkt_home, mkt_away = _extract_mkt_probs(raw, market_raw, custom_raw)
        has_mkt = mkt_home is not None and mkt_away is not None
        data_quality = "real" if has_mkt else "partial"
        quality_notes: list[str] = []

        custom: dict = {
            **custom_raw,
            "best_of": best_of,
            "series_format": series_format,
        }
        if mkt_home is not None:
            custom["mkt_home"] = mkt_home
        if mkt_away is not None:
            custom["mkt_away"] = mkt_away

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
                h2h_home_win_rate=None,
                h2h_draw_rate=None,
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
                key_players_available_home=player_raw.get("key_players_available_home"),
                key_players_available_away=player_raw.get("key_players_available_away"),
                injury_impact_home=player_raw.get("injury_impact_home"),
                injury_impact_away=player_raw.get("injury_impact_away"),
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=inject_liquidity_into_custom(custom, match.match_id),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version=self.feature_version,
        )
