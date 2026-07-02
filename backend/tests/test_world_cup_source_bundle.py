import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_source_bundle import (
    import_world_cup_source_bundle_feeds,
    fetch_world_cup_source_bundle_url,
    import_world_cup_source_bundle,
    import_world_cup_source_bundle_file,
    import_world_cup_source_bundle_url,
    load_world_cup_source_bundle_file,
    preview_world_cup_source_bundle,
    preview_world_cup_source_bundle_feeds,
    preview_world_cup_source_bundle_file,
    preview_world_cup_source_bundle_url,
    validate_world_cup_source_bundle_metadata,
)


def _bundle_payload() -> dict:
    return {
        "sources": [{
            "kind": "matches",
            "payload": {
                "source": "api_football",
                "observed_at": "2026-07-20T00:00:00Z",
                "response": [{
                    "fixture": {"id": 1001, "status": {"short": "PEN"}},
                    "league": {"round": "Round of 16"},
                    "teams": {
                        "home": {"name": "Team A", "winner": True},
                        "away": {"name": "Team B", "winner": False},
                    },
                    "goals": {"home": 1, "away": 1},
                    "score": {"penalty": {"home": 5, "away": 4}},
                }],
            },
        }, {
            "kind": "standings",
            "payload": {
                "source": "api_football",
                "observed_at": "2026-06-28T00:00:00Z",
                "response": [{
                    "league": {
                        "standings": [[{
                            "team": {"name": "Mexico"},
                            "group": "Group A",
                            "description": "Qualified for knockout stage",
                        }]]
                    }
                }],
            },
        }, {
            "kind": "player_awards",
            "payload": {
                "source": "api_football",
                "observed_at": "2026-07-20T00:00:00Z",
                "award": "golden_boot",
                "response": [{
                    "rank": 1,
                    "player": {"name": "Player A"},
                    "statistics": [{
                        "team": {"name": "Team A"},
                        "goals": {"total": 7},
                    }],
                }],
            },
        }, {
            "kind": "player_status",
            "payload": {
                "source": "official_injury_feed",
                "observed_at": "2026-06-25T00:00:00Z",
                "response": [{
                    "player": {"name": "Player B"},
                    "team": {"name": "Brazil"},
                    "status": "out",
                    "injury": {"type": "hamstring"},
                }],
            },
        }],
    }


class _UrlResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, _size: int) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class WorldCupSourceBundleTests(unittest.TestCase):
    def test_preview_converts_multiple_sources_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
            result = preview_world_cup_source_bundle(_bundle_payload())
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["source_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(result["run"]["status"], "success")
        self.assertEqual(result["run"]["source_count"], 4)
        self.assertGreaterEqual(result["run"]["duration_ms"], 0)
        self.assertEqual(result["sources"][0]["status"], "converted")
        self.assertGreaterEqual(result["sources"][0]["duration_ms"], 0)
        self.assertEqual([source["kind"] for source in result["sources"]], [
            "matches",
            "standings",
            "player_awards",
            "player_status",
        ])
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {"match_result", "qualification", "player_award", "injury"},
        )
        self.assertEqual(facts, [])

    def test_import_writes_combined_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fact_path = str(Path(tmp) / "facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", fact_path):
                result = import_world_cup_source_bundle(
                    _bundle_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["source_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(result["imported"], 4)
        self.assertEqual(len(facts), 4)

    def test_entry_metadata_is_applied_when_payload_metadata_is_missing(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "matches",
                "source": "entry_source",
                "source_url": "https://example.com/feed",
                "observed_at": "2026-07-20T00:00:00Z",
                "payload": {
                    "response": [{
                        "fixture": {"id": 1001, "status": {"short": "FT"}},
                        "teams": {
                            "home": {"name": "Team A", "winner": True},
                            "away": {"name": "Team B", "winner": False},
                        },
                        "goals": {"home": 2, "away": 0},
                    }],
                },
            }],
        })

        normalized = result["sources"][0]["normalized_data"]
        self.assertEqual(normalized["source"], "entry_source")
        self.assertEqual(normalized["source_url"], "https://example.com/feed")
        self.assertEqual(normalized["observed_at"], "2026-07-20T00:00:00Z")

    def test_entry_metadata_is_applied_to_list_payloads(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "matches",
                "source": "entry_source",
                "observed_at": "2026-07-20T00:00:00Z",
                "payload": [{
                    "fixture": {"id": 1001, "status": {"short": "FT"}},
                    "teams": {
                        "home": {"name": "Team A", "winner": True},
                        "away": {"name": "Team B", "winner": False},
                    },
                    "goals": {"home": 2, "away": 0},
                }],
            }],
        })

        normalized = result["sources"][0]["normalized_data"]
        self.assertEqual(normalized["source"], "entry_source")
        self.assertEqual(normalized["observed_at"], "2026-07-20T00:00:00Z")

    def test_bundle_converts_match_events_source(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "match_events",
                "payload": {
                    "source": "api_football_events",
                    "observed_at": "2026-07-20T00:00:00Z",
                    "fixture": {"id": 1001},
                    "response": [{
                        "time": {"elapsed": 72},
                        "team": {"name": "Team A"},
                        "player": {"name": "Player A"},
                        "type": "Card",
                        "detail": "Red Card",
                    }],
                },
            }],
        })

        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["sources"][0]["kind"], "match_events")
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["facts"][0]["kind"], "discipline")
        self.assertEqual(result["facts"][0]["red_cards"], 1.0)

    def test_bundle_converts_lineups_source(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "lineups",
                "payload": {
                    "source": "api_football_lineups",
                    "observed_at": "2026-07-20T00:00:00Z",
                    "fixture": {"id": 1001},
                    "response": [{
                        "team": {"name": "Team A"},
                        "formation": "4-3-3",
                        "startXI": [{
                            "player": {"name": "Player A", "number": 10, "pos": "F"}
                        }],
                    }],
                },
            }],
        })

        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["sources"][0]["kind"], "lineups")
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["facts"][0]["kind"], "lineup")
        self.assertEqual(result["facts"][0]["formation"], "4-3-3")

    def test_bundle_converts_official_csv_source(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "official_csv",
                "payload": {
                    "source": "official_csv",
                    "observed_at": "2026-07-20T00:00:00Z",
                    "csv": {
                        "matches": (
                            "match_id,stage,kickoff_at,venue,referee,home_team,"
                            "away_team,status,home_score,away_score,winner,"
                            "extra_time,penalty_shootout,home_red_cards,"
                            "away_red_cards,home_yellow_cards,away_yellow_cards\n"
                            "round16-1,round_of_16,2026-07-20T19:00:00+00:00,"
                            "\"Stadium A, City A\",Referee A,Team A,Team B,"
                            "finished,1,1,Team A,true,true,1,0,2,1\n"
                        )
                    },
                },
            }],
        })

        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["sources"][0]["kind"], "official_csv")
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["facts"][0]["kind"], "match_result")
        self.assertEqual(result["facts"][0]["yellow_cards"], 3.0)

    def test_bundle_converts_statistics_source(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "statistics",
                "payload": {
                    "source": "api_football_statistics",
                    "observed_at": "2026-07-20T00:00:00Z",
                    "fixture": {"id": 1001},
                    "response": [{
                        "team": {"name": "Team A"},
                        "statistics": [{
                            "type": "Shots on Goal",
                            "value": 5,
                        }],
                    }],
                },
            }],
        })

        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["sources"][0]["kind"], "statistics")
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["facts"][0]["kind"], "team_stat")
        self.assertEqual(result["facts"][0]["stat_value"], 5.0)

    def test_configured_bundle_file_can_be_previewed_and_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bundle_path = base / "world_cup_source_bundle.json"
            facts_path = base / "facts.json"
            bundle_path.write_text(json.dumps(_bundle_payload()), encoding="utf-8")

            with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_FILE", str(bundle_path)), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(facts_path)), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0):
                # _bundle_payload uses fixed observed_at dates; disabling the
                # staleness gate keeps these bundle-flow tests wall-clock-
                # independent. Staleness itself is covered by
                # test_world_cup_data_source_service.py.
                payload = load_world_cup_source_bundle_file()
                preview = preview_world_cup_source_bundle_file()
                result = import_world_cup_source_bundle_file(replace=True)
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(payload["sources"][0]["kind"], "matches")
        self.assertEqual(preview["source_file"], str(bundle_path))
        self.assertEqual(len(preview["source_metadata"]), 4)
        self.assertEqual(preview["converted_fact_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(len(facts), 4)

    def test_configured_bundle_file_missing_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_FILE", str(Path(tmp) / "missing.json")):
            with self.assertRaises(FileNotFoundError):
                load_world_cup_source_bundle_file()

    def test_remote_bundle_url_preview_fetches_configured_url_without_writing(self):
        body = json.dumps(_bundle_payload()).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(
                    settings,
                    "WORLD_CUP_SOURCE_BUNDLE_URL",
                    "https://example.com/world-cup-bundle?token=secret",
                ), \
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER", "X-Feed-Key"), \
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE", "secret-value"), \
                patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                patch(
                    "app.services.world_cup_source_bundle.urlopen",
                    return_value=_UrlResponse(body),
                ) as open_mock:
            # _bundle_payload uses fixed observed_at dates; disable staleness
            # gate (covered by test_world_cup_data_source_service.py).
            result = preview_world_cup_source_bundle_url()
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        request = open_mock.call_args.args[0]
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.assertEqual(request.full_url, "https://example.com/world-cup-bundle?token=secret")
        self.assertEqual(headers["x-feed-key"], "secret-value")
        self.assertEqual(result["source_url"], "https://example.com/world-cup-bundle")
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(facts, [])

    def test_remote_bundle_url_import_writes_combined_facts(self):
        body = json.dumps(_bundle_payload()).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            fact_path = str(Path(tmp) / "facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", fact_path), \
                    patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_URL", "https://example.com/bundle"), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(
                        "app.services.world_cup_source_bundle.urlopen",
                        return_value=_UrlResponse(body),
                    ):
                # _bundle_payload uses fixed observed_at dates; disable
                # staleness gate (covered by test_world_cup_data_source_service.py).
                result = import_world_cup_source_bundle_url(replace=True)
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(result["imported"], 4)
        self.assertEqual(len(facts), 4)

    def test_configured_source_feeds_preview_fetches_multiple_urls(self):
        match_body = json.dumps({
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "FT"}},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }]
        }).encode("utf-8")
        status_body = json.dumps([{
            "player": {"name": "Player B"},
            "team": {"name": "Brazil"},
            "status": "out",
            "injury": {"type": "hamstring"},
        }]).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_MATCH_SOURCE_URL", "https://example.com/matches?token=secret"), \
                patch.object(settings, "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_LINEUPS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", "https://status.example/injuries"), \
                patch.object(settings, "WORLD_CUP_STATISTICS_SOURCE_URL", ""), \
                patch(
                    "app.services.world_cup_source_bundle.urlopen",
                    side_effect=[_UrlResponse(match_body), _UrlResponse(status_body)],
                ) as open_mock:
            result = preview_world_cup_source_bundle_feeds(
                now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
            )
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        requests = [call.args[0] for call in open_mock.call_args_list]
        self.assertEqual(requests[0].full_url, "https://example.com/matches?token=secret")
        self.assertEqual(requests[1].full_url, "https://status.example/injuries")
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["converted_fact_count"], 2)
        self.assertEqual(result["run"]["source_fetch_count"], 2)
        self.assertEqual(result["source_fetches"][0]["status"], "success")
        self.assertGreaterEqual(result["source_fetches"][0]["duration_ms"], 0)
        self.assertEqual(result["source_feeds"][0]["source_url"], "https://example.com/matches")
        self.assertEqual(result["source_metadata"][0]["source"], "example.com")
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(facts, [])

    def test_configured_source_feeds_import_writes_facts(self):
        body = json.dumps({
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "FT"}},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }]
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            fact_path = str(Path(tmp) / "facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", fact_path), \
                    patch.object(settings, "WORLD_CUP_MATCH_SOURCE_URL", "https://example.com/matches"), \
                    patch.object(settings, "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""), \
                    patch.object(settings, "WORLD_CUP_LINEUPS_SOURCE_URL", ""), \
                    patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""), \
                    patch.object(settings, "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""), \
                    patch.object(settings, "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", ""), \
                    patch.object(settings, "WORLD_CUP_STATISTICS_SOURCE_URL", ""), \
                    patch(
                        "app.services.world_cup_source_bundle.urlopen",
                        return_value=_UrlResponse(body),
                    ):
                result = import_world_cup_source_bundle_feeds(
                    replace=True,
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(facts), 1)

    def test_configured_source_feeds_require_at_least_one_url(self):
        with patch.object(settings, "WORLD_CUP_MATCH_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_LINEUPS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STATISTICS_SOURCE_URL", ""):
            with self.assertRaisesRegex(ValueError, "No World Cup source feed URLs"):
                preview_world_cup_source_bundle_feeds()

    def test_remote_bundle_url_requires_configured_url(self):
        with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_URL", ""):
            with self.assertRaisesRegex(ValueError, "not configured"):
                fetch_world_cup_source_bundle_url()

    def test_remote_bundle_url_rejects_invalid_json(self):
        with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_URL", "https://example.com/bundle"), \
                patch(
                    "app.services.world_cup_source_bundle.urlopen",
                    return_value=_UrlResponse(b"not-json"),
                ):
            with self.assertRaisesRegex(ValueError, "valid JSON"):
                fetch_world_cup_source_bundle_url()

    def test_configured_bundle_requires_source_metadata(self):
        with self.assertRaisesRegex(ValueError, r"sources\[0\] matches: .*source"):
            validate_world_cup_source_bundle_metadata({
                "sources": [{
                    "kind": "matches",
                    "payload": {
                        "observed_at": "2026-07-20T00:00:00Z",
                        "response": [{
                            "fixture": {"id": 1001, "status": {"short": "FT"}},
                            "teams": {
                                "home": {"name": "Team A", "winner": True},
                                "away": {"name": "Team B", "winner": False},
                            },
                        }],
                    },
                }]
            })

    def test_configured_bundle_rejects_stale_source_metadata(self):
        with self.assertRaisesRegex(ValueError, r"sources\[0\] matches: .*stale"):
            validate_world_cup_source_bundle_metadata(
                {
                    "sources": [{
                        "kind": "matches",
                        "source": "api_football",
                        "observed_at": "2026-06-01T00:00:00Z",
                        "payload": {
                            "response": [{
                                "fixture": {"id": 1001, "status": {"short": "FT"}},
                                "teams": {
                                    "home": {"name": "Team A", "winner": True},
                                    "away": {"name": "Team B", "winner": False},
                                },
                            }],
                        },
                    }]
                },
                now=datetime(2026, 6, 3, tzinfo=timezone.utc),
                max_age_hours=1,
            )

    def test_rejects_empty_bundle(self):
        with self.assertRaisesRegex(ValueError, "at least one source"):
            preview_world_cup_source_bundle({"sources": []})

    def test_rejects_unknown_source_kind(self):
        with self.assertRaisesRegex(ValueError, "unsupported source kind"):
            preview_world_cup_source_bundle({
                "sources": [{
                    "kind": "odds",
                    "payload": {},
                }]
            })

    def test_rejects_invalid_nested_source_with_context(self):
        with self.assertRaisesRegex(ValueError, r"sources\[0\] matches"):
            preview_world_cup_source_bundle({
                "sources": [{
                    "kind": "matches",
                    "payload": {"response": [{"fixture": {"id": 1001}}]},
                }]
            })


if __name__ == "__main__":
    unittest.main()
