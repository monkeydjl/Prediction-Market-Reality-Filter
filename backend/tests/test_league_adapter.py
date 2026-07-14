# backend/tests/test_league_adapter.py
"""Tests for league_adapter — config-driven adapter for league-format football."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.sports.football.adapters.league_adapter import (
    LeagueConfig,
    LeagueAdapter,
    LEAGUE_REGISTRY,
    _LALIGA_CONFIG,
    _BUNDESLIGA_CONFIG,
    _SERIEA_CONFIG,
    _LIGUE1_CONFIG,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_fixture(match_id="laliga-123", competition="laliga", stage="regular_season"):
    """Create a mock KernelMatchFixture row."""
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = competition
    fixture.season = "2025-26"
    fixture.home_team = "Real Madrid CF"
    fixture.away_team = "FC Barcelona"
    fixture.kickoff_utc = datetime(2025, 9, 14, 20, 0, tzinfo=timezone.utc)
    fixture.stage = stage
    fixture.status = "scheduled"
    fixture.venue = "Santiago Bernabéu"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


def _make_result(match_id="laliga-123"):
    """Create a mock KernelMatchResult row."""
    result = MagicMock()
    result.match_id = match_id
    result.home_score = 2
    result.away_score = 1
    result.outcome = "home_win"
    result.finished_at = datetime(2025, 9, 14, 22, 0, tzinfo=timezone.utc)
    return result


# ---------------------------------------------------------------------------
# TestLeagueConfig
# ---------------------------------------------------------------------------

class TestLeagueConfig:
    def test_frozen_immutable(self):
        cfg = _LALIGA_CONFIG
        with pytest.raises(FrozenInstanceError):
            cfg.code = "modified"  # type: ignore

    def test_laliga_config_values(self):
        cfg = _LALIGA_CONFIG
        assert cfg.code == "laliga"
        assert cfg.name == "La Liga"
        assert cfg.match_id_prefix == "laliga-"
        assert cfg.fd_competition == "PD"
        assert cfg.default_stage == "regular_season"
        assert cfg.stage_map == {}

    def test_bundesliga_config_values(self):
        cfg = _BUNDESLIGA_CONFIG
        assert cfg.code == "bundesliga"
        assert cfg.fd_competition == "BL1"
        assert cfg.match_id_prefix == "bundesliga-"

    def test_seriea_config_values(self):
        cfg = _SERIEA_CONFIG
        assert cfg.code == "seriea"
        assert cfg.fd_competition == "SA"
        assert cfg.match_id_prefix == "seriea-"

    def test_ligue1_config_values(self):
        cfg = _LIGUE1_CONFIG
        assert cfg.code == "ligue1"
        assert cfg.fd_competition == "FL1"
        assert cfg.match_id_prefix == "ligue1-"


# ---------------------------------------------------------------------------
# TestLeagueAdapterProtocol
# ---------------------------------------------------------------------------

class TestLeagueAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        assert isinstance(adapter, DataAdapter)


# ---------------------------------------------------------------------------
# TestLeagueAdapterGetMatchIdentity
# ---------------------------------------------------------------------------

class TestLeagueAdapterGetMatchIdentity:
    @patch("app.sports.football.adapters.league_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        identity = adapter.get_match_identity("laliga-123")
        assert identity.match_id == "laliga-123"
        assert identity.home.name == "Real Madrid CF"
        assert identity.away.name == "FC Barcelona"
        assert identity.stage == "regular_season"

    @patch("app.sports.football.adapters.league_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        identity = adapter.get_match_identity("laliga-nonexistent")
        assert identity.match_id == "laliga-nonexistent"
        assert identity.home.name == "Home"


# ---------------------------------------------------------------------------
# TestLeagueAdapterFetchAllData
# ---------------------------------------------------------------------------

class TestLeagueAdapterFetchAllData:
    @patch("app.sports.football.adapters.league_adapter.fetch_elo_and_odds")
    def test_fetch_all_data_uses_club_elo(self, mock_fetch):
        mock_fetch.return_value = {"team": {}, "market": {}, "player": {}}
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        match = MagicMock()
        result = adapter.fetch_all_data(match)
        assert "team" in result
        # Verify fetch_elo_and_odds was called with club scope and laliga aliases
        call_args = mock_fetch.call_args
        assert call_args.kwargs["elo_scope"] == "club"
        assert call_args.kwargs["team_aliases"] is _LALIGA_CONFIG.team_aliases

    @patch("app.sports.football.adapters.league_adapter.fetch_elo_and_odds")
    def test_fetch_all_data_with_bundesliga_aliases(self, mock_fetch):
        mock_fetch.return_value = {"team": {}}
        adapter = LeagueAdapter(_BUNDESLIGA_CONFIG)
        adapter.fetch_all_data(MagicMock())
        call_args = mock_fetch.call_args
        assert call_args.kwargs["team_aliases"] is _BUNDESLIGA_CONFIG.team_aliases


# ---------------------------------------------------------------------------
# TestLeagueAdapterFetchOutcome
# ---------------------------------------------------------------------------

class TestLeagueAdapterFetchOutcome:
    @patch("app.sports.football.adapters.league_adapter.build_match_outcome")
    @patch("app.sports.football.adapters.league_adapter.query_result")
    def test_fetch_outcome_returns_outcome(self, mock_query, mock_build):
        mock_query.return_value = _make_result()
        mock_build.return_value = MatchOutcome(
            match_id="laliga-123",
            home_score=2,
            away_score=1,
            outcome="home_win",
            finished_at=datetime(2025, 9, 14, 22, 0, tzinfo=timezone.utc),
        )
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        result = adapter.fetch_outcome("laliga-123")
        assert result is not None
        assert result.home_score == 2

    @patch("app.sports.football.adapters.league_adapter.build_match_outcome")
    @patch("app.sports.football.adapters.league_adapter.query_result")
    def test_fetch_outcome_returns_none(self, mock_query, mock_build):
        mock_query.return_value = None
        mock_build.return_value = None
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        result = adapter.fetch_outcome("laliga-nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# TestLeagueAdapterSyncSchedule
# ---------------------------------------------------------------------------

class TestLeagueAdapterSyncSchedule:
    @patch("app.sports.football.adapters.league_adapter.save_fixture")
    @patch("app.sports.football.adapters.league_adapter.parse_fixture")
    @patch("app.sports.football.adapters.league_adapter.fetch_competition_fixtures")
    def test_sync_uses_correct_fd_code(self, mock_fetch, mock_parse, mock_save):
        mock_fetch.return_value = [{"id": 1}]
        mock_parse.return_value = {"match_id": "laliga-1"}
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        count = adapter.sync_schedule()
        assert count == 1
        # Verify FD competition code
        call_args = mock_fetch.call_args
        assert call_args.args[0] == "PD"

    @patch("app.sports.football.adapters.league_adapter.fetch_competition_fixtures")
    def test_sync_failure_returns_zero(self, mock_fetch):
        mock_fetch.side_effect = Exception("API error")
        adapter = LeagueAdapter(_BUNDESLIGA_CONFIG)
        count = adapter.sync_schedule()
        assert count == 0


# ---------------------------------------------------------------------------
# TestLeagueAdapterStubMethods
# ---------------------------------------------------------------------------

class TestLeagueAdapterStubMethods:
    def test_fetch_team_data_returns_empty(self):
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        assert adapter.fetch_team_data(MagicMock()) == {}

    def test_fetch_player_data_returns_empty(self):
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        assert adapter.fetch_player_data(MagicMock()) == {}

    def test_fetch_market_data_returns_empty(self):
        adapter = LeagueAdapter(_LALIGA_CONFIG)
        assert adapter.fetch_market_data(MagicMock()) == {}


# ---------------------------------------------------------------------------
# TestLeagueRegistry
# ---------------------------------------------------------------------------

class TestLeagueRegistry:
    def test_four_prefixes_registered(self):
        assert "laliga-" in LEAGUE_REGISTRY
        assert "bundesliga-" in LEAGUE_REGISTRY
        assert "seriea-" in LEAGUE_REGISTRY
        assert "ligue1-" in LEAGUE_REGISTRY

    def test_each_config_has_non_empty_aliases(self):
        for prefix, cfg in LEAGUE_REGISTRY.items():
            assert len(cfg.team_aliases) > 0, f"{prefix} has empty aliases"

    def test_fd_codes_are_unique(self):
        codes = [cfg.fd_competition for cfg in LEAGUE_REGISTRY.values()]
        assert len(codes) == len(set(codes)), "FD competition codes must be unique"
