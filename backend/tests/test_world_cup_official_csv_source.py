import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_official_csv_source import (
    import_world_cup_official_csv_source,
    preview_world_cup_official_csv_source,
    world_cup_official_csv_source_to_data,
)


def _official_csv_payload() -> dict:
    return {
        "source": "official_csv",
        "source_url": "https://example.com/world-cup-official-csv",
        "observed_at": "2026-07-20T00:00:00Z",
        "csv": {
            "matches": (
                "match_id,stage,kickoff_at,venue,referee,home_team,away_team,status,"
                "home_score,away_score,winner,extra_time,penalty_shootout,"
                "home_red_cards,away_red_cards,home_yellow_cards,away_yellow_cards\n"
                "round16-1,round_of_16,2026-07-20T19:00:00+00:00,"
                "\"Stadium A, City A\",Referee A,Team A,Team B,finished,1,1,"
                "Team A,true,true,1,0,2,1\n"
            ),
            "discipline": (
                "event_id,match_id,stage,team,player,minute,status,red_cards,"
                "yellow_cards,reason\n"
                "card-1,round16-1,round_of_16,Team A,Player A,72+1,red_card,1,0,"
                "serious foul\n"
            ),
            "qualifications": (
                "team,stage,status,already_qualified,already_eliminated\n"
                "Mexico,round_of_32,qualified,true,false\n"
            ),
            "player_awards": (
                "award,player,team,goals,rank,status\n"
                "golden_boot,Player C,Team C,7,1,current\n"
            ),
            "player_statuses": (
                "kind,team,player,status,severity,match_id,stage,position,formation,"
                "jersey_number,reason,applies_to\n"
                "lineup,Team A,Player A,starting,,round16-1,round_of_16,F,4-3-3,"
                "10,official starting XI,world-cup-2026:team-a-round16\n"
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
    }


class WorldCupOfficialCsvSourceTests(unittest.TestCase):
    def test_strict_csv_profile_converts_to_normalized_data(self):
        data = world_cup_official_csv_source_to_data(_official_csv_payload())

        self.assertEqual(data["profile"], "official_csv_v1")
        self.assertEqual(data["profile_counts"]["matches"], 1)
        self.assertEqual(data["matches"][0]["match_id"], "round16-1")
        self.assertEqual(data["matches"][0]["home_yellow_cards"], "2")
        self.assertEqual(data["discipline"][0]["event_id"], "card-1")
        self.assertEqual(data["player_statuses"][0]["formation"], "4-3-3")
        self.assertEqual(data["team_stats"][0]["stat_name"], "ball possession")
        self.assertEqual(data["player_stats"][0]["player"], "Player A")
        self.assertEqual(
            data["player_statuses"][0]["applies_to"],
            ["world-cup-2026:team-a-round16"],
        )

    def test_preview_converts_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
            result = preview_world_cup_official_csv_source(_official_csv_payload())
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["profile"], "official_csv_v1")
        self.assertEqual(result["converted_fact_count"], 7)
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {
                "match_result",
                "discipline",
                "qualification",
                "player_award",
                "lineup",
                "team_stat",
                "player_stat",
            },
        )
        lineup = next(fact for fact in result["facts"] if fact["kind"] == "lineup")
        self.assertEqual(lineup["position"], "F")
        self.assertEqual(lineup["jersey_number"], "10")
        self.assertEqual(facts, [])

    def test_import_writes_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fact_path = str(Path(tmp) / "facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", fact_path):
                result = import_world_cup_official_csv_source(
                    _official_csv_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["converted_fact_count"], 7)
        self.assertEqual(result["imported"], 7)
        self.assertEqual(len(facts), 7)

    def test_rejects_missing_or_reordered_headers(self):
        payload = _official_csv_payload()
        payload["csv"]["matches"] = (
            "match_id,stage,venue,referee,home_team,away_team,status,home_score,"
            "away_score,winner,extra_time,penalty_shootout,home_red_cards,"
            "away_red_cards,home_yellow_cards,away_yellow_cards\n"
            "round16-1,round_of_16,\"Stadium A, City A\",Referee A,Team A,Team B,"
            "finished,1,1,Team A,true,true,1,0,2,1\n"
        )

        with self.assertRaisesRegex(ValueError, "headers must exactly match"):
            world_cup_official_csv_source_to_data(payload)

    def test_rejects_unknown_csv_section(self):
        payload = _official_csv_payload()
        payload["csv"]["odds"] = "market,price\nTeam A,0.5\n"

        with self.assertRaisesRegex(ValueError, "does not support csv.odds"):
            world_cup_official_csv_source_to_data(payload)


if __name__ == "__main__":
    unittest.main()
