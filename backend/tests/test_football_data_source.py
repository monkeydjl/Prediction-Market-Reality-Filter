import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.football_data_source import (
    FootballDataAPIError,
    import_world_cup_football_data_standings,
)
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts


class _Response:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FootballDataStandingsImportTests(unittest.TestCase):
    def test_import_world_cup_standings_fetches_and_imports_trusted_facts(self):
        payload = {
            "standings": [{
                "stage": "ALL",
                "type": "TOTAL",
                "group": "Group A",
                "table": [{
                    "position": 1,
                    "team": {"name": "Mexico"},
                    "playedGames": 3,
                    "won": 3,
                    "draw": 0,
                    "lost": 0,
                    "points": 9,
                    "goalsFor": 6,
                    "goalsAgainst": 0,
                    "goalDifference": 6,
                }, {
                    "position": 2,
                    "team": {"name": "Germany"},
                    "playedGames": 3,
                    "won": 2,
                    "draw": 0,
                    "lost": 1,
                    "points": 6,
                    "goalsFor": 5,
                    "goalsAgainst": 3,
                    "goalDifference": 2,
                }, {
                    "position": 3,
                    "team": {"name": "Japan"},
                    "playedGames": 3,
                    "won": 1,
                    "draw": 0,
                    "lost": 2,
                    "points": 3,
                    "goalsFor": 3,
                    "goalsAgainst": 5,
                    "goalDifference": -2,
                }, {
                    "position": 4,
                    "team": {"name": "Wales"},
                    "playedGames": 3,
                    "won": 0,
                    "draw": 0,
                    "lost": 3,
                    "points": 0,
                    "goalsFor": 0,
                    "goalsAgainst": 6,
                    "goalDifference": -6,
                }],
            }],
        }

        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", "secret-token"),
                patch.object(settings, "FOOTBALL_DATA_BASE_URL", "https://api.football-data.example/v4"),
                patch(
                    "app.services.football_data_source.httpx.get",
                    return_value=_Response(200, payload),
                ) as mock_get,
            ):
                result = import_world_cup_football_data_standings(replace=True)
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="qualification")

        request = mock_get.call_args
        self.assertEqual(
            request.args[0],
            "https://api.football-data.example/v4/competitions/WC/standings",
        )
        self.assertEqual(request.kwargs["headers"]["X-Auth-Token"], "secret-token")
        self.assertEqual(result["provider"], "football_data")
        self.assertEqual(result["source_url"], "https://api.football-data.example/v4/competitions/WC/standings")
        self.assertEqual(result["normalized_qualification_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(len(facts), 4)
        self.assertTrue(all(fact.get("source_url", "").startswith("https://") for fact in facts))
        self.assertTrue(all(fact.get("observed_at") for fact in facts))
        self.assertNotIn("secret-token", str(result))

    def test_import_world_cup_standings_requires_api_key(self):
        with patch.object(settings, "FOOTBALL_DATA_API_KEY", ""):
            with self.assertRaisesRegex(FootballDataAPIError, "FOOTBALL_DATA_API_KEY"):
                import_world_cup_football_data_standings()


if __name__ == "__main__":
    unittest.main()
