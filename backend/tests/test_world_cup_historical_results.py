import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.services import world_cup_historical_results as historical


class WorldCupHistoricalResultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.results_path = Path(self.tmp.name) / "results.csv"
        self.results_path.write_text(
            "\n".join([
                "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral",
                "2025-01-01,France,Belgium,2,1,Friendly,Paris,France,FALSE",
                "2025-02-01,Belgium,France,0,0,Friendly,Brussels,Belgium,FALSE",
                "2025-02-15,France,Belgium,1,0,Friendly,Paris,France,TRUE",
                "2025-03-01,France,Spain,1,3,Friendly,Paris,France,FALSE",
            ]),
            encoding="utf-8",
        )
        historical._load_results.cache_clear()

    def tearDown(self):
        historical._load_results.cache_clear()
        self.tmp.cleanup()

    def test_historical_h2h_uses_requested_home_team_perspective(self):
        with patch.dict(os.environ, {"WORLD_CUP_HISTORICAL_RESULTS_FILE": str(self.results_path)}):
            h2h = historical.get_historical_h2h("France", "Belgium", before_date="2025-12-01")

        self.assertIsNotNone(h2h)
        assert h2h is not None
        self.assertEqual(h2h["matches_played"], 3)
        self.assertEqual(h2h["home_wins"], 2)
        self.assertEqual(h2h["draws"], 1)
        self.assertEqual(h2h["away_wins"], 0)
        self.assertEqual(h2h["avg_goals_home"], 1.0)
        self.assertEqual(h2h["avg_goals_away"], 0.33)
        self.assertEqual(h2h["data_source"], historical.DATA_SOURCE)

    def test_historical_meetings_exclude_neutral_match_from_home_venue(self):
        with patch.dict(os.environ, {"WORLD_CUP_HISTORICAL_RESULTS_FILE": str(self.results_path)}):
            meetings = historical.historical_h2h_meetings(
                "France", "Belgium", before_date="2025-12-01",
            )

        assert len(meetings) == 3
        assert sum(meeting.current_home_hosted for meeting in meetings) == 1

    def test_historical_team_stats_build_recent_form(self):
        with patch.dict(os.environ, {"WORLD_CUP_HISTORICAL_RESULTS_FILE": str(self.results_path)}):
            stats = historical.get_historical_team_stats(
                "France",
                before_date="2025-12-01",
                max_matches=2,
            )

        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats["played"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["draws"], 0)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["recent_results"], ["L", "W"])
        self.assertEqual(stats["data_source"], historical.DATA_SOURCE)


class InternationalMatchDatesTests(unittest.TestCase):
    """P1-F2: real international match days behind a national-team fixture."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.results_path = Path(self.tmp.name) / "results.csv"
        self.results_path.write_text(
            "\n".join([
                "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral",
                "2025-02-20,France,Italy,1,1,Friendly,Paris,France,FALSE",
                "2025-03-01,France,Spain,1,3,FIFA World Cup qualification,Paris,France,FALSE",
                "2025-03-05,Portugal,France,0,1,Friendly,Lisbon,Portugal,FALSE",
                "2025-03-08,France,Germany,2,2,UEFA Nations League,Paris,France,FALSE",
                "2025-03-06,Czechia,Spain,0,2,Friendly,Prague,Czechia,FALSE",
            ]),
            encoding="utf-8",
        )
        historical._load_results.cache_clear()

    def tearDown(self):
        historical._load_results.cache_clear()
        self.tmp.cleanup()

    def _dates(self, team, before, days):
        with patch.dict(os.environ, {"WORLD_CUP_HISTORICAL_RESULTS_FILE": str(self.results_path)}):
            return historical.international_match_dates(
                team, before_date=before, window_days=days,
            )

    def test_window_covers_qualifiers_and_friendlies_regardless_of_venue(self):
        assert self._dates("France", "2025-03-08", 7) == (
            date(2025, 3, 1), date(2025, 3, 5),
        )

    def test_narrow_window_drops_older_match_days(self):
        assert self._dates("France", "2025-03-08", 3) == (date(2025, 3, 5),)

    def test_fixture_own_date_is_excluded(self):
        # A national team plays at most once a day, so the 2025-03-08 row is the
        # fixture itself and must not count as a prior match.
        assert date(2025, 3, 8) not in self._dates("France", "2025-03-08", 30)

    def test_alias_resolves_to_csv_spelling(self):
        assert self._dates("Czech Republic", "2025-03-08", 7) == (date(2025, 3, 6),)

    def test_window_boundary_is_inclusive(self):
        assert self._dates("France", "2025-02-27", 7) == (date(2025, 2, 20),)
        assert self._dates("France", "2025-02-28", 7) == ()

    def test_unknown_team_and_missing_cutoff_return_empty(self):
        assert self._dates("NotANationalTeamXYZ", "2025-03-08", 7) == ()
        assert self._dates("France", None, 7) == ()

    def test_zero_window_returns_no_prior_days(self):
        assert self._dates("France", "2025-03-08", 0) == ()


if __name__ == "__main__":
    unittest.main()
