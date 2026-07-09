import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import import_sports_facts
from app.services.world_cup_data_source_status_service import world_cup_data_source_status


class WorldCupDataSourceStatusServiceTests(unittest.TestCase):
    def test_readiness_reports_missing_real_qualification_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    return_value={
                        "status": "failed",
                        "error": "WORLD_CUP_SOURCE_BUNDLE_URL is not configured",
                    },
                ),
            ):
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertFalse(readiness["qualification_source_configured"])
        self.assertEqual(readiness["qualification_fact_count"], 0)
        self.assertIn("qualification_source_not_configured", readiness["issues"])
        self.assertIn("qualification_facts_missing", readiness["issues"])
        self.assertIn("scheduled_import_disabled", readiness["issues"])
        self.assertFalse(readiness["last_import_failed"])
        self.assertNotIn("last_import_failed", readiness["issues"])
        self.assertEqual(readiness["qualification_source_state"], "not_configured")
        self.assertEqual(readiness["recommended_qualification_import_mode"], "")
        details_by_code = {
            detail["code"]: detail
            for detail in readiness["issue_details"]
        }
        self.assertEqual(
            details_by_code["qualification_facts_missing"]["message"],
            "尚未导入真实出线/淘汰事实",
        )
        self.assertIn(
            "standings",
            details_by_code["qualification_source_not_configured"]["action"],
        )

    def test_readiness_distinguishes_configured_source_without_imported_qualification_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "configured"),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    return_value={"status": "failed", "error": "provider returned errors"},
                ),
            ):
                import_sports_facts(
                    {
                        "facts": [
                            {
                                "kind": "match_result",
                                "match_id": "m1",
                                "home_team": "Mexico",
                                "away_team": "Canada",
                                "winner": "Mexico",
                                "source": "official_results",
                            }
                        ]
                    },
                    replace=True,
                )
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertTrue(readiness["qualification_source_configured"])
        self.assertEqual(readiness["qualification_source_state"], "configured_not_imported")
        self.assertEqual(readiness["qualification_fact_count"], 0)
        self.assertEqual(readiness["recommended_qualification_import_mode"], "api_football")
        self.assertEqual(readiness["recommended_qualification_import_label"], "API-Football")
        self.assertIn("qualification_facts_missing", readiness["issues"])
        self.assertIn("qualification_import_required", readiness["issues"])
        details_by_code = {
            detail["code"]: detail
            for detail in readiness["issue_details"]
        }
        self.assertIn("Import", details_by_code["qualification_import_required"]["action"])

    def test_readiness_recommends_football_data_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", "configured"),
                patch.object(settings, "FOOTBALL_DATA_BASE_URL", "https://api.football-data.example/v4"),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "configured-but-not-preferred"),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    return_value=None,
                ),
            ):
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertTrue(status["configured_sources"]["football_data"]["configured"])
        self.assertEqual(
            status["configured_sources"]["football_data"]["base_url"],
            "https://api.football-data.example/v4",
        )
        self.assertTrue(readiness["qualification_source_configured"])
        self.assertEqual(readiness["recommended_qualification_import_mode"], "football_data")
        self.assertEqual(readiness["recommended_qualification_import_label"], "Football-Data.org")
        self.assertEqual(readiness["qualification_source_state"], "configured_not_imported")

    def test_readiness_ignores_stale_url_bundle_failure_when_current_mode_is_football_data(self):
        def fake_last_run(job_name: str):
            if job_name == "world_cup_source_bundle_import":
                return {
                    "status": "failed",
                    "error": "WORLD_CUP_SOURCE_BUNDLE_URL is not configured",
                    "result": {},
                }
            return None

        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", "configured"),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "football_data"),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    side_effect=fake_last_run,
                ),
            ):
                import_sports_facts(
                    {
                        "facts": [
                            {
                                "kind": "qualification",
                                "team": "Mexico",
                                "status": "qualified",
                                "source": "football_data",
                                "source_url": "https://api.football-data.org/v4/competitions/WC/standings",
                                "observed_at": "2026-07-07T12:00:00Z",
                            }
                        ]
                    },
                    replace=True,
                )
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertTrue(readiness["ok"])
        self.assertEqual(readiness["recommended_qualification_import_mode"], "football_data")
        self.assertFalse(readiness["last_import_failed"])
        self.assertNotIn("last_import_failed", readiness["issues"])

    def test_readiness_reports_failed_recommended_provider_validation(self):
        def fake_last_run(job_name: str):
            if job_name == "world_cup_api_football_validate":
                return {
                    "status": "failed",
                    "error": "API-Football returned 0 fixtures for league=1 season=2026",
                    "result": {
                        "provider": "api_football",
                        "ok": False,
                        "fixture_count": 0,
                        "failed_step": "fixture_fetch",
                    },
                }
            return None

        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "configured"),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    side_effect=fake_last_run,
                ),
            ):
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertIn("recommended_provider_validation_failed", readiness["issues"])
        self.assertNotIn("qualification_import_required", readiness["issues"])
        self.assertEqual(readiness["qualification_source_state"], "validation_failed")
        self.assertEqual(readiness["recommended_provider_last_validation_status"], "failed")
        self.assertEqual(
            readiness["recommended_provider_last_validation_error"],
            "API-Football returned 0 fixtures for league=1 season=2026",
        )
        self.assertEqual(
            status["runs"]["world_cup_api_football_validate"]["result"]["failed_step"],
            "fixture_fetch",
        )
        details_by_code = {
            detail["code"]: detail
            for detail in readiness["issue_details"]
        }
        self.assertIn(
            "pipeline validation failed",
            details_by_code["recommended_provider_validation_failed"]["action"],
        )

    def test_readiness_allows_simulation_when_trusted_qualification_facts_exist(self):
        def fake_last_run(job_name: str):
            if job_name == "world_cup_api_football_validate":
                return {
                    "status": "failed",
                    "error": "API-Football returned 0 fixtures for league=1 season=2026",
                    "result": {"provider": "api_football", "ok": False},
                }
            if job_name == "world_cup_source_bundle_import":
                return {
                    "status": "failed",
                    "error": "legacy scheduled import failed before manual trusted import",
                }
            return None

        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "configured"),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    side_effect=fake_last_run,
                ),
            ):
                import_sports_facts(
                    {
                        "facts": [
                            {
                                "kind": "qualification",
                                "team": "Mexico",
                                "status": "qualified",
                                "source": "football_data",
                                "source_url": "https://api.football-data.org/v4/competitions/WC/standings",
                                "observed_at": "2026-07-07T12:00:00Z",
                            },
                            {
                                "kind": "match_result",
                                "match_id": "m1",
                                "home_team": "Mexico",
                                "away_team": "Canada",
                                "winner": "Mexico",
                                "source": "official_results",
                            },
                        ]
                    },
                    replace=True,
                )
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertTrue(readiness["ok"])
        self.assertEqual(readiness["qualification_source_state"], "ready")
        self.assertEqual(readiness["qualification_fact_count"], 1)
        self.assertEqual(readiness["untrusted_qualification_fact_count"], 0)
        self.assertIn("scheduled_import_disabled", readiness["issues"])
        self.assertIn("last_import_failed", readiness["issues"])
        self.assertNotIn("recommended_provider_validation_failed", readiness["issues"])
        details_by_code = {
            detail["code"]: detail
            for detail in readiness["issue_details"]
        }
        self.assertEqual(details_by_code["last_import_failed"]["severity"], "warn")

    def test_readiness_does_not_trust_qualification_facts_without_source_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", "https://example.com/standings"),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    return_value={"status": "success", "result": {"imported": 1}},
                ),
            ):
                import_sports_facts(
                    {
                        "facts": [
                            {
                                "kind": "qualification",
                                "team": "Mexico",
                                "status": "qualified",
                                "source": "manual",
                                "observed_at": "2026-06-28T00:00:00Z",
                            },
                        ]
                    },
                    replace=True,
                )
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertEqual(readiness["qualification_fact_count"], 0)
        self.assertEqual(readiness["untrusted_qualification_fact_count"], 1)
        self.assertIn("qualification_facts_untrusted", readiness["issues"])

    def test_readiness_passes_when_source_facts_and_import_loop_are_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = str(Path(tmp) / "sports_facts.json")
            with (
                patch.object(settings, "SPORTS_FACT_FILE", facts_path),
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", "https://example.com/standings"),
                patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""),
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True),
                patch(
                    "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                    return_value={"status": "success", "result": {"imported": 2}},
                ),
            ):
                import_sports_facts(
                    {
                        "facts": [
                            {
                                "kind": "qualification",
                                "team": "Mexico",
                                "status": "qualified",
                                "source": "official_standings",
                                "source_url": "https://example.com/standings",
                                "observed_at": "2026-06-28T00:00:00Z",
                            },
                            {
                                "kind": "match_result",
                                "match_id": "m1",
                                "home_team": "Mexico",
                                "away_team": "Canada",
                                "winner": "Mexico",
                                "source": "official_results",
                            },
                        ]
                    },
                    replace=True,
                )
                status = world_cup_data_source_status()

        readiness = status["real_data_readiness"]
        self.assertTrue(readiness["ok"])
        self.assertTrue(readiness["qualification_source_configured"])
        self.assertEqual(readiness["qualification_source_state"], "ready")
        self.assertEqual(readiness["qualification_fact_count"], 1)
        self.assertEqual(readiness["match_result_count"], 1)
        self.assertEqual(readiness["recommended_qualification_import_mode"], "feeds")
        self.assertEqual(readiness["issues"], [])
        self.assertEqual(readiness["issue_details"], [])


if __name__ == "__main__":
    unittest.main()
