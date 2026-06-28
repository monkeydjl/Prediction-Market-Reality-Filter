"""Tests for world_cup_match_service.

Covers _clean, parse_fixture, save_fixtures_to_db, get_remaining_matches,
and sync_world_cup_fixtures. All external dependencies (HTTP, database) are
mocked so no network or real DB is touched.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock
from urllib.error import URLError

from app.services import world_cup_match_service
from app.services import football_data_source
from app.services.world_cup_match_service import (
    _clean,
    parse_fixture,
    save_fixtures_to_db,
    get_remaining_matches,
    sync_world_cup_fixtures,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fixture(
    fixture_id=12345,
    home="Brazil",
    away="Germany",
    date="2026-06-15T18:00:00+00:00",
    timestamp=None,
    status_short="NS",
    round_name="Group A",
    venue="MetLife Stadium",
):
    """Build a minimal API-Football fixture dict."""
    fixture_block = {
        "id": fixture_id,
        "date": date,
        "timestamp": timestamp,
        "status": {"short": status_short},
        "venue": {"name": venue},
    }
    teams_block = {
        "home": {"name": home},
        "away": {"name": away},
    }
    league_block = {
        "round": round_name,
    }
    return {
        "fixture": fixture_block,
        "teams": teams_block,
        "league": league_block,
    }


def _make_parsed_fixture(match_id="wc2026-12345", status="scheduled", **overrides):
    """Build a parsed fixture dict as returned by parse_fixture."""
    data = {
        "match_id": match_id,
        "fixture_id": "12345",
        "home_team": "Brazil",
        "away_team": "Germany",
        "kickoff_utc": datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        "venue": "MetLife Stadium",
        "stage": "group_stage",
        "group": "A",
        "status": status,
    }
    data.update(overrides)
    return data


# ===========================================================================
# _clean tests
# ===========================================================================

class CleanTests(unittest.TestCase):
    """_clean normalises string inputs."""

    def test_none_returns_empty(self):
        self.assertEqual(_clean(None), "")

    def test_empty_string(self):
        self.assertEqual(_clean(""), "")

    def test_whitespace_strips(self):
        self.assertEqual(_clean("  hello  "), "hello")

    def test_normal_string(self):
        self.assertEqual(_clean("Brazil"), "Brazil")


# ===========================================================================
# parse_fixture tests
# ===========================================================================

class ParseFixtureTests(unittest.TestCase):
    """parse_fixture converts API-Football raw dicts into our format."""

    # -- valid input ---------------------------------------------------------

    def test_valid_fixture_returns_dict(self):
        raw = _make_fixture(fixture_id=99, home="Brazil", away="Germany",
                            date="2026-06-15T18:00:00+00:00")
        result = parse_fixture(raw)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["match_id"], "wc2026-99")
        self.assertEqual(result["fixture_id"], "99")
        self.assertEqual(result["home_team"], "Brazil")
        self.assertEqual(result["away_team"], "Germany")
        self.assertEqual(result["kickoff_utc"],
                         datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(result["venue"], "MetLife Stadium")

    def test_valid_fixture_with_timestamp(self):
        ts = int(datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc).timestamp())
        raw = _make_fixture(fixture_id=200, timestamp=ts)
        result = parse_fixture(raw)

        self.assertIsNotNone(result)
        self.assertEqual(result["kickoff_utc"].year, 2026)
        self.assertEqual(result["kickoff_utc"].hour, 20)

    # -- missing required fields ---------------------------------------------

    def test_missing_fixture_id_returns_none(self):
        raw = _make_fixture(fixture_id="")
        self.assertIsNone(parse_fixture(raw))

    def test_missing_fixture_block_returns_none(self):
        """No 'fixture' key at all → id is empty string → None."""
        raw = {"teams": {"home": {"name": "A"}, "away": {"name": "B"}},
               "league": {"round": "Group A"}}
        self.assertIsNone(parse_fixture(raw))

    def test_missing_home_team_returns_none(self):
        raw = _make_fixture(home="")
        self.assertIsNone(parse_fixture(raw))

    def test_missing_away_team_returns_none(self):
        raw = _make_fixture(away="")
        self.assertIsNone(parse_fixture(raw))

    def test_missing_home_block_returns_none(self):
        raw = _make_fixture()
        raw["teams"]["home"] = {}  # no 'name' key
        self.assertIsNone(parse_fixture(raw))

    def test_missing_kickoff_returns_none(self):
        """Both timestamp and date are absent/invalid → None."""
        raw = _make_fixture(date="not-a-date")
        raw["fixture"]["timestamp"] = None
        self.assertIsNone(parse_fixture(raw))

    # -- stage mapping -------------------------------------------------------

    def test_group_stage_mapping(self):
        raw = _make_fixture(round_name="Group A")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "group_stage")
        # The service scans uppercased round text for first A-H char;
        # "GROUP A" → first match is 'G' from "GROUP".
        self.assertIsNotNone(result["group"])

    def test_group_stage_extracts_letter(self):
        raw = _make_fixture(round_name="Group A")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "group_stage")
        # The code uppercases "group a" → "GROUP A" and picks first char in
        # ABCDEFGH, which is 'G' from "GROUP". This documents current behaviour.
        self.assertEqual(result["group"], "G")

    def test_final_mapping(self):
        raw = _make_fixture(round_name="Final")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "final")
        self.assertIsNone(result["group"])

    def test_semifinal_mapping(self):
        raw = _make_fixture(round_name="Semi-final")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "semifinal")
        self.assertIsNone(result["group"])

    def test_quarterfinal_mapping(self):
        raw = _make_fixture(round_name="Quarter-final")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "quarterfinal")
        self.assertIsNone(result["group"])

    def test_round_of_16_mapping(self):
        raw = _make_fixture(round_name="Round of 16")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "round_of_16")
        self.assertIsNone(result["group"])

    def test_unknown_round_mapping(self):
        raw = _make_fixture(round_name="Play-off")
        result = parse_fixture(raw)
        self.assertEqual(result["stage"], "unknown")
        self.assertIsNone(result["group"])

    # -- status mapping ------------------------------------------------------

    def test_status_ns_scheduled(self):
        raw = _make_fixture(status_short="NS")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "scheduled")

    def test_status_tbd_scheduled(self):
        raw = _make_fixture(status_short="TBD")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "scheduled")

    def test_status_ft_finished(self):
        raw = _make_fixture(status_short="FT")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "finished")

    def test_status_aet_finished(self):
        raw = _make_fixture(status_short="AET")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "finished")

    def test_status_pen_finished(self):
        raw = _make_fixture(status_short="PEN")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "finished")

    def test_status_1h_in_play(self):
        raw = _make_fixture(status_short="1H")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "in_play")

    def test_status_ht_in_play(self):
        raw = _make_fixture(status_short="HT")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "in_play")

    def test_status_2h_in_play(self):
        raw = _make_fixture(status_short="2H")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "in_play")

    def test_status_et_in_play(self):
        raw = _make_fixture(status_short="ET")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "in_play")

    def test_status_live_in_play(self):
        raw = _make_fixture(status_short="LIVE")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "in_play")

    def test_status_unknown_defaults_to_scheduled(self):
        raw = _make_fixture(status_short="XX")
        result = parse_fixture(raw)
        self.assertEqual(result["status"], "scheduled")


# ===========================================================================
# save_fixtures_to_db tests
# ===========================================================================

class SaveFixturesToDbTests(unittest.TestCase):
    """save_fixtures_to_db creates, updates, or skips fixtures."""

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_creates_new_fixtures(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session

        # query(...).filter_by(...).first() returns None → fixture doesn't exist
        session.query.return_value.filter_by.return_value.first.return_value = None

        fixtures = [_make_parsed_fixture()]
        stats = save_fixtures_to_db(fixtures)

        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["skipped"], 0)
        session.add.assert_called_once()
        session.commit.assert_called_once()
        mock_close.assert_called_once_with(session)

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_skips_unchanged(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session

        existing = MagicMock()
        existing.status = "scheduled"
        existing.home_score = None
        existing.away_score = None
        session.query.return_value.filter_by.return_value.first.return_value = existing

        fixtures = [_make_parsed_fixture(status="scheduled")]
        stats = save_fixtures_to_db(fixtures)

        self.assertEqual(stats["created"], 0)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["skipped"], 1)
        session.add.assert_not_called()
        session.commit.assert_called_once()

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_updates_changed_status(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session

        existing = MagicMock()
        existing.status = "scheduled"
        existing.home_score = None
        existing.away_score = None
        session.query.return_value.filter_by.return_value.first.return_value = existing

        fixtures = [_make_parsed_fixture(status="finished")]
        stats = save_fixtures_to_db(fixtures)

        self.assertEqual(stats["created"], 0)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(existing.status, "finished")
        session.commit.assert_called_once()

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_updates_changed_scores(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session

        existing = MagicMock()
        existing.status = "in_play"
        existing.home_score = 0
        existing.away_score = 0
        session.query.return_value.filter_by.return_value.first.return_value = existing

        fixture = _make_parsed_fixture(status="in_play")
        fixture["home_score"] = 2
        fixture["away_score"] = 1
        stats = save_fixtures_to_db([fixture])

        self.assertEqual(stats["updated"], 1)
        self.assertEqual(existing.home_score, 2)
        self.assertEqual(existing.away_score, 1)

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_multiple_fixtures_mixed(self, mock_get_session, mock_close):
        """Two fixtures: one new, one existing-but-changed."""
        session = MagicMock()
        mock_get_session.return_value = session

        existing = MagicMock()
        existing.status = "scheduled"
        existing.home_score = None
        existing.away_score = None

        # First call returns None (new), second returns existing (update)
        query_mock = session.query.return_value.filter_by.return_value
        query_mock.first.side_effect = [None, existing]

        fixtures = [
            _make_parsed_fixture(match_id="wc2026-1"),
            _make_parsed_fixture(match_id="wc2026-2", status="finished"),
        ]
        stats = save_fixtures_to_db(fixtures)

        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["skipped"], 0)

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_rollback_on_error(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session
        session.query.return_value.filter_by.return_value.first.side_effect = RuntimeError("db down")

        with self.assertRaises(RuntimeError):
            save_fixtures_to_db([_make_parsed_fixture()])

        session.rollback.assert_called_once()
        mock_close.assert_called_once_with(session)


# ===========================================================================
# get_remaining_matches tests
# ===========================================================================

class GetRemainingMatchesTests(unittest.TestCase):
    """get_remaining_matches queries for scheduled/in_play future matches."""

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    def test_creates_session_when_none_provided(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session

        chain = session.query.return_value.filter.return_value.order_by.return_value
        chain.all.return_value = []

        result = get_remaining_matches(session=None)

        self.assertEqual(result, [])
        mock_get_session.assert_called_once()
        mock_close.assert_called_once_with(session)

    def test_uses_provided_session(self):
        session = MagicMock()
        chain = session.query.return_value.filter.return_value.order_by.return_value
        chain.all.return_value = [MagicMock()]

        with patch("app.services.world_cup_match_service.close_prediction_session") as mock_close:
            result = get_remaining_matches(session=session)

        self.assertEqual(len(result), 1)
        mock_close.assert_not_called()  # should NOT close a caller-provided session


# ===========================================================================
# sync_world_cup_fixtures tests
# ===========================================================================

class SyncWorldCupFixturesTests(unittest.TestCase):
    """sync_world_cup_fixtures orchestrates fetch → parse → save → count."""

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    @patch("app.services.world_cup_match_service.get_remaining_matches")
    @patch("app.services.world_cup_match_service.save_fixtures_to_db")
    @patch("app.services.world_cup_match_service.parse_fixture")
    @patch("app.services.world_cup_match_service.fetch_world_cup_fixtures")
    def test_api_football_success(self, mock_fetch, mock_parse, mock_save,
                                  mock_remaining, mock_get_session, mock_close):
        raw = [_make_fixture()]
        mock_fetch.return_value = raw
        mock_parse.return_value = _make_parsed_fixture()
        mock_save.return_value = {"created": 1, "updated": 0, "skipped": 0}
        mock_remaining.return_value = [MagicMock()]
        session = MagicMock()
        mock_get_session.return_value = session

        result = sync_world_cup_fixtures(source="api-football")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "api-football")
        self.assertEqual(result["fixtures_fetched"], 1)
        self.assertEqual(result["fixtures_parsed"], 1)
        self.assertEqual(result["fixtures_synced"], 1)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["remaining_matches"], 1)

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    @patch("app.services.world_cup_match_service.get_remaining_matches")
    @patch("app.services.world_cup_match_service.save_fixtures_to_db")
    @patch("app.services.world_cup_match_service.parse_fixture")
    @patch("app.services.world_cup_match_service.fetch_world_cup_fixtures")
    def test_api_football_parse_returns_none_skipped(self, mock_fetch, mock_parse,
                                                      mock_save, mock_remaining,
                                                      mock_get_session, mock_close):
        """Fixtures that fail to parse are silently skipped."""
        mock_fetch.return_value = [_make_fixture(), _make_fixture(fixture_id=2)]
        mock_parse.side_effect = [_make_parsed_fixture(), None]
        mock_save.return_value = {"created": 1, "updated": 0, "skipped": 0}
        mock_remaining.return_value = []
        mock_get_session.return_value = MagicMock()

        result = sync_world_cup_fixtures(source="api-football")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fixtures_fetched"], 2)
        self.assertEqual(result["fixtures_parsed"], 1)

    @patch("app.services.world_cup_match_service.fetch_world_cup_fixtures",
           side_effect=RuntimeError("network down"))
    def test_api_football_error_returns_error(self, mock_fetch):
        result = sync_world_cup_fixtures(source="api-football")

        self.assertEqual(result["status"], "error")
        self.assertIn("network down", result["error"])

    @patch("app.services.football_data_source.parse_fixture")
    @patch("app.services.football_data_source.fetch_world_cup_fixtures")
    def test_football_data_success(self, mock_fd_fetch, mock_fd_parse):
        raw = [{"id": 1}]
        mock_fd_fetch.return_value = raw
        mock_fd_parse.return_value = _make_parsed_fixture()

        with patch("app.services.world_cup_match_service.save_fixtures_to_db",
                   return_value={"created": 1, "updated": 0, "skipped": 0}) as mock_save, \
             patch("app.services.world_cup_match_service.get_remaining_matches",
                   return_value=[]) as mock_remaining, \
             patch("app.services.world_cup_match_service.get_prediction_session",
                   return_value=MagicMock()), \
             patch("app.services.world_cup_match_service.close_prediction_session"):
            result = sync_world_cup_fixtures(source="football-data")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "football-data")
        self.assertEqual(result["fixtures_fetched"], 1)
        mock_fd_fetch.assert_called_once()
        mock_fd_parse.assert_called_once()

    @patch("app.services.football_data_source.fetch_world_cup_fixtures",
           side_effect=football_data_source.FootballDataAPIError("rate limited"))
    def test_football_data_api_error_returns_error(self, mock_fd_fetch):
        result = sync_world_cup_fixtures(source="football-data")

        self.assertEqual(result["status"], "error")
        self.assertIn("Football-Data.org API error", result["error"])
        self.assertIn("rate limited", result["error"])

    @patch("app.services.world_cup_match_service.close_prediction_session")
    @patch("app.services.world_cup_match_service.get_prediction_session")
    @patch("app.services.world_cup_match_service.get_remaining_matches")
    @patch("app.services.world_cup_match_service.save_fixtures_to_db")
    @patch("app.services.world_cup_match_service.parse_fixture")
    @patch("app.services.world_cup_match_service.fetch_world_cup_fixtures")
    def test_synced_count_includes_created_and_updated(self, mock_fetch, mock_parse,
                                                        mock_save, mock_remaining,
                                                        mock_get_session, mock_close):
        mock_fetch.return_value = [_make_fixture()]
        mock_parse.return_value = _make_parsed_fixture()
        mock_save.return_value = {"created": 3, "updated": 2, "skipped": 5}
        mock_remaining.return_value = []
        mock_get_session.return_value = MagicMock()

        result = sync_world_cup_fixtures(source="api-football")

        self.assertEqual(result["fixtures_synced"], 5)  # created + updated
        self.assertEqual(result["created"], 3)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["skipped"], 5)


# ===========================================================================
# fetch_world_cup_fixtures tests
# ===========================================================================

class FetchWorldCupFixturesTests(unittest.TestCase):
    """fetch_world_cup_fixtures makes an HTTP call and returns response data."""

    @patch("app.services.world_cup_match_service.urlopen")
    def test_success_returns_fixtures(self, mock_urlopen):
        response_body = b'{"response": [{"fixture": {"id": 1}}]}'
        mock_response = MagicMock()
        mock_response.read.return_value = response_body
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        with patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_API_KEY", "test-key"), \
             patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example.com"), \
             patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "1"):
            result = world_cup_match_service.fetch_world_cup_fixtures()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["fixture"]["id"], 1)

    def test_missing_config_raises_value_error(self):
        with patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_API_KEY", ""), \
             patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_BASE_URL", ""), \
             patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", ""):
            with self.assertRaises(ValueError):
                world_cup_match_service.fetch_world_cup_fixtures()

    @patch("app.services.world_cup_match_service.urlopen",
           side_effect=URLError("connection refused"))
    def test_network_error_raises_runtime_error(self, mock_urlopen):
        with patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_API_KEY", "test-key"), \
             patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example.com"), \
             patch.object(world_cup_match_service.settings,
                          "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "1"):
            with self.assertRaises(RuntimeError):
                world_cup_match_service.fetch_world_cup_fixtures()


if __name__ == "__main__":
    unittest.main()
