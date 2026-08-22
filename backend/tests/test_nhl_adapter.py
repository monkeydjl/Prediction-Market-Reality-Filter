# backend/tests/test_nhl_adapter.py
"""Tests for NHLAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.hockey.nhl_adapter import (
    NHLAdapter,
    parse_nhl_game,
    _nhl_team_abbrev,
)


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_match(match_id="nhl-2023020001") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_NHL, season_key="20232024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NJD", name="New Jersey Devils", competition=_NHL),
        away=TeamIdentity(code="NYR", name="New York Rangers", competition=_NHL),
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="nhl-2023020001", home="New Jersey Devils", away="New York Rangers"):
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "nhl"
    fixture.season = "20232024"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 1, 15, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "Prudential Center"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestNHLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = NHLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseNhlGame:
    def test_parses_regular_season_final_game(self):
        """parse_nhl_game maps API fields to internal fixture format."""
        raw = {
            "id": 2023020001,
            "season": 20232024,
            "gameDate": "2024-01-15T00:00:00Z",
            "homeTeam": {"id": 1, "name": "New Jersey Devils", "abbrev": "NJD"},
            "awayTeam": {"id": 2, "name": "New York Rangers", "abbrev": "NYR"},
            "gameState": "OFF FINAL",
            "homeTeamScore": 3,
            "awayTeamScore": 2,
            "gameType": 2,  # 2 = regular season
        }
        parsed = parse_nhl_game(raw)
        assert parsed["match_id"] == "nhl-2023020001"
        assert parsed["home_team"] == "New Jersey Devils"
        assert parsed["away_team"] == "New York Rangers"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"
        assert parsed["home_score"] == 3
        assert parsed["away_score"] == 2

    def test_parses_playoff_game_with_overtime(self):
        """Playoff game maps to 'playoff'; overtime/shootout flags captured."""
        raw = {
            "id": 2023030111,
            "season": 20232024,
            "gameDate": "2024-04-20T00:00:00Z",
            "homeTeam": {"id": 1, "name": "New Jersey Devils", "abbrev": "NJD"},
            "awayTeam": {"id": 2, "name": "New York Rangers", "abbrev": "NYR"},
            "gameState": "OFF FINAL",
            "homeTeamScore": 4,
            "awayTeamScore": 3,
            "gameType": 3,  # 3 = playoffs
            "period": 4,  # OT
        }
        parsed = parse_nhl_game(raw)
        assert parsed["match_id"] == "nhl-2023030111"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "finished"
        assert parsed["went_to_overtime"] is True
        assert parsed["went_to_shootout"] is False

    def test_parses_official_nested_place_common_name(self):
        """api-web.nhle.com uses placeName + commonName + team.score."""
        raw = {
            "id": 2025020004,
            "season": 20252026,
            "startTimeUTC": "2025-10-08T23:00:00Z",
            "gameType": 2,
            "gameState": "OFF",
            "venue": {"default": "Scotiabank Arena"},
            "homeTeam": {
                "id": 10,
                "commonName": {"default": "Maple Leafs"},
                "placeName": {"default": "Toronto"},
                "abbrev": "TOR",
                "score": 5,
            },
            "awayTeam": {
                "id": 8,
                "commonName": {"default": "Canadiens"},
                "placeName": {"default": "Montréal"},
                "abbrev": "MTL",
                "score": 2,
            },
            "periodDescriptor": {
                "number": 3,
                "periodType": "REG",
                "maxRegulationPeriods": 3,
            },
            "gameOutcome": {"lastPeriodType": "REG"},
        }
        parsed = parse_nhl_game(raw)
        assert parsed is not None
        assert parsed["match_id"] == "nhl-2025020004"
        assert parsed["home_team"] == "Toronto Maple Leafs"
        assert parsed["away_team"] == "Montreal Canadiens"
        assert parsed["home_score"] == 5
        assert parsed["away_score"] == 2
        assert parsed["status"] == "finished"
        assert parsed["stage"] == "regular_season"
        assert parsed["venue"] == "Scotiabank Arena"
        assert parsed["went_to_overtime"] is False

    def test_parses_ot_from_period_descriptor(self):
        raw = {
            "id": 2025020999,
            "startTimeUTC": "2025-11-01T00:00:00Z",
            "gameType": 2,
            "gameState": "FINAL",
            "homeTeam": {
                "placeName": {"default": "Boston"},
                "commonName": {"default": "Bruins"},
                "score": 3,
            },
            "awayTeam": {
                "placeName": {"default": "Buffalo"},
                "commonName": {"default": "Sabres"},
                "score": 2,
            },
            "periodDescriptor": {"number": 4, "periodType": "OT"},
            "gameOutcome": {"lastPeriodType": "OT"},
        }
        parsed = parse_nhl_game(raw)
        assert parsed["went_to_overtime"] is True
        assert parsed["went_to_shootout"] is False
        assert parsed["status"] == "finished"

    def test_canonicalizes_utah_hockey_club_variants(self):
        """Utah franchise renames / bad concatenations collapse to Utah Mammoth."""
        nested = {
            "id": 2024020115,
            "season": 20242025,
            "startTimeUTC": "2024-11-01T02:00:00Z",
            "gameType": 2,
            "gameState": "OFF",
            "homeTeam": {
                "placeName": {"default": "Utah"},
                "commonName": {"default": "Utah Hockey Club"},
                "abbrev": "UTA",
                "score": 2,
            },
            "awayTeam": {
                "placeName": {"default": "Colorado"},
                "commonName": {"default": "Avalanche"},
                "abbrev": "COL",
                "score": 1,
            },
        }
        parsed = parse_nhl_game(nested)
        assert parsed is not None
        assert parsed["home_team"] == "Utah Mammoth"
        assert parsed["away_team"] == "Colorado Avalanche"

        flat = {
            "id": 2024020116,
            "season": 20242025,
            "gameDate": "2024-11-02T00:00:00Z",
            "gameType": 2,
            "gameState": "FINAL",
            "homeTeam": {"name": "Utah Hockey Club", "abbrev": "UTA"},
            "awayTeam": {"name": "Dallas Stars", "abbrev": "DAL"},
            "homeTeamScore": 3,
            "awayTeamScore": 4,
        }
        parsed_flat = parse_nhl_game(flat)
        assert parsed_flat is not None
        assert parsed_flat["home_team"] == "Utah Mammoth"

        mammoth = {
            "id": 2026020001,
            "season": 20262027,
            "startTimeUTC": "2026-10-10T01:00:00Z",
            "gameType": 2,
            "gameState": "FUT",
            "homeTeam": {
                "placeName": {"default": "Utah"},
                "commonName": {"default": "Mammoth"},
                "abbrev": "UTA",
            },
            "awayTeam": {
                "placeName": {"default": "Vegas"},
                "commonName": {"default": "Golden Knights"},
                "abbrev": "VGK",
            },
        }
        parsed_m = parse_nhl_game(mammoth)
        assert parsed_m is not None
        assert parsed_m["home_team"] == "Utah Mammoth"

        coyotes = {
            "id": 2023020500,
            "season": 20232024,
            "gameDate": "2024-03-01T00:00:00Z",
            "gameType": 2,
            "gameState": "FINAL",
            "homeTeam": {"name": "Arizona Coyotes", "abbrev": "ARI"},
            "awayTeam": {"name": "Colorado Avalanche", "abbrev": "COL"},
            "homeTeamScore": 2,
            "awayTeamScore": 3,
        }
        parsed_c = parse_nhl_game(coyotes)
        assert parsed_c is not None
        assert parsed_c["home_team"] == "Utah Mammoth"


class TestNhlTeamAbbrev:
    def test_maps_utah_and_coyotes_to_uta(self):
        assert _nhl_team_abbrev("Utah Mammoth") == "UTA"
        assert _nhl_team_abbrev("Utah Hockey Club") == "UTA"
        assert _nhl_team_abbrev("Arizona Coyotes") == "UTA"
        assert _nhl_team_abbrev("Colorado Avalanche") == "COL"


class TestNHLAdapterStartingGoalies:
    @patch("app.sports.hockey.nhl_adapter.fetch_nhl_club_stats")
    def test_fetch_starting_goalies_from_club_stats(self, mock_stats):
        mock_stats.side_effect = [
            {
                "goalies": [
                    {
                        "playerId": 1,
                        "firstName": {"default": "Scott"},
                        "lastName": {"default": "Wedgewood"},
                        "gamesStarted": 43,
                        "gamesPlayed": 45,
                        "savePercentage": 0.921317,
                    }
                ]
            },
            {
                "goalies": [
                    {
                        "playerId": 2,
                        "firstName": {"default": "Karel"},
                        "lastName": {"default": "Vejmelka"},
                        "gamesStarted": 63,
                        "gamesPlayed": 64,
                        "savePercentage": 0.896679,
                    }
                ]
            },
        ]
        adapter = NHLAdapter()
        match = MatchIdentity(
            match_id="nhl-2026010012",
            season=SeasonIdentity(competition=_NHL, season_key="20262027"),
            stage="regular_season",
            round=None,
            home=TeamIdentity(code="COL", name="Colorado Avalanche", competition=_NHL),
            away=TeamIdentity(code="UTA", name="Utah Mammoth", competition=_NHL),
            kickoff_utc=datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc),
        )
        goalies = adapter._fetch_starting_goalies(match)
        assert goalies["home"]["name"] == "Scott Wedgewood"
        assert abs(goalies["home"]["save_pct"] - 0.921317) < 1e-6
        assert goalies["away"]["name"] == "Karel Vejmelka"
        assert abs(goalies["away"]["save_pct"] - 0.896679) < 1e-6
        assert mock_stats.call_args_list[0].args[0] == "COL"
        assert mock_stats.call_args_list[1].args[0] == "UTA"

    @patch("app.sports.hockey.nhl_adapter.fetch_nhl_club_stats")
    def test_fetch_all_data_writes_goalie_save_pct_from_live_path(self, mock_stats):
        mock_stats.return_value = {
            "goalies": [
                {
                    "firstName": {"default": "Igor"},
                    "lastName": {"default": "Shesterkin"},
                    "gamesStarted": 50,
                    "gamesPlayed": 50,
                    "savePercentage": 0.915,
                    "goalsAgainst": 100,
                    "shotsAgainst": 1200,
                }
            ],
            "skaters": [
                {"goals": 200, "shots": 2400, "gamesPlayed": 80},
            ],
        }
        adapter = NHLAdapter()
        with patch.object(
            adapter,
            "_fetch_elo_ratings",
            return_value={"New Jersey Devils": 1510.0, "New York Rangers": 1495.0},
        ), patch.object(adapter, "_compute_form", return_value=0.5), patch.object(
            adapter, "_compute_rest_days", return_value=2.0
        ):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["custom"]["goalie_save_pct_home"] == 0.915
            assert raw["custom"]["goalie_save_pct_away"] == 0.915
            assert raw["player"]["starting_goalie_home"] == "Igor Shesterkin"
            # Real club rates (not form soft proxy 2.9/3.1)
            assert abs(raw["custom"]["team_gf_home"] - 200 / 80) < 1e-4
            assert abs(raw["custom"]["team_ga_home"] - 100 / 80) < 1e-4
            assert raw["custom"]["corsi_pct_home"] is not None
            assert raw["custom"]["corsi_pct_away"] is not None
            assert abs(raw["custom"]["xg_for_home"] - (2400 / 80) * 0.09) < 1e-4


class TestNHLAdapterLive5v5Provider:
    """P1-H1 residual: measured 5v5 xG/corsi ahead of the club-stats proxies."""

    _SIDE_HOME = {
        "name": "Igor Shesterkin",
        "save_pct": 0.912,
        "rates": {
            "games": 80, "gf_per_game": 3.1, "ga_per_game": 2.8,
            "sf_per_game": 30.0, "sa_per_game": 28.0, "shot_share": 0.517,
        },
    }
    _SIDE_AWAY = {
        "name": "Juuse Saros",
        "save_pct": 0.920,
        "rates": {
            "games": 80, "gf_per_game": 2.9, "ga_per_game": 2.7,
            "sf_per_game": 29.0, "sa_per_game": 29.5, "shot_share": 0.496,
        },
    }

    @classmethod
    def _run(cls, live_by_team, *, sides=None):
        """Enrich with the live provider stubbed per team name.

        ``live_by_team`` maps a team name to its LiveNhl5v5, or is a callable
        used as the patch side effect for failure cases.
        """
        from app.services.nhl_live_xg_service import LiveNhl5v5

        side_effect = (
            live_by_team
            if callable(live_by_team)
            else lambda _season, name: live_by_team.get(name, LiveNhl5v5(available=False))
        )
        adapter = NHLAdapter()
        with patch.object(adapter, "_fetch_elo_ratings", return_value={}), \
             patch.object(adapter, "_fetch_club_side",
                          side_effect=list(sides or [cls._SIDE_HOME, cls._SIDE_AWAY])), \
             patch.object(adapter, "_compute_form", return_value=0.5), \
             patch.object(adapter, "_compute_rest_days", return_value=2.0), \
             patch(
                 "app.services.nhl_live_xg_service.get_live_5v5_metrics",
                 side_effect=side_effect,
             ):
            return adapter.fetch_all_data(_make_match())

    @staticmethod
    def _live(**metrics):
        from app.services.nhl_live_xg_service import LiveNhl5v5

        return LiveNhl5v5(available=True, metrics={"toi_minutes": 1000.0, **metrics})

    def test_live_corsi_and_xg_replace_the_proxies(self):
        raw = self._run({
            "New Jersey Devils": self._live(xgf_per_60=2.85, corsi_pct=0.545),
            "New York Rangers": self._live(xgf_per_60=2.40, corsi_pct=0.478),
        })
        custom = raw["custom"]
        assert custom["corsi_pct_home"] == pytest.approx(0.545)
        assert custom["corsi_pct_away"] == pytest.approx(0.478)
        assert custom["xg_for_home"] == pytest.approx(2.85)
        assert custom["xg_for_away"] == pytest.approx(2.40)
        assert custom["skating_source"] == "live_provider"

    def test_live_xg_only_clears_the_shots_on_goal_proxy(self):
        # HockeyEngine prefers corsi over xG, so leaving the shots-on-goal share
        # in place would make the measured xG unreachable.
        raw = self._run({
            "New Jersey Devils": self._live(xgf_per_60=2.85),
            "New York Rangers": self._live(xgf_per_60=2.40),
        })
        custom = raw["custom"]
        assert custom["xg_for_home"] == pytest.approx(2.85)
        assert custom["xg_for_away"] == pytest.approx(2.40)
        assert custom["corsi_pct_home"] is None
        assert custom["corsi_pct_away"] is None
        assert custom["skating_source"] == "live_provider"

    def test_live_corsi_only_keeps_the_shots_derived_xg(self):
        raw = self._run({
            "New Jersey Devils": self._live(corsi_pct=0.545),
            "New York Rangers": self._live(corsi_pct=0.478),
        })
        custom = raw["custom"]
        assert custom["corsi_pct_home"] == pytest.approx(0.545)
        # The corsi branch wins in the engine, so the SF-derived xG stays as-is.
        assert custom["xg_for_home"] == pytest.approx(30.0 * 0.09)
        assert custom["skating_source"] == "live_provider"

    def test_one_live_side_preserves_the_proxies(self):
        # A measured 5v5 rate against a shots-on-goal proxy is not comparable.
        raw = self._run({
            "New Jersey Devils": self._live(xgf_per_60=2.85, corsi_pct=0.545),
        })
        custom = raw["custom"]
        assert custom["corsi_pct_home"] == pytest.approx(0.517)
        assert custom["corsi_pct_away"] == pytest.approx(0.496)
        assert custom["xg_for_home"] == pytest.approx(30.0 * 0.09)
        assert custom["skating_source"] == "club_stats_proxy"

    def test_each_metric_pair_needs_both_sides(self):
        raw = self._run({
            "New Jersey Devils": self._live(xgf_per_60=2.85),
            "New York Rangers": self._live(corsi_pct=0.478),
        })
        custom = raw["custom"]
        assert custom["corsi_pct_home"] == pytest.approx(0.517)
        assert custom["corsi_pct_away"] == pytest.approx(0.496)
        assert custom["xg_for_home"] == pytest.approx(30.0 * 0.09)
        assert custom["skating_source"] == "club_stats_proxy"

    def test_reached_provider_without_metrics_preserves_the_proxies(self):
        from app.services.nhl_live_xg_service import LiveNhl5v5

        raw = self._run({
            "New Jersey Devils": LiveNhl5v5(available=True, metrics=None),
            "New York Rangers": LiveNhl5v5(available=True, metrics=None),
        })
        custom = raw["custom"]
        assert custom["corsi_pct_home"] == pytest.approx(0.517)
        assert custom["xg_for_away"] == pytest.approx(29.0 * 0.09)
        assert custom["skating_source"] == "club_stats_proxy"

    def test_provider_exception_preserves_the_proxies(self):
        def boom(_season, _name):
            raise RuntimeError("provider down")

        raw = self._run(boom)
        custom = raw["custom"]
        assert custom["corsi_pct_home"] == pytest.approx(0.517)
        assert custom["xg_for_home"] == pytest.approx(30.0 * 0.09)
        assert custom["skating_source"] == "club_stats_proxy"

    def test_missing_club_rates_report_a_soft_form_source(self):
        raw = self._run({}, sides=[{}, {}])
        custom = raw["custom"]
        assert custom["corsi_pct_home"] is None
        assert custom["skating_source"] == "soft_form"

    def test_season_key_is_passed_to_the_provider(self):
        from app.services.nhl_live_xg_service import LiveNhl5v5

        seen: list = []

        def record(season, _name):
            seen.append(season)
            return LiveNhl5v5(available=False)

        self._run(record)
        assert seen == ["20232024", "20232024"]


class TestNHLAdapterGetMatchIdentity:
    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = NHLAdapter()
        identity = adapter.get_match_identity("nhl-2023020001")
        assert identity.match_id == "nhl-2023020001"
        assert identity.home.name == "New Jersey Devils"
        assert identity.away.name == "New York Rangers"
        assert identity.season.competition.code == "nhl"

    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = NHLAdapter()
        identity = adapter.get_match_identity("nhl-nonexistent")
        assert identity.match_id == "nhl-nonexistent"
        assert identity.home.name == "Home"


class TestNHLAdapterFetchAllData:
    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_fetch_all_data_includes_goalie_save_pct(self, mock_query):
        """fetch_all_data writes goalie save% into raw['custom']."""
        mock_query.return_value = _make_fixture()

        adapter = NHLAdapter()
        side_home = {
            "name": "Igor Shesterkin",
            "save_pct": 0.912,
            "rates": {
                "games": 80,
                "gf_per_game": 3.1,
                "ga_per_game": 2.8,
                "sf_per_game": 30.0,
                "sa_per_game": 28.0,
                "shot_share": 0.517,
            },
        }
        side_away = {
            "name": "Juuse Saros",
            "save_pct": 0.920,
            "rates": {
                "games": 80,
                "gf_per_game": 2.9,
                "ga_per_game": 2.7,
                "sf_per_game": 29.0,
                "sa_per_game": 29.5,
                "shot_share": 0.496,
            },
        }
        with patch.object(adapter, "_fetch_elo_ratings",
                          return_value={"New Jersey Devils": 1510.0, "New York Rangers": 1495.0}), \
             patch.object(adapter, "_fetch_club_side",
                          side_effect=[side_home, side_away]), \
             patch.object(adapter, "_compute_form", return_value=0.5), \
             patch.object(adapter, "_compute_rest_days", return_value=2.0):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1510.0
            assert raw["team"]["elo_away"] == 1495.0
            assert raw["environment"]["is_home_advantage"] is True
            # Goalie stats in custom dict
            assert raw["custom"]["goalie_save_pct_home"] == 0.912
            assert raw["custom"]["goalie_save_pct_away"] == 0.920
            assert raw["custom"]["team_gf_home"] == 3.1
            assert raw["custom"]["corsi_pct_home"] == 0.517
            # Overtime defaults (False for fresh game)
            assert raw["custom"]["went_to_overtime"] is False
            assert raw["custom"]["went_to_shootout"] is False


class TestNHLAdapterFetchOutcome:
    @patch("app.sports.hockey.nhl_adapter.build_match_outcome")
    @patch("app.sports.hockey.nhl_adapter.query_result")
    def test_fetch_outcome_returns_binary_outcome(self, mock_query, mock_build):
        """fetch_outcome returns binary outcome even for OT/shootout games."""
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="nhl-2023020001",
            home_score=3, away_score=2,
            outcome="home_win",  # binary — no "overtime_win"
            finished_at=datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc),
        )
        adapter = NHLAdapter()
        result = adapter.fetch_outcome("nhl-2023020001")
        assert result is not None
        assert result.home_score == 3
        assert result.outcome == "home_win"


class TestNHLAdapterSyncSchedule:
    @patch("app.sports.hockey.nhl_adapter.save_fixture")
    @patch("app.sports.hockey.nhl_adapter.fetch_nhl_schedule")
    @patch("app.sports.hockey.nhl_adapter.config.settings")
    def test_sync_uses_preferred_then_fallback_season(
        self, mock_settings, mock_fetch, mock_save
    ):
        mock_settings.PHASE5_NHL_ENABLED = True
        mock_fetch.side_effect = [
            [],
            [
                {
                    "id": 2025020001,
                    "season": 20252026,
                    "startTimeUTC": "2025-10-08T23:00:00Z",
                    "gameType": 2,
                    "gameState": "FUT",
                    "homeTeam": {
                        "placeName": {"default": "Toronto"},
                        "commonName": {"default": "Maple Leafs"},
                    },
                    "awayTeam": {
                        "placeName": {"default": "Montreal"},
                        "commonName": {"default": "Canadiens"},
                    },
                }
            ],
        ]
        adapter = NHLAdapter()
        with patch.object(
            adapter,
            "_season_candidates",
            return_value=["20262027", "20252026"],
        ):
            count = adapter.sync_schedule()
        assert count == 1
        assert mock_fetch.call_args_list[0].args[0] == "20262027"
        assert mock_fetch.call_args_list[1].args[0] == "20252026"
        mock_save.assert_called_once()
        assert mock_save.call_args.args[1] == "nhl"
        assert mock_save.call_args.args[2] == "20252026"

    @patch("app.sports.hockey.nhl_adapter.config.settings")
    def test_sync_disabled_returns_zero(self, mock_settings):
        mock_settings.PHASE5_NHL_ENABLED = False
        assert NHLAdapter().sync_schedule() == 0


class TestNHLAdapterMarketTotalsWiring:
    """P1-O1 真盘口: the NHL adapter must actually reach the provider."""

    @staticmethod
    def _run(result=None, **kwargs):
        adapter = NHLAdapter()
        with patch.object(adapter, "_fetch_elo_ratings", return_value={}), \
             patch.object(adapter, "_fetch_club_side", side_effect=[{}, {}]), \
             patch.object(adapter, "_compute_form", return_value=0.5), \
             patch.object(adapter, "_compute_rest_days", return_value=2.0), \
             patch(
                 "app.services.market_totals_service.get_market_total",
                 return_value=result,
                 **kwargs,
             ) as provider:
            return adapter.fetch_all_data(_make_match()), provider

    def test_available_line_reaches_custom(self):
        from app.services.market_totals_service import MarketTotal

        raw, provider = self._run(MarketTotal(
            available=True, total={"total_line": 6.5, "market_p_over": 0.49},
        ))
        assert raw["custom"]["market_total_line"] == pytest.approx(6.5)
        assert raw["custom"]["market_total_p_over"] == pytest.approx(0.49)
        assert provider.call_args.args == (
            "hockey", "2024-01-15", "New Jersey Devils", "New York Rangers",
        )

    def test_unavailable_provider_writes_nothing(self):
        from app.services.market_totals_service import MarketTotal

        raw, _ = self._run(MarketTotal(available=False))
        assert "market_total_line" not in raw["custom"]

    def test_provider_exception_does_not_break_enrichment(self):
        raw, _ = self._run(side_effect=RuntimeError("provider down"))
        assert "market_total_line" not in raw["custom"]
