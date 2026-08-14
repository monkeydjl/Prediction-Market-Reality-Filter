import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.sports_signal_service import build_sports_signals
from app.services.world_cup_player_status_source import (
    get_team_injury_impact,
    import_world_cup_player_status_source,
    preview_world_cup_player_status_source,
    world_cup_player_status_source_to_data,
)
from app.sports.football.football_injury import ROLE_WEIGHTS


def _raw_status_payload() -> dict:
    return {
        "source": "official_injury_feed",
        "source_url": "https://example.com/injuries",
        "observed_at": "2026-06-25T00:00:00Z",
        "response": [{
            "player": {"name": "Player A"},
            "team": {"name": "Brazil"},
            "status": "out",
            "injury": {"type": "hamstring"},
            "severity": "high",
            "fixture": {"id": "group-a-1"},
        }, {
            "player": {"name": "Player B"},
            "team": {"name": "Brazil"},
            "status": "suspended",
            "reason": "red card ban",
        }],
    }


class WorldCupPlayerStatusSourceTests(unittest.TestCase):
    def test_normalizes_raw_player_statuses_to_data_shape(self):
        data = world_cup_player_status_source_to_data(_raw_status_payload())

        self.assertEqual(data["source"], "official_injury_feed")
        self.assertEqual(data["observed_at"], "2026-06-25T00:00:00Z")
        self.assertEqual(len(data["player_statuses"]), 2)
        injury = data["player_statuses"][0]
        self.assertEqual(injury["kind"], "injury")
        self.assertEqual(injury["team"], "Brazil")
        self.assertEqual(injury["player"], "Player A")
        self.assertEqual(injury["status"], "out")
        self.assertEqual(injury["reason"], "hamstring")
        self.assertEqual(injury["match_id"], "group-a-1")
        suspension = data["player_statuses"][1]
        self.assertEqual(suspension["kind"], "suspension")
        self.assertEqual(suspension["status"], "suspended")

    def test_envelope_team_applies_to_player_rows(self):
        data = world_cup_player_status_source_to_data({
            "source": "manual_status",
            "team": "France",
            "injuries": [{
                "player": "Player C",
                "status": "doubtful",
            }],
        })

        self.assertEqual(data["player_statuses"][0]["team"], "France")
        self.assertEqual(data["player_statuses"][0]["kind"], "injury")

    def test_normalizes_api_football_injury_rows(self):
        data = world_cup_player_status_source_to_data({
            "provider": "api_football",
            "observed_at": "2026-06-25T00:00:00Z",
            "response": [{
                "player": {
                    "name": "Player C",
                    "type": "Missing Fixture",
                    "reason": "Hamstring injury",
                },
                "team": {"name": "Brazil"},
                "fixture": {"id": 1002},
            }],
        })

        status = data["player_statuses"][0]
        self.assertEqual(status["kind"], "injury")
        self.assertEqual(status["status"], "injured")
        self.assertEqual(status["reason"], "Hamstring injury")
        self.assertEqual(status["match_id"], "1002")

    def test_preview_converts_statuses_to_facts(self):
        result = preview_world_cup_player_status_source(_raw_status_payload())

        self.assertEqual(result["normalized_status_count"], 2)
        self.assertEqual(result["converted_fact_count"], 2)
        facts_by_kind = {fact["kind"]: fact for fact in result["facts"]}
        self.assertEqual(facts_by_kind["injury"]["player"], "Player A")
        self.assertEqual(facts_by_kind["suspension"]["player"], "Player B")

    def test_imported_statuses_feed_existing_injury_signal(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:brazil-semifinal",
            "entities": ["Brazil", WORLD_CUP_TOURNAMENT],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = import_world_cup_player_status_source(
                    _raw_status_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        bundle = build_sports_signals(
            "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )
        self.assertEqual(result["converted_fact_count"], 2)
        self.assertEqual(bundle["signals"]["injury_signal"]["level"], "high")

    def test_rejects_payload_without_statuses(self):
        with self.assertRaisesRegex(ValueError, "did not contain player statuses"):
            world_cup_player_status_source_to_data({"source": "empty_feed", "response": []})

    def test_rejects_status_without_team(self):
        with self.assertRaisesRegex(ValueError, "missing team"):
            world_cup_player_status_source_to_data({
                "injuries": [{
                    "player": "Player A",
                    "status": "out",
                }]
            })


class TeamInjuryImpactTests(unittest.TestCase):
    """The P1-F3 fallback that adapters/_shared.py reaches for.

    _shared.py imported get_team_injury_impact inside a bare `except Exception`,
    so while the function did not exist the ImportError was swallowed and the
    World Cup fallback never ran in production. Every test of that branch
    patched the name with create=True, which invented the attribute and hid the
    gap. These tests call the real function.
    """

    def _import(self, payload, path):
        with patch.object(settings, "SPORTS_FACT_FILE", path):
            import_world_cup_player_status_source(payload, replace=True)

    def test_sums_role_weights_for_out_and_suspended(self):
        payload = _raw_status_payload()
        payload["response"].append({
            "player": {"name": "Player C"},
            "team": {"name": "Brazil"},
            "kind": "lineup",
            "status": "starting",
        })
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            self._import(payload, path)
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                impact = get_team_injury_impact("Brazil")

        # Player A (out) and Player B (suspended) both count; neither is listed
        # as starting, so both take the bench weight.
        self.assertAlmostEqual(impact, 2 * ROLE_WEIGHTS["bench"], places=6)

    def test_starter_lineup_fact_raises_the_weight(self):
        payload = _raw_status_payload()
        payload["response"] = [
            payload["response"][0],
            {
                "player": {"name": "Player A"},
                "team": {"name": "Brazil"},
                "kind": "lineup",
                "status": "starting",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            self._import(payload, path)
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                impact = get_team_injury_impact("Brazil")

        self.assertAlmostEqual(impact, ROLE_WEIGHTS["starter"], places=6)

    def test_none_for_unknown_team_and_blank_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            self._import(_raw_status_payload(), path)
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                # None rather than 0.0: no facts is not known-healthy.
                self.assertIsNone(get_team_injury_impact("Nowhere United"))
                self.assertIsNone(get_team_injury_impact("   "))

    def test_none_when_team_has_only_available_players(self):
        payload = {
            "source": "official_injury_feed",
            "observed_at": "2026-06-25T00:00:00Z",
            "response": [{
                "player": {"name": "Player D"},
                "team": {"name": "Brazil"},
                "kind": "availability",
                "status": "fit",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            self._import(payload, path)
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                self.assertIsNone(get_team_injury_impact("Brazil"))

    def test_adapter_fallback_reaches_the_real_function(self):
        """The import in _shared.py must resolve without create=True."""
        from app.services import world_cup_player_status_source as module

        self.assertTrue(callable(module.get_team_injury_impact))


if __name__ == "__main__":
    unittest.main()
