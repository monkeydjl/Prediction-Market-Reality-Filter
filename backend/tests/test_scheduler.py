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
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import scheduler
from app.memory import loop_run_store
from app.utils import sqlite_db
from scripts import backup_stores


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
        self.assertEqual(run["error"], "Scheduler job failed: RuntimeError")

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
    """The job keeps its name and gained its scope.

    It used to call `sqlite_db.maintain()` with no argument, which maintains
    `LOOP_DB_FILE` only; the other three SQLite state stores were checked nowhere.
    These tests patch `maintain_all` because that is what the job calls now —
    patching `maintain` would leave the job running against the real stores and
    the success case would pass without the mock being consulted at all.
    """

    def test_job_maintains_every_store_and_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "ok": True,
                "failed": [],
                "stores": {
                    "LOOP_DB_FILE": {"ok": True},
                    "KERNEL_DB_FILE": {"ok": True},
                    "WORLD_CUP_PREDICTION_DB_FILE": {"ok": True},
                    "DOMAIN_RELIABILITY_DB_PATH": {"ok": True},
                },
            }
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain_all", return_value=result):
                asyncio.run(scheduler._job_loop_db_maintenance())
                run = loop_run_store.last_run("loop_db_maintenance")
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"], result)

    def test_job_uses_the_all_store_entrypoint(self):
        """Pin the call itself: a revert to `maintain()` is silent otherwise.

        The stored row looks the same either way, so nothing above would notice
        the job going back to covering one store out of four.
        """
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain_all",
                                 return_value={"ok": True, "failed": [], "stores": {}}) as all_mock, \
                    patch.object(scheduler.sqlite_db, "maintain") as single_mock:
                asyncio.run(scheduler._job_loop_db_maintenance())
        all_mock.assert_called_once_with()
        single_mock.assert_not_called()

    def test_a_failed_store_fails_the_run_and_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "ok": False,
                "failed": ["KERNEL_DB_FILE"],
                "stores": {"KERNEL_DB_FILE": {"ok": False, "error": "malformed"}},
            }
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain_all", return_value=result):
                asyncio.run(scheduler._job_loop_db_maintenance())
                run = loop_run_store.last_run("loop_db_maintenance")
        self.assertEqual(run["status"], "failed")
        self.assertIn("KERNEL_DB_FILE", run["error"])

    def test_every_failed_store_is_named_not_just_the_first(self):
        """An operator restoring one store and not the other is the failure mode."""
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "ok": False,
                "failed": ["KERNEL_DB_FILE", "DOMAIN_RELIABILITY_DB_PATH"],
                "stores": {},
            }
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain_all", return_value=result):
                asyncio.run(scheduler._job_loop_db_maintenance())
                run = loop_run_store.last_run("loop_db_maintenance")
        self.assertIn("KERNEL_DB_FILE", run["error"])
        self.assertIn("DOMAIN_RELIABILITY_DB_PATH", run["error"])

    def test_failed_store_diagnostic_is_safe_in_authenticated_health(self):
        from fastapi.testclient import TestClient

        from app.main import app

        sensitive = (
            "Authorization=Bearer fake-api-key ticket=fake-ticket at "
            "D:/private/runtime/store.db"
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "v2_loop.db"
            store_path = Path(tmp) / "kernel.db"
            store_path.touch()
            running_scheduler = MagicMock(running=True)
            with patch.object(sqlite_db, "loop_db_path", return_value=str(ledger_path)), \
                    patch("app.core.runtime_stores.sqlite_state_paths",
                          return_value={"KERNEL_DB_FILE": store_path}), \
                    patch.object(scheduler.sqlite_db, "maintain",
                                 side_effect=RuntimeError(sensitive)), \
                    patch.object(scheduler, "scheduler", running_scheduler), \
                    patch.object(scheduler.settings, "API_WRITE_KEY", "secret"):
                asyncio.run(scheduler._job_loop_db_maintenance())
                response = TestClient(app).get(
                    "/api/health", headers={"X-API-Key": "secret"}
                )

        body = response.json()
        run = body["loop"]["runs"]["loop_db_maintenance"]
        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["status"], "degraded")
        self.assertIn("loop_db_maintenance", body["failed_runs"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["result"]["failed"], ["KERNEL_DB_FILE"])
        self.assertEqual(
            run["result"]["stores"]["KERNEL_DB_FILE"],
            {"ok": False, "error": "SQLite integrity check failed: RuntimeError"},
        )
        for fragment in (
            "Authorization", "fake-api-key", "fake-ticket", "D:/private/runtime"
        ):
            self.assertNotIn(fragment, response.text)

    def test_job_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.sqlite_db, "maintain_all",
                                 side_effect=RuntimeError("bad db")):
                asyncio.run(scheduler._job_loop_db_maintenance())
                run = loop_run_store.last_run("loop_db_maintenance")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "Scheduler job failed: RuntimeError")


class BackupStoresJobTests(unittest.TestCase):
    """The backup that a Docker deployment had no way to run.

    `deploy/docker-compose.yml` mounted `pmrf_data:/app/backups` and nothing ever
    wrote there: `grep backup app/core/scheduler.py` was empty, and the backup
    unit + timer are systemd-only, so a container had the volume and no writer.
    `BACKUP_SCHEDULE_ENABLED` is off by default — on a systemd host the timer is
    already the writer, and a second one would halve the effective retention at
    `--keep 30` — so the *registration* tests below are the ones that decide
    whether the capability is reachable at all.

    `create_backup` is patched on `scripts.backup_stores`, which is where the job
    imports it from at call time. Patching a name the job re-imports per call is
    the only place that works, and it also keeps the real 28.79 MB / 1.01 s zip
    out of the suite.
    """

    def _fake_archive(self, tmp, name="pmrf-backup-20260905-070000Z.zip"):
        archive = Path(tmp) / name
        archive.write_bytes(b"x" * 1234)
        return archive

    def test_job_records_the_archive_it_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._fake_archive(tmp)
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(backup_stores, "create_backup", return_value=archive):
                asyncio.run(scheduler._job_backup_stores())
                run = loop_run_store.last_run("backup_stores")
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["archive"], archive.name)
        self.assertEqual(run["result"]["bytes"], 1234)

    def test_job_does_not_block_the_event_loop(self):
        """Under Docker this job runs inside the API process, serving requests.

        `create_backup` is synchronous — zip-deflating 28.79 MB of stores took
        1.01 s when measured — so a plain call would stall every in-flight request
        for that long. Asserted by thread identity rather than by timing, which is
        the only form that cannot flake: a direct call would run on the loop's own
        thread.
        """
        seen: dict[str, int] = {}

        def _record(*_args, **_kwargs):
            seen["thread"] = threading.get_ident()
            return archive

        with tempfile.TemporaryDirectory() as tmp:
            archive = self._fake_archive(tmp)

            async def _drive():
                seen["loop"] = threading.get_ident()
                await scheduler._job_backup_stores()

            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(backup_stores, "create_backup", side_effect=_record):
                asyncio.run(_drive())

        self.assertIn("thread", seen, "create_backup was never called")
        self.assertNotEqual(
            seen["thread"], seen["loop"],
            "create_backup ran on the event loop thread; it blocks for ~1s on a "
            "real install, which is ~1s of stalled requests",
        )

    def test_job_failure_is_isolated_and_recorded(self):
        """A backup that silently stopped happening is the worst version of this."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(backup_stores, "create_backup",
                                 side_effect=OSError("no space left on device")):
                asyncio.run(scheduler._job_backup_stores())
                run = loop_run_store.last_run("backup_stores")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "Scheduler job failed: OSError")

    def _registered(self, enabled):
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False), \
                patch.object(scheduler.settings, "BACKUP_SCHEDULE_ENABLED", enabled):
            scheduler.start_scheduler()
        return {
            call.kwargs["id"]: call.args[1]
            for call in fake.add_job.call_args_list
            if call.kwargs.get("id")
        }

    def test_registered_when_enabled(self):
        self.assertIn("backup_stores", self._registered(True))

    def test_not_registered_when_disabled(self):
        """The default. A systemd install must not get a second writer."""
        registered = self._registered(False)
        self.assertNotIn("backup_stores", registered)
        self.assertIn("loop_db_maintenance", registered)

    def test_the_backup_runs_after_the_integrity_check(self):
        """Order is the reason the archive is trustworthy, not a preference.

        `loop_db_maintenance` truncates every WAL and runs `PRAGMA
        integrity_check`. Archiving before it would capture a `.db` whose newest
        rows are still in a `-wal` the archive may or may not include, and would
        archive a corrupt store without anything having looked.
        """
        triggers = self._registered(True)

        def _minute_of_day(trigger):
            fields = {name: str(f) for name, f in zip(trigger.FIELD_NAMES, trigger.fields)}
            return int(fields["hour"]) * 60 + int(fields["minute"])

        self.assertGreater(
            _minute_of_day(triggers["backup_stores"]),
            _minute_of_day(triggers["loop_db_maintenance"]),
            "the backup is scheduled before the integrity check that certifies "
            "what it archives",
        )


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
        self.assertNotIn("source_url", run["result"])
        self.assertNotIn("sources", run["result"])

    def test_job_summary_hides_source_locations_from_ledger_and_health(self):
        from fastapi.testclient import TestClient

        from app.main import app

        sensitive = "https://user:fake-secret@internal.example/private/feed?token=fake-key"
        result = {
            "source_count": 1,
            "converted_fact_count": 1,
            "imported": 1,
            "error_count": 0,
            "total": 1,
            "replace": True,
            "source_file": "D:/private/fake-api-key/source-bundle.json",
            "source_url": sensitive,
            "source_feeds": [{"kind": "matches", "source_url": sensitive}],
            "skipped_source_count": 1,
            "skipped_sources": [{
                "kind": "matches",
                "source_url": sensitive,
                "reason": "empty response",
            }],
            "source_fetch_count": 1,
            "source_fetches": [{"kind": "matches", "source_url": sensitive, "status": "success"}],
            "source_metadata": [{"source_url": sensitive, "source_name": "matches"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.world_cup_source_bundle.import_world_cup_source_bundle_url",
                          return_value=result), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", True), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE", "url"), \
                    patch.object(scheduler.settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE", True), \
                    patch.object(scheduler.settings, "API_WRITE_KEY", "secret"):
                asyncio.run(scheduler._job_world_cup_source_bundle_import())
                run = loop_run_store.last_run("world_cup_source_bundle_import")
                response = TestClient(app).get(
                    "/api/health", headers={"X-API-Key": "secret"}
                )

        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["mode"], "url")
        self.assertEqual(run["result"]["source_count"], 1)
        self.assertEqual(run["result"]["skipped_source_count"], 1)
        self.assertEqual(run["result"]["source_fetch_count"], 1)
        self.assertNotIn("skipped_sources", run["result"])
        self.assertIn(response.status_code, {200, 503})
        for fragment in (
            "fake-secret", "fake-api-key", "internal.example", "/private/feed",
            "token=fake-key", "D:/private", sensitive,
        ):
            self.assertNotIn(fragment, repr(run))
            self.assertNotIn(fragment, response.text)

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
        self.assertNotIn("source_file", run["result"])

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
        self.assertNotIn("source_feeds", run["result"])
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
        self.assertNotIn("source_fetches", run["result"])
        self.assertNotIn("source_feeds", run["result"])
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
        self.assertEqual(run["error"], "Scheduler job failed: RuntimeError")
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


class LoopRunLedgerMaintenanceJobTests(unittest.TestCase):
    """The reconcile + retention job for the ledger this scheduler writes.

    Measured before this job existed: 123 rows stuck `running` since 2026-07-23
    or earlier (their owning processes died mid-flight and nothing re-attaches
    to a stored row), and no retention of any kind over 1706 rows.
    """

    def _run_job(self, *, stale_hours=36, retention_days=90):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.settings, "LOOP_RUN_STALE_RUNNING_HOURS", stale_hours), \
                    patch.object(scheduler.settings, "LOOP_RUN_RETENTION_DAYS", retention_days):
                asyncio.run(scheduler._job_loop_run_ledger_maintenance())
                return loop_run_store.last_run("loop_run_ledger_maintenance")

    def test_it_is_registered_with_a_daily_trigger(self):
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False):
            scheduler.start_scheduler()
        ids = {call.kwargs.get("id") for call in fake.add_job.call_args_list}
        self.assertIn("loop_run_ledger_maintenance", ids)

    def test_a_clean_ledger_is_a_success_with_zero_counts(self):
        run = self._run_job()
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["reconciled_running"], 0)
        self.assertEqual(run["result"]["deleted_terminal"], 0)

    def test_stale_running_rows_are_reconciled_through_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.settings, "LOOP_RUN_STALE_RUNNING_HOURS", 36), \
                    patch.object(scheduler.settings, "LOOP_RUN_RETENTION_DAYS", 90):
                loop_run_store.recent_runs(limit=1)  # schema
                with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
                    conn.execute(
                        "INSERT INTO loop_runs (id, job_name, status, started_at) "
                        "VALUES ('ghost', 'event_discover', 'running', '2026-07-01T00:00:00+00:00')"
                    )
                asyncio.run(scheduler._job_loop_run_ledger_maintenance())
                run = loop_run_store.last_run("loop_run_ledger_maintenance")
                ghost = loop_run_store.get_run("ghost")
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["reconciled_running"], 1)
        self.assertEqual(ghost["status"], "failed")

    def test_the_settings_reach_the_store_calls(self):
        """Pin the wiring: a job that computes the wrong cutoffs from the
        settings, or hardcodes them, is wrong only at the horizon."""
        calls = {}
        real_fail = loop_run_store.fail_stale_running_rows
        real_delete = loop_run_store.delete_terminal_runs_before
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.settings, "LOOP_RUN_STALE_RUNNING_HOURS", 5.0), \
                    patch.object(scheduler.settings, "LOOP_RUN_RETENTION_DAYS", 7):

                def spy_fail(cutoff):
                    calls["fail"] = cutoff
                    return real_fail(cutoff)

                def spy_delete(cutoff):
                    calls["delete"] = cutoff
                    return real_delete(cutoff)

                with patch.object(scheduler.loop_run_store, "fail_stale_running_rows", spy_fail), \
                        patch.object(scheduler.loop_run_store, "delete_terminal_runs_before", spy_delete):
                    asyncio.run(scheduler._job_loop_run_ledger_maintenance())
        now = datetime.now(timezone.utc)
        fail_cutoff = datetime.fromisoformat(calls["fail"])
        delete_cutoff = datetime.fromisoformat(calls["delete"])
        self.assertAlmostEqual(
            (now - fail_cutoff).total_seconds(), 5 * 3600, delta=120
        )
        self.assertAlmostEqual(
            (now - delete_cutoff).total_seconds(), 7 * 86400, delta=120
        )

    def test_a_store_failure_fails_the_run(self):
        """A silent catch here would report the ledger as maintained while
        the stale rows are still there -- the alarm-gate defect, one file up."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(scheduler.loop_run_store, "fail_stale_running_rows",
                                 side_effect=sqlite3.OperationalError("database is locked")):
                asyncio.run(scheduler._job_loop_run_ledger_maintenance())
                run = loop_run_store.last_run("loop_run_ledger_maintenance")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "Scheduler job failed: OperationalError")


if __name__ == "__main__":
    unittest.main()
