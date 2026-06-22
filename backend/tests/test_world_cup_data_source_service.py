import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import sports_resolution_service as resolver
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_data_source_service import (
    import_world_cup_data_file,
    load_world_cup_data_file,
    import_world_cup_data,
    preview_world_cup_data_file,
    validate_world_cup_data_source_metadata,
    world_cup_data_file_to_facts,
    world_cup_data_to_facts,
)
from tests.test_sports_resolution_service import _sports_record


def _observed_at_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WorldCupDataSourceServiceTests(unittest.TestCase):
    def test_converts_match_snapshot_to_structured_facts(self):
        facts = world_cup_data_to_facts({
            "source": "official_feed",
            "observed_at": "2026-07-20T00:00:00Z",
            "matches": [{
                "match_id": "round16-1",
                "stage": "round_of_16",
                "kickoff_at": "2026-07-20T19:00:00+00:00",
                "venue": "Stadium A, City A",
                "referee": "Referee A",
                "home_team": "Team A",
                "away_team": "Team B",
                "status": "finished",
                "home_score": 1,
                "away_score": 1,
                "winner": "Team A",
                "extra_time": True,
                "penalty_shootout": True,
                "home_red_cards": 1,
                "away_red_cards": 0,
            }],
            "qualifications": [{
                "team": "Mexico",
                "status": "qualified",
                "stage": "round_of_32",
                "already_qualified": True,
            }],
            "discipline": [{
                "match_id": "round16-1",
                "team": "Team A",
                "player": "Player A",
                "minute": "72+1",
                "status": "red_card",
                "red_cards": 1,
            }],
            "player_awards": [{
                "award": "golden_boot",
                "player": "Player A",
                "goals": 7,
                "rank": 1,
                "status": "current",
            }],
            "player_statuses": [{
                "kind": "injury",
                "team": "Brazil",
                "player": "Player B",
                "status": "out",
                "severity": "high",
                "reason": "hamstring",
            }],
            "team_stats": [{
                "team": "Team A",
                "match_id": "round16-1",
                "stat_name": "ball possession",
                "stat_value": 60,
                "stat_unit": "%",
            }],
            "player_stats": [{
                "team": "Team A",
                "player": "Player A",
                "match_id": "round16-1",
                "stat_name": "shots.total",
                "stat_value": 3,
            }],
            "tournament_status": {
                "status": "complete",
                "tournament_complete": True,
            },
        })

        by_kind = {fact["kind"]: fact for fact in facts}
        self.assertEqual(by_kind["match_result"]["fact_id"], "wc2026:match:round16-1")
        self.assertEqual(by_kind["match_result"]["kickoff_at"], "2026-07-20T19:00:00+00:00")
        self.assertEqual(by_kind["match_result"]["venue"], "Stadium A, City A")
        self.assertEqual(by_kind["match_result"]["referee"], "Referee A")
        self.assertEqual(by_kind["match_result"]["score"], {"home": 1, "away": 1})
        self.assertEqual(by_kind["match_result"]["red_cards"], 1.0)
        self.assertTrue(by_kind["match_result"]["penalty_shootout"])
        self.assertEqual(by_kind["discipline"]["match_id"], "round16-1")
        self.assertEqual(by_kind["discipline"]["minute"], "72+1")
        self.assertEqual(by_kind["discipline"]["red_cards"], 1.0)
        self.assertEqual(by_kind["qualification"]["team"], "Mexico")
        self.assertEqual(by_kind["player_award"]["goals"], 7)
        self.assertEqual(by_kind["injury"]["player"], "Player B")
        self.assertEqual(by_kind["injury"]["notes"], "hamstring")
        self.assertEqual(by_kind["team_stat"]["stat_value"], 60.0)
        self.assertEqual(by_kind["team_stat"]["stat_unit"], "%")
        self.assertEqual(by_kind["player_stat"]["player"], "Player A")
        self.assertTrue(by_kind["tournament_status"]["tournament_complete"])

    def test_converts_csv_sections_to_structured_facts(self):
        facts = world_cup_data_to_facts({
            "source": "official_csv",
            "csv": {
                "matches": (
                    "match_id,stage,kickoff_at,venue,referee,home_team,away_team,status,"
                    "home_score,away_score,"
                    "extra_time,penalty_shootout,home_red_cards,away_red_cards\n"
                    "round16-1,round_of_16,2026-07-20T19:00:00+00:00,"
                    "\"Stadium A, City A\",Referee A,Team A,Team B,finished,1,1,"
                    "false,true,1,0\n"
                ),
                "qualifications": (
                    "team,status,stage,already_qualified,already_eliminated\n"
                    "Mexico,qualified,round_of_32,true,false\n"
                ),
                "discipline": (
                    "match_id,team,player,minute,status,red_cards,yellow_cards\n"
                    "round16-1,Team A,Player A,72+1,red_card,1,0\n"
                ),
                "player_awards": (
                    "award,player,team,goals,rank,status\n"
                    "golden_boot,Player A,Team A,7,1,current\n"
                ),
                "team_stats": (
                    "team,match_id,stage,stat_name,stat_value,stat_unit\n"
                    "Team A,round16-1,round_of_16,ball possession,60,%\n"
                ),
                "player_stats": (
                    "team,player,match_id,stage,position,jersey_number,stat_name,stat_value,stat_unit\n"
                    "Team A,Player A,round16-1,round_of_16,F,10,shots.total,3,\n"
                ),
            },
            "tournament_status": {
                "status": "in_progress",
                "tournament_complete": False,
            },
        })

        by_kind = {fact["kind"]: fact for fact in facts}
        self.assertEqual(by_kind["match_result"]["kickoff_at"], "2026-07-20T19:00:00+00:00")
        self.assertEqual(by_kind["match_result"]["venue"], "Stadium A, City A")
        self.assertEqual(by_kind["match_result"]["referee"], "Referee A")
        self.assertFalse(by_kind["match_result"]["extra_time"])
        self.assertTrue(by_kind["match_result"]["penalty_shootout"])
        self.assertEqual(by_kind["match_result"]["red_cards"], 1.0)
        self.assertEqual(by_kind["discipline"]["minute"], "72+1")
        self.assertEqual(by_kind["discipline"]["red_cards"], 1.0)
        self.assertTrue(by_kind["qualification"]["already_qualified"])
        self.assertFalse(by_kind["qualification"]["already_eliminated"])
        self.assertEqual(by_kind["player_award"]["goals"], "7")
        self.assertEqual(by_kind["team_stat"]["stat_value"], 60.0)
        self.assertEqual(by_kind["player_stat"]["position"], "F")

    def test_rejects_unknown_boolean_values(self):
        with self.assertRaisesRegex(ValueError, "penalty_shootout must be a boolean"):
            world_cup_data_to_facts({
                "csv": {
                    "matches": (
                        "match_id,stage,home_team,away_team,status,penalty_shootout\n"
                        "round16-1,round_of_16,Team A,Team B,finished,maybe\n"
                    )
                }
            })

    def test_imported_match_data_feeds_existing_resolution(self):
        record = _sports_record(
            "penalties",
            "Will any 2026 FIFA World Cup knockout match be decided by a penalty shootout?",
            "world-cup-2026:penalty-shootout",
            "match_format",
            [WORLD_CUP_TOURNAMENT, "penalty shootout"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = import_world_cup_data({
                    "source": "official_feed",
                    "matches": [{
                        "match_id": "round16-1",
                        "stage": "round_of_16",
                        "home_team": "Team A",
                        "away_team": "Team B",
                        "status": "finished",
                        "penalty_shootout": True,
                    }],
                })
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_configured_data_file_can_be_previewed_and_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data_path = base / "world_cup_data.json"
            fact_path = base / "sports_facts.json"
            data_path.write_text(json.dumps({
                "source": "official_file",
                "source_url": "https://example.com/world-cup-feed",
                "observed_at": _observed_at_now(),
                "matches": [{
                    "match_id": "round16-1",
                    "stage": "round_of_16",
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "status": "finished",
                    "penalty_shootout": True,
                }],
            }), encoding="utf-8")

            with patch.object(settings, "WORLD_CUP_DATA_FILE", str(data_path)), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(fact_path)):
                payload = load_world_cup_data_file()
                preview_result = preview_world_cup_data_file()
                preview = world_cup_data_file_to_facts()
                result = import_world_cup_data_file(replace=True)
                stored = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(payload["source"], "official_file")
        self.assertEqual(preview_result["source_metadata"]["source"], "official_file")
        self.assertEqual(preview_result["converted_fact_count"], 1)
        self.assertEqual(preview[0]["match_id"], "round16-1")
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["source_metadata"]["source"], "official_file")
        self.assertEqual(stored[0]["match_id"], "round16-1")

    def test_configured_data_file_missing_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing.json")
            with patch.object(settings, "WORLD_CUP_DATA_FILE", missing):
                with self.assertRaises(FileNotFoundError):
                    load_world_cup_data_file()

    def test_configured_data_file_requires_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "world_cup_data.json"
            data_path.write_text(json.dumps({
                "matches": [{
                    "match_id": "round16-1",
                    "home_team": "Team A",
                    "away_team": "Team B",
                }],
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_DATA_FILE", str(data_path)):
                with self.assertRaisesRegex(ValueError, "missing source"):
                    world_cup_data_file_to_facts()

    def test_configured_data_file_rejects_stale_snapshot(self):
        observed_at = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "world_cup_data.json"
            data_path.write_text(json.dumps({
                "source": "official_file",
                "observed_at": observed_at,
                "matches": [{
                    "match_id": "round16-1",
                    "home_team": "Team A",
                    "away_team": "Team B",
                }],
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_DATA_FILE", str(data_path)), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 1):
                with self.assertRaisesRegex(ValueError, "stale"):
                    world_cup_data_file_to_facts()

    def test_source_metadata_requires_timezone(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            validate_world_cup_data_source_metadata({
                "source": "official_file",
                "observed_at": "2026-06-22T00:00:00",
            })


if __name__ == "__main__":
    unittest.main()
