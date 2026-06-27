import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import world_cup_openfootball_data as service


class WorldCupOpenfootballDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self._write_json("worldcup.teams.json", [
            {
                "name": "France",
                "continent": "Europe",
                "fifa_code": "FRA",
                "group": "I",
                "confed": "UEFA",
            },
            {
                "name": "Bosnia & Herzegovina",
                "continent": "Europe",
                "fifa_code": "BIH",
                "group": "B",
                "confed": "UEFA",
            },
        ])
        self._write_json("worldcup.squads.json", [
            {
                "name": "France",
                "fifa_code": "FRA",
                "group": "I",
                "players": [
                    {"name": "A", "pos": "GK", "date_of_birth": "1996-06-11"},
                    {"name": "B", "pos": "DF", "date_of_birth": "2000-06-11"},
                ],
            },
            {
                "name": "Bosnia & Herzegovina",
                "fifa_code": "BIH",
                "group": "B",
                "players": [
                    {"name": "C", "pos": "FW", "date_of_birth": "1998-06-11"},
                ],
            },
        ])
        self._write_json("worldcup.stadiums.json", {
            "stadiums": [
                {
                    "city": "Paris",
                    "name": "Test Stadium",
                    "cc": "fr",
                    "timezone": "UTC+1",
                    "capacity": 50000,
                }
            ]
        })
        self._write_json("worldcup.json", {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "18:00 UTC+1",
                    "team1": "France",
                    "team2": "Bosnia & Herzegovina",
                    "group": "Group I",
                    "ground": "Paris",
                    "score": {"ft": [9, 9]},
                }
            ]
        })
        service.clear_openfootball_cache()

    def tearDown(self):
        service.clear_openfootball_cache()
        self.tmp.cleanup()

    def _write_json(self, filename, payload):
        (self.data_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_context_resolves_aliases_squad_and_stadium_without_scores(self):
        with patch.dict(os.environ, {"WORLD_CUP_OPENFOOTBALL_DATA_DIR": str(self.data_dir)}):
            context = service.build_openfootball_match_context(
                "France",
                "Bosnia-Herzegovina",
                city="Paris",
                match_date="2026-06-11T18:00:00Z",
            )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["home_team"]["metadata"]["fifa_code"], "FRA")
        self.assertEqual(context["away_team"]["metadata"]["fifa_code"], "BIH")
        self.assertEqual(context["home_team"]["squad"]["player_count"], 2)
        self.assertEqual(context["home_team"]["squad"]["position_counts"], {"GK": 1, "DF": 1})
        self.assertEqual(context["stadium"]["capacity"], 50000)
        self.assertEqual(context["fixture"]["group"], "Group I")
        self.assertNotIn("score", context["fixture"])


if __name__ == "__main__":
    unittest.main()
