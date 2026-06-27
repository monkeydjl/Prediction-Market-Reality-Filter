import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import world_cup_team_stats_service as service


class WorldCupTeamStatsServiceTests(unittest.TestCase):
    def test_get_team_id_uses_api_resolver_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "team_ids.json"
            api_response = {
                "response": [
                    {"team": {"id": 1234, "name": "Belgium", "national": True}},
                    {"team": {"id": 9999, "name": "Club Belgium", "national": False}},
                ]
            }

            with (
                patch.object(service, "TEAM_ID_CACHE_PATH", cache_path),
                patch.object(service, "_api_football_request", return_value=api_response) as request,
            ):
                self.assertEqual(service.get_team_id_from_name("Belgium"), 1234)
                self.assertEqual(service.get_team_id_from_name("Belgium"), 1234)

            request.assert_called_once_with("teams", {"search": "Belgium"})
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(cached["Belgium"], 1234)

    def test_get_team_id_canonicalises_common_fixture_names(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "team_ids.json"
            api_response = {
                "response": [
                    {"team": {"id": 1412, "name": "Cape Verde", "national": True}},
                ]
            }

            with (
                patch.object(service, "TEAM_ID_CACHE_PATH", cache_path),
                patch.object(service, "_api_football_request", return_value=api_response) as request,
            ):
                self.assertEqual(service.get_team_id_from_name("Cape Verde Islands"), 1412)

            request.assert_called_once_with("teams", {"search": "Cape Verde"})
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(cached["Cape Verde"], 1412)
            self.assertEqual(cached["Cape Verde Islands"], 1412)


if __name__ == "__main__":
    unittest.main()
