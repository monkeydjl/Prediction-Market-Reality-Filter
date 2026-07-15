"""Tests for The Odds API multi-league extension + feature builder odds injection.

Phase 7, Task 4 — extends the World Cup-only Odds API integration to all 10
competitions (wc, ucl, epl, laliga, bundesliga, seriea, ligue1, nba, mlb, nhl)
and enables odds injection in the NBA/MLB/NHL feature builders.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from httpx import Response

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)


# ---------------------------------------------------------------------------
# MatchIdentity fixtures (use the real dataclass shape — the brief's tests
# used an incorrect constructor that does not exist on MatchIdentity).
# ---------------------------------------------------------------------------
_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)
_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)
_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _nba_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="nba-20250101-LAL-BOS",
        season=SeasonIdentity(competition=_NBA, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=_NBA),
        away=TeamIdentity(code="BOS", name="Boston Celtics", competition=_NBA),
        kickoff_utc=datetime(2025, 1, 1, 19, 0, tzinfo=timezone.utc),
    )


def _mlb_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="mlb-20250101-NYY-BOS",
        season=SeasonIdentity(competition=_MLB, season_key="2025"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NYY", name="New York Yankees", competition=_MLB),
        away=TeamIdentity(code="BOS", name="Boston Red Sox", competition=_MLB),
        kickoff_utc=datetime(2025, 4, 1, 19, 0, tzinfo=timezone.utc),
    )


def _nhl_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="nhl-20250101-BOS-TOR",
        season=SeasonIdentity(competition=_NHL, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="BOS", name="Boston Bruins", competition=_NHL),
        away=TeamIdentity(code="TOR", name="Toronto Maple Leafs", competition=_NHL),
        kickoff_utc=datetime(2025, 1, 1, 19, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. COMPETITION_TO_ODDS_API_SPORT mapping covers all 10 competitions
# ---------------------------------------------------------------------------
def test_competition_to_odds_api_sport_covers_all_10():
    from app.services.odds_api_service import COMPETITION_TO_ODDS_API_SPORT
    expected = {"wc", "ucl", "epl", "laliga", "bundesliga",
                "seriea", "ligue1", "nba", "mlb", "nhl"}
    assert expected.issubset(set(COMPETITION_TO_ODDS_API_SPORT.keys()))
    assert COMPETITION_TO_ODDS_API_SPORT["wc"] == "soccer_fifa_world_cup"
    assert COMPETITION_TO_ODDS_API_SPORT["nba"] == "basketball_nba"
    assert COMPETITION_TO_ODDS_API_SPORT["nhl"] == "icehockey_nhl"


# ---------------------------------------------------------------------------
# 2. fetch_match_odds(competition="nba") hits basketball_nba sport key
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_match_odds_uses_nba_sport_key():
    from app.services import odds_api_service
    # Build an API response with one NBA fixture.
    api_data = [{
        "id": "evt-1",
        "home_team": "Los Angeles Lakers",
        "away_team": "Boston Celtics",
        "commence_time": "2025-01-01T19:00:00Z",
        "bookmakers": [],
    }]
    captured_params = {}

    async def fake_get(url, params=None):
        captured_params["url"] = url
        captured_params["params"] = params
        return Response(200, json=api_data)

    with patch.object(odds_api_service, "ODDS_API_KEY", "fake-key"), \
         patch.object(odds_api_service, "settings") as mock_settings, \
         patch("app.services.odds_api_service.httpx.AsyncClient") as mock_client_cls:
        # Stub settings so the ODDS_API_ENABLED guard does not short-circuit.
        mock_settings.ODDS_API_ENABLED = True
        mock_settings.ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=fake_get)
        # Reset quota so the skip-quota guard does not short-circuit.
        with patch.object(odds_api_service, "_quota_remaining", None):
            result = await odds_api_service.fetch_match_odds(
                "Los Angeles Lakers", "Boston Celtics", competition="nba"
            )

    # URL must reference the NBA sport key, NOT soccer_fifa_world_cup.
    assert "basketball_nba" in captured_params["url"]
    assert "soccer_fifa_world_cup" not in captured_params["url"]


# ---------------------------------------------------------------------------
# 3. Regression: default competition="wc" still uses soccer_fifa_world_cup
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_match_odds_default_competition_is_wc():
    # Regression: default competition="wc" must use soccer_fifa_world_cup.
    from app.services import odds_api_service
    api_data = [{
        "id": "evt-1", "home_team": "Brazil", "away_team": "Argentina",
        "commence_time": "2026-06-01T19:00:00Z", "bookmakers": [],
    }]
    captured_params = {}

    async def fake_get(url, params=None):
        captured_params["url"] = url
        return Response(200, json=api_data)

    with patch.object(odds_api_service, "ODDS_API_KEY", "fake-key"), \
         patch.object(odds_api_service, "settings") as mock_settings, \
         patch("app.services.odds_api_service.httpx.AsyncClient") as mock_client_cls:
        # Stub settings so the ODDS_API_ENABLED guard does not short-circuit.
        mock_settings.ODDS_API_ENABLED = True
        mock_settings.ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=fake_get)
        with patch.object(odds_api_service, "_quota_remaining", None):
            await odds_api_service.fetch_match_odds("Brazil", "Argentina")

    assert "soccer_fifa_world_cup" in captured_params["url"]


# ---------------------------------------------------------------------------
# 4. Cache key is namespaced by competition
# ---------------------------------------------------------------------------
def test_cache_key_includes_competition():
    from app.services.odds_cache_service import get_match_key
    key_wc = get_match_key("Brazil", "Argentina", competition="wc")
    key_nba = get_match_key("Lakers", "Celtics", competition="nba")
    # Different competitions produce distinguishable keys.
    assert "wc" in key_wc
    assert "nba" in key_nba
    assert key_wc != key_nba


# ---------------------------------------------------------------------------
# 5. Football _shared.fetch_match_odds forwards competition to the cache
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_football_shared_passes_competition():
    # The football _shared.fetch_match_odds must forward competition to the cache.
    from app.sports.football.adapters import _shared
    with patch("app.services.odds_cache_service.get_cached_odds",
               new=AsyncMock(return_value=None)) as mock_get:
        await _shared.fetch_match_odds("Real Madrid", "Barcelona", competition="ucl")
        # The mock is called with competition="ucl".
        assert mock_get.call_args.kwargs.get("competition") == "ucl" or \
               "ucl" in mock_get.call_args.args


# ---------------------------------------------------------------------------
# 6. Basketball feature builder reads odds from market_raw
# ---------------------------------------------------------------------------
def test_basketball_feature_builder_reads_odds_from_market_raw():
    from app.sports.basketball.feature_builder import BasketballFeatureBuilder
    fb = BasketballFeatureBuilder()
    match = _nba_match()
    raw = {
        "team": {"elo_home": 1500, "elo_away": 1500},
        "market": {"odds_home": 1.8, "odds_away": 2.2, "odds_source": "odds_api", "odds_fresh": True},
    }
    fs = fb.build(match, raw)
    assert fs.market.odds_home == 1.8
    assert fs.market.odds_away == 2.2
    assert fs.market.odds_source == "odds_api"
    assert fs.market.odds_fresh is True


# ---------------------------------------------------------------------------
# 7. Baseball feature builder reads odds from market_raw
# ---------------------------------------------------------------------------
def test_baseball_feature_builder_reads_odds_from_market_raw():
    from app.sports.baseball.feature_builder import BaseballFeatureBuilder
    fb = BaseballFeatureBuilder()
    match = _mlb_match()
    raw = {
        "team": {"elo_home": 1500, "elo_away": 1500},
        "market": {"odds_home": 1.7, "odds_away": 2.3},
    }
    fs = fb.build(match, raw)
    assert fs.market.odds_home == 1.7
    assert fs.market.odds_away == 2.3


# ---------------------------------------------------------------------------
# 8. Hockey feature builder reads odds from market_raw
# ---------------------------------------------------------------------------
def test_hockey_feature_builder_reads_odds_from_market_raw():
    from app.sports.hockey.feature_builder import HockeyFeatureBuilder
    fb = HockeyFeatureBuilder()
    match = _nhl_match()
    raw = {
        "team": {"elo_home": 1500, "elo_away": 1500},
        "market": {"odds_home": 1.9, "odds_away": 2.1},
    }
    fs = fb.build(match, raw)
    assert fs.market.odds_home == 1.9
    assert fs.market.odds_away == 2.1


# ---------------------------------------------------------------------------
# 9. Default behavior unchanged: market_raw has no odds → None
# ---------------------------------------------------------------------------
def test_feature_builder_odds_default_none_when_absent():
    from app.sports.basketball.feature_builder import BasketballFeatureBuilder
    fb = BasketballFeatureBuilder()
    match = _nba_match()
    raw = {"team": {"elo_home": 1500, "elo_away": 1500}, "market": {}}
    fs = fb.build(match, raw)
    # When market_raw has no odds, behavior is unchanged (None).
    assert fs.market.odds_home is None
    assert fs.market.odds_away is None
