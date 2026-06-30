import os
import tempfile
import unittest
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
        self.assertEqual(h2h["matches_played"], 2)
        self.assertEqual(h2h["home_wins"], 1)
        self.assertEqual(h2h["draws"], 1)
        self.assertEqual(h2h["away_wins"], 0)
        self.assertEqual(h2h["avg_goals_home"], 1.0)
        self.assertEqual(h2h["avg_goals_away"], 0.5)
        self.assertEqual(h2h["data_source"], historical.DATA_SOURCE)

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
        self.assertEqual(stats["wins"], 0)
        self.assertEqual(stats["draws"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["recent_results"], ["L", "D"])
        self.assertEqual(stats["data_source"], historical.DATA_SOURCE)


if __name__ == "__main__":
    unittest.main()
