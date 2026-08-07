"""Tests for scheduler job logic.

The scheduler jobs are thin glue (fetch -> process -> log). These lock the
event-layer jobs: event_discover passes the configured limit and forces a
fresh re-scan, a discovery failure cannot crash the scheduler, the job is
gated on EVENT_DISCOVER_ENABLED, and the misfire-grace defaults are set so a
missed run is not silently dropped.

All external dependencies are mocked (discover_events), so no network or LLM
is hit.
"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import scheduler
from app.memory import loop_run_store
from app.utils import sqlite_db


class JobDefaultsTests(unittest.TestCase):
    """job_defaults on the scheduler prevent silent misfire drops."""

    def test_scheduler_has_coalesce_and_misfire_grace(self):
        defaults = scheduler.scheduler._job_defaults
        self.assertTrue(defaults.get("coalesce"))
        self.assertGreater(defaults.get("misfire_grace_time", 0), 60)


class StopSchedulerTests(unittest.TestCase):
    def test_stop_waits_for_running_jobs(self):
        fake = MagicMock()
        fake.running = True
        with patch.object(scheduler, "scheduler", fake):
            scheduler.stop_scheduler()
        fake.shutdown.assert_called_once_with(wait=True)


class SchedulerLockTests(unittest.TestCase):
    """The scheduler process lock prevents same-host worker duplication."""

    def tearDown(self):
        scheduler._release_scheduler_lock()

    def test_process_lock_acquires_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = str(Path(tmp) / "scheduler.lock")
            with patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", True), \
                    patch.object(scheduler.settings, "SCHEDULER_LOCK_FILE", lock_path):
                self.assertTrue(scheduler._try_acquire_scheduler_lock())
                self.assertFalse(scheduler.scheduler_start_skipped_due_to_lock())
                scheduler._release_scheduler_lock()
                self.assertTrue(scheduler._try_acquire_scheduler_lock())
                scheduler._release_scheduler_lock()

    def test_process_lock_detects_external_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = str(Path(tmp) / "scheduler.lock")
            with open(lock_path, "a+", encoding="utf-8") as owner:
                scheduler._acquire_process_lock(owner)
                try:
                    with patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", True), \
                            patch.object(scheduler.settings, "SCHEDULER_LOCK_FILE", lock_path):
                        self.assertFalse(scheduler._try_acquire_scheduler_lock())
                        self.assertTrue(scheduler.scheduler_start_skipped_due_to_lock())
                finally:
                    scheduler._release_process_lock(owner)


class EventDiscoverJobTests(unittest.TestCase):
    """_job_event_discover runs the event-layer discovery (which freezes
    predictions), passing the configured limit and forcing a fresh re-scan."""

    def test_job_calls_discover_events_with_config(self):
        captured = {}

        async def fake_discover(**kwargs):
            captured.update(kwargs)
            return {"count": 3}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.event_intelligence_service.discover_events",
                          new=AsyncMock(side_effect=fake_discover)), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_LIMIT", 7):
                asyncio.run(scheduler._job_event_discover())
                run = loop_run_store.last_run("event_discover")
        self.assertEqual(captured.get("limit"), 7)
        self.assertEqual(captured.get("use_cache"), False)  # fresh re-scan each run
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["count"], 3)

    def test_job_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.event_intelligence_service.discover_events",
                          new=AsyncMock(side_effect=RuntimeError("boom"))), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True):
                # Must not raise: a discovery failure cannot crash the scheduler.
                asyncio.run(scheduler._job_event_discover())
                run = loop_run_store.last_run("event_discover")
        self.assertEqual(run["status"], "failed")
        self.assertIn("boom", run["error"])

    def test_job_skips_when_disabled(self):
        mock_discover = AsyncMock(return_value={"count": 1})
        with patch("app.services.event_intelligence_service.discover_events",
                   new=mock_discover), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", False):
            asyncio.run(scheduler._job_event_discover())
        mock_discover.assert_not_called()


class EventDiscoverStartupJobTests(unittest.TestCase):
    """_job_event_discover_startup always uses limit=10 independent of
    EVENT_DISCOVER_LIMIT, so first-run LLM cost stays bounded."""

    def test_startup_job_uses_fixed_limit(self):
        captured = {}

        async def fake_discover(**kwargs):
            captured.update(kwargs)
            return {"count": 3}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.event_intelligence_service.discover_events",
                          new=AsyncMock(side_effect=fake_discover)), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_LIMIT", 100):
                asyncio.run(scheduler._job_event_discover_startup())
                run = loop_run_store.last_run("event_discover_startup")
        # Startup job must ignore EVENT_DISCOVER_LIMIT and always use 10
        self.assertEqual(captured.get("limit"), 10)
        self.assertEqual(captured.get("use_cache"), False)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["count"], 3)


class TranslateTitlesJobTests(unittest.TestCase):
    def test_job_retries_english_placeholder_titles(self):
        records = [
            {
                "event_id": "english-placeholder",
                "record": {
                    "event_id": "english-placeholder",
                    "event_title": "Will it rain?",
                    "event_title_zh": "Will it rain?",
                },
            },
            {
                "event_id": "already-zh",
                "record": {
                    "event_id": "already-zh",
                    "event_title": "Will it snow?",
                    "event_title_zh": "会下雪吗？",
                },
            },
            {
                "event_id": "empty-zh",
                "record": {
                    "event_id": "empty-zh",
                    "event_title": "Will it pass?",
                    "event_title_zh": "",
                },
            },
        ]

        async def fake_translate(title):
            return {
                "Will it rain?": "会下雨吗？",
                "Will it pass?": "会通过吗？",
            }[title]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.memory.event_store.list_all_events", return_value=records), \
                    patch("app.memory.event_store.save_events") as save_events, \
                    patch("app.services.probability_engine_service.translate_title",
                          new=AsyncMock(side_effect=fake_translate)):
                asyncio.run(scheduler._job_translate_titles())
                run = loop_run_store.last_run("translate_titles")

        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["translated"], 2)
        saved = save_events.call_args.args[0]
        self.assertEqual(
            {record["event_id"]: record["event_title_zh"] for record in saved},
            {
                "english-placeholder": "会下雨吗？",
                "empty-zh": "会通过吗？",
            },
        )


class LoopDbMaintenanceJobTests(unittest.TestCase):
    def test_job_runs_sqlite_maintenance_and_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {"ok": True, "integrity": ["ok"], "checkpoint": {"busy": 0}}
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain", return_value=result):
                asyncio.run(scheduler._job_loop_db_maintenance())
                run = loop_run_store.last_run("loop_db_maintenance")
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"], result)

    def test_job_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain",
                                 side_effect=RuntimeError("bad db")):
                asyncio.run(scheduler._job_loop_db_maintenance())
                run = loop_run_store.last_run("loop_db_maintenance")
        self.assertEqual(run["status"], "failed")
        self.assertIn("bad db", run["error"])


class WorldCupBundleImportJobTests(unittest.TestCase):
    def test_job_imports_remote_bundle_and_records_summary(self):
        result = {
            "source_count": 2,
            "converted_fact_count": 4,
            "imported": 4,
            "error_count": 0,
            "total": 12,
            "replace": True,
            "source_url": "https://example.com/bundle",
            "sources": [{"normalized_data": {"large": "payload"}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_source_bundle.import_world_cup_source_bundle_url",
                          return_value=result) as import_url, \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "url"), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", True):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")

        import_url.assert_called_once_with(replace=True)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["mode"], "url")
        self.assertEqual(run["result"]["converted_fact_count"], 4)
        self.assertEqual(run["result"]["source_url"], "https://example.com/bundle")
        self.assertNotIn("sources", run["result"])

    def test_job_imports_configured_file_when_mode_is_file(self):
        result = {
            "source_count": 1,
            "converted_fact_count": 1,
            "imported": 1,
            "replace": False,
            "source_file": "world_cup_source_bundle.json",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_source_bundle.import_world_cup_source_bundle_file",
                          return_value=result) as import_file, \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "file"), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", False):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")

        import_file.assert_called_once_with(replace=False)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["mode"], "file")
        self.assertEqual(run["result"]["source_file"], "world_cup_source_bundle.json")

    def test_job_imports_configured_feeds_when_mode_is_feeds(self):
        result = {
            "source_count": 1,
            "converted_fact_count": 1,
            "imported": 1,
            "replace": False,
            "source_feeds": [{
                "kind": "matches",
                "source_url": "https://example.com/matches",
            }],
            "sources": [{"normalized_data": {"large": "payload"}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_source_bundle.import_world_cup_source_bundle_feeds",
                          return_value=result) as import_feeds, \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "feeds"), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", False):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")

        import_feeds.assert_called_once_with(replace=False)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["mode"], "feeds")
        self.assertEqual(
            run["result"]["source_feeds"][0]["source_url"],
            "https://example.com/matches",
        )
        self.assertNotIn("sources", run["result"])

    def test_job_imports_api_football_when_mode_is_api_football(self):
        result = {
            "provider": "api_football",
            "source_count": 1,
            "converted_fact_count": 1,
            "imported": 1,
            "replace": False,
            "source_feeds": [{
                "kind": "matches",
                "source_url": "https://api-football.example/v3/fixtures",
            }],
            "skipped_source_count": 3,
            "source_fetch_count": 4,
            "source_fetches": [{
                "kind": "matches",
                "source_url": "https://api-football.example/v3/fixtures",
                "status": "success",
                "duration_ms": 12,
            }],
            "call_budget": {
                "fixture_count": 1,
                "max_detail_calls": 100,
                "detail_calls_used": 0,
                "detail_calls_skipped": 0,
                "detail_calls_remaining": 100,
                "enabled_detail_feeds": [],
            },
            "sources": [{"normalized_data": {"large": "payload"}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_api_football_source.import_world_cup_api_football_bundle",
                          return_value=result) as import_provider, \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "api_football"), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", False):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")

        import_provider.assert_called_once_with(replace=False)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["mode"], "api_football")
        self.assertEqual(run["result"]["provider"], "api_football")
        self.assertEqual(run["result"]["skipped_source_count"], 3)
        self.assertEqual(run["result"]["source_fetch_count"], 4)
        self.assertEqual(run["result"]["source_fetches"][0]["status"], "success")
        self.assertEqual(run["result"]["call_budget"]["max_detail_calls"], 100)
        self.assertNotIn("sources", run["result"])

    def test_job_imports_sportmonks_when_mode_is_sportmonks(self):
        result = {
            "provider": "sportmonks",
            "source_count": 1,
            "converted_fact_count": 1,
            "imported": 1,
            "replace": False,
            "source_feeds": [{
                "kind": "matches",
                "source_url": "https://sportmonks.example/fixtures",
            }],
            "skipped_source_count": 2,
            "sources": [{"normalized_data": {"large": "payload"}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_sportmonks_source.import_world_cup_sportmonks_bundle",
                          return_value=result) as import_provider, \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "sportmonks"), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", False):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")

        import_provider.assert_called_once_with(replace=False)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["mode"], "sportmonks")
        self.assertEqual(run["result"]["provider"], "sportmonks")
        self.assertEqual(run["result"]["skipped_source_count"], 2)
        self.assertNotIn("sources", run["result"])

    def test_job_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_source_bundle.import_world_cup_source_bundle_url",
                          side_effect=RuntimeError("feed down")), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "url"):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")

        self.assertEqual(run["status"], "failed")
        self.assertIn("feed down", run["error"])
        self.assertEqual(run["result"]["mode"], "url")

    def test_job_skips_when_disabled(self):
        with patch("app.services.world_cup_source_bundle.import_world_cup_source_bundle_url") as import_url, \
                patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False):
            asyncio.run(scheduler._job_world_cup_source_bundle_import())
        import_url.assert_not_called()

    def test_matchday_refresh_runs_post_match_backfill_after_import(self):
        import_result = {
            "provider": "football_data",
            "source_count": 1,
            "converted_fact_count": 1,
            "imported": 1,
            "replace": True,
        }
        post_match_result = {
            "status": "ok",
            "candidate_count": 1,
            "scoring": {"scored": 1, "skipped": 0, "errors": 0},
            "result_fact_backfill": {"imported": 1},
        }
        facts = [
            {
                "kind": "match_result",
                "status": "SCHEDULED",
                "kickoff_at": datetime.now(timezone.utc).isoformat(),
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.settings, "WORLD_CUP_MATCHDAY_REFRESH_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "football_data"), \
                    patch.object(scheduler.settings, "WORLD_CUP_MATCHDAY_REFRESH_WINDOW_HOURS", 4), \
                    patch("app.services.sports_fact_service.load_sports_facts", return_value=facts), \
                    patch.object(scheduler, "_run_world_cup_bundle_import", return_value=import_result) as import_bundle, \
                    patch(
                        "app.services.world_cup_post_match_backfill_service.run_post_match_backfill",
                        return_value=post_match_result,
                    ) as backfill:
                asyncio.run(scheduler._job_world_cup_matchday_refresh())
                run = loop_run_store.last_run("world_cup_matchday_refresh")

        import_bundle.assert_called_once_with("football_data", replace=True)
        backfill.assert_called_once_with(dry_run=False, sync_first=False)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["post_match_backfill"]["candidate_count"], 1)
        self.assertEqual(run["result"]["post_match_backfill"]["result_facts_imported"], 1)


class EventDiscoverRegistrationTests(unittest.TestCase):
    """The event_discover job is registered only when EVENT_DISCOVER_ENABLED;
    event_auto_resolve is always registered."""

    def _registered_ids(self, enabled):
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", enabled):
            scheduler.start_scheduler()
        return [call.kwargs.get("id") for call in fake.add_job.call_args_list]

    def test_registered_when_enabled(self):
        ids = self._registered_ids(True)
        self.assertIn("event_discover", ids)
        self.assertIn("event_auto_resolve", ids)
        self.assertIn("loop_db_maintenance", ids)

    def test_not_registered_when_disabled(self):
        ids = self._registered_ids(False)
        self.assertNotIn("event_discover", ids)
        self.assertIn("event_auto_resolve", ids)  # always registered
        self.assertIn("loop_db_maintenance", ids)

    def test_world_cup_bundle_import_registered_when_enabled(self):
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", False), \
                patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC", 5), \
                patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC", 20):
            scheduler.start_scheduler()
        ids = [call.kwargs.get("id") for call in fake.add_job.call_args_list]
        self.assertIn("world_cup_source_bundle_import", ids)

    def test_start_is_noop_when_already_running(self):
        fake = MagicMock()
        fake.running = True
        with patch.object(scheduler, "scheduler", fake):
            scheduler.start_scheduler()
        fake.add_job.assert_not_called()
        fake.start.assert_not_called()

    def test_start_skips_when_process_lock_is_held(self):
        fake = MagicMock()
        fake.running = False
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler, "_try_acquire_scheduler_lock", return_value=False):
            started = scheduler.start_scheduler()
        self.assertFalse(started)
        fake.add_job.assert_not_called()
        fake.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
