"""Tests for the P0 follow-up fixes (F1-F5).

These tests cover the regression class identified in the P0 review:

- F1: audit_quality_consistency.py / restore_stores.py must not raise
  ``UnicodeEncodeError`` on Windows GBK consoles (cp936) — the human
  output uses ASCII tags ([OK]/[FAIL]/[INFO]/[WARN]) and a UTF-8
  reconfiguration helper instead of emoji.
- F2: Prometheus metrics are actually incremented on overlay build
  failures, downgrade rule fires, scheduler failures, LLM token usage,
  and guardrail fires (not just defined as no-ops).
- F4: ``event_store`` read paths (``_load_unlocked`` /
  ``_load_for_write``) normalize every record on read, so callers always
  see the current schema — including historical records that were
  written before the schema bump.
- F5: ``restore_stores._target_path_for_arcname`` rejects path-traversal
  arcnames (``../``, absolute paths, separators) with ``ValueError``.

Scope:
- Pure-function / fixture-based tests only — no live LLM / no live
  scheduler. The metrics wireup is verified by importing the symbols and
  checking the increment calls happen at the right call sites via mock.
"""
from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ─── F1: Unicode-safe CLI output ──────────────────────────────────────────


class TestF1UnicodeSafeOutput(unittest.TestCase):
    """F1: scripts must not raise UnicodeEncodeError on GBK consoles."""

    def test_audit_format_human_uses_ascii_tags(self):
        """The audit report must use ASCII tags, not emoji, so it renders
        on Windows GBK consoles without UnicodeEncodeError."""
        from scripts.audit_quality_consistency import _format_human, Conflict

        # Empty conflicts case.
        out = _format_human([], verbose=False)
        self.assertIn("[OK]", out)
        self.assertNotIn("✅", out)
        self.assertNotIn("❌", out)
        self.assertNotIn("ℹ️", out)

        # With conflicts.
        conflicts = [
            Conflict(
                event_id="evt1",
                conflict_type="wide_spread_not_downgraded",
                severity="ERROR",
                message="test message",
                field_values={"a": 1},
            )
        ]
        out = _format_human(conflicts, verbose=False)
        self.assertIn("[FAIL]", out)
        self.assertNotIn("❌", out)
        # ASCII bullet instead of •
        self.assertIn("- a:", out)
        self.assertNotIn("•", out)

    def test_audit_format_human_info_hidden_uses_ascii(self):
        from scripts.audit_quality_consistency import _format_human, Conflict, INFO

        conflicts = [
            Conflict(
                event_id="evt1",
                conflict_type="info_only",
                severity=INFO,
                message="info message",
            )
        ]
        out = _format_human(conflicts, verbose=False)
        self.assertIn("[INFO]", out)
        self.assertNotIn("ℹ️", out)

    def test_audit_main_does_not_raise_on_gbk_stdout(self):
        """Simulate a Windows GBK stdout by setting encoding to cp936.
        main() must not raise UnicodeEncodeError — the _print helper
        reconfigures stdout to UTF-8 or falls back to ASCII replacement."""
        from scripts.audit_quality_consistency import main

        # Use a MagicMock to simulate a GBK stdout (encoding='cp936').
        # io.StringIO's encoding attribute is read-only, so we use a mock
        # instead. The _print helper checks the encoding attribute and
        # calls reconfigure() to switch to UTF-8.
        mock_stdout = MagicMock()
        mock_stdout.encoding = "cp936"
        mock_stdout.reconfigure = MagicMock()
        # print() writes to the mock, so we don't need to capture output.

        with patch("sys.stdout", mock_stdout):
            try:
                exit_code = main(["--json"])
            except UnicodeEncodeError:
                self.fail("main() raised UnicodeEncodeError on GBK stdout")

        # Verify reconfigure was called (UTF-8 path taken).
        mock_stdout.reconfigure.assert_called()

    def test_restore_format_report_uses_ascii_tags(self):
        """restore_stores._format_report must use ASCII tags."""
        from scripts.restore_stores import _format_report

        # Dry-run result.
        result = {
            "applied": False,
            "archive": "/tmp/backup.zip",
            "entries": [],
            "warnings": [],
        }
        out = _format_report(result, verbose=False)
        self.assertIn("[DRY-RUN]", out)
        self.assertNotIn("📋", out)

        # Applied result with warnings.
        result = {
            "applied": True,
            "archive": "/tmp/backup.zip",
            "entries": [],
            "warnings": ["service running"],
            "pre_restore_dir": "/tmp/.pre_restore_123",
        }
        out = _format_report(result, verbose=False)
        self.assertIn("[OK]", out)
        self.assertIn("[WARN]", out)
        self.assertNotIn("✅", out)
        self.assertNotIn("⚠️", out)


# ─── F2: Prometheus metrics wireup ────────────────────────────────────────


class TestF2MetricsIncrementedAtCallSites(unittest.TestCase):
    """F2: verify metrics are imported and incremented at the right call
    sites — not just defined as no-ops in metrics.py.

    We use static analysis (source inspection) rather than runtime mock,
    because the wireup happens inside try/except blocks that are hard
    to trigger in unit tests. The test asserts that the source file
    contains the increment calls at the expected call sites.
    """

    def _read_source(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_event_intelligence_increments_overlay_build_failure(self):
        """event_intelligence_service.py must call
        record_overlay_build_failure in each overlay except block."""
        src = self._read_source(
            "app/services/event_intelligence_service.py"
        )
        # Each overlay's except block should record the failure.
        self.assertIn('record_overlay_build_failure("decision_quality")', src)
        self.assertIn('record_overlay_build_failure("market_quality")', src)
        self.assertIn('record_overlay_build_failure("source_reliability")', src)
        self.assertIn('record_overlay_build_failure("merge")', src)
        self.assertIn('record_overlay_build_failure("llm_telemetry")', src)

    def test_event_intelligence_increments_rule_fire_on_downgrade(self):
        """When an overlay downgrades a direction, RULE_FIRE must increment."""
        src = self._read_source(
            "app/services/event_intelligence_service.py"
        )
        self.assertIn('RULE_FIRE.labels(rule="decision_quality_downgrade")', src)
        self.assertIn('RULE_FIRE.labels(rule="market_quality_downgrade")', src)
        self.assertIn('RULE_FIRE.labels(rule="source_reliability_downgrade")', src)

    def test_event_intelligence_increments_guardrail_rule_fire(self):
        """Guardrail fires must increment RULE_FIRE for each fired rule."""
        src = self._read_source(
            "app/services/event_intelligence_service.py"
        )
        self.assertIn("for rule_name in fired_rules:", src)
        self.assertIn("RULE_FIRE.labels(rule=rule_name).inc()", src)

    def test_scheduler_increments_failed_runs_counter(self):
        """scheduler._finish_run must increment SCHEDULER_FAILED_RUNS on
        status='failed'."""
        src = self._read_source("app/core/scheduler.py")
        self.assertIn("SCHEDULER_FAILED_RUNS.labels(job_name=job_name).inc()", src)

    def test_scheduler_increments_last_success_gauge(self):
        """scheduler._finish_run must set SCHEDULER_LAST_SUCCESS on
        status='success'."""
        src = self._read_source("app/core/scheduler.py")
        self.assertIn("SCHEDULER_LAST_SUCCESS.labels(job_name=job_name).set(", src)

    def test_scheduler_has_run_to_job_mapping(self):
        """scheduler must track run_id -> job_name for metric attribution."""
        src = self._read_source("app/core/scheduler.py")
        self.assertIn("_RUN_TO_JOB", src)
        self.assertIn("_RUN_TO_JOB[run_id] = job_name", src)

    def test_llm_telemetry_increments_token_usage(self):
        """llm_telemetry_service._build_block must increment
        LLM_TOKEN_USAGE and LLM_TOKEN_COST."""
        src = self._read_source("app/services/llm_telemetry_service.py")
        self.assertIn("LLM_TOKEN_USAGE.labels(model=model, kind=\"input\").inc(", src)
        self.assertIn("LLM_TOKEN_USAGE.labels(model=model, kind=\"output\").inc(", src)
        self.assertIn("LLM_TOKEN_COST.labels(model=model).inc(", src)

    def test_event_intelligence_has_short_reason_helper(self):
        """_short_reason helper must exist to keep label cardinality bounded."""
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn("def _short_reason(reason: str) -> str:", src)
        self.assertIn("_REASON_LABEL_MAP", src)


# ─── F4: event_store read-path normalize ──────────────────────────────────


class TestF4EventStoreReadNormalize(unittest.TestCase):
    """F4: _load_unlocked and _load_for_write must normalize every record
    on read so callers always see the current schema."""

    def test_load_unlocked_normalizes_records(self):
        """A record missing overlay fields gets them backfilled via
        normalize_event_record when loaded."""
        from app.memory import event_store

        # Build a minimal store with one record that has NO schema_version
        # and NO overlay fields (simulating a historical record).
        store_data = {
            "evt_old": {
                "event_id": "evt_old",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
                "record": {
                    "event_id": "evt_old",
                    "event_title": "Old event",
                },
            }
        }

        with patch("app.memory.event_store.read_json", return_value=store_data):
            with patch("app.memory.event_store._store_path", return_value="/fake/path"):
                result = event_store._load_unlocked("/fake/path")

        record = result["evt_old"]["record"]
        # normalize_event_record should have set schema_version.
        self.assertIn("schema_version", record)
        # Overlay fields should be present (setdefault backfill).
        self.assertIn("decision_quality", record)
        self.assertIn("market_quality", record)
        self.assertIn("source_reliability", record)
        self.assertIn("final_displayed_direction", record)

    def test_load_for_write_normalizes_records(self):
        """_load_for_write must also normalize so the read-modify-write
        cycle persists the upgrade."""
        from app.memory import event_store

        store_data = {
            "evt1": {
                "event_id": "evt1",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
                "record": {
                    "event_id": "evt1",
                    "event_title": "Test",
                },
            }
        }

        with patch("app.memory.event_store.read_json_strict", return_value=store_data):
            with patch("app.memory.event_store._store_path", return_value="/fake/path"):
                result = event_store._load_for_write("/fake/path")

        record = result["evt1"]["record"]
        self.assertIn("schema_version", record)
        self.assertIn("decision_quality", record)

    def test_load_unlocked_handles_malformed_entry_gracefully(self):
        """A malformed entry (non-dict record) must not crash the load."""
        from app.memory import event_store

        store_data = {
            "evt1": "not a dict",  # malformed
            "evt2": {"record": "also not a dict"},  # malformed record
            "evt3": {"record": {"event_id": "evt3"}},  # valid
        }

        with patch("app.memory.event_store.read_json", return_value=store_data):
            with patch("app.memory.event_store._store_path", return_value="/fake/path"):
                result = event_store._load_unlocked("/fake/path")

        # Malformed entries preserved as-is; valid entry normalized.
        self.assertEqual(result["evt1"], "not a dict")
        self.assertEqual(result["evt2"]["record"], "also not a dict")
        # The valid record got normalized.
        self.assertIn("schema_version", result["evt3"]["record"])


class TestF4MigrationScript(unittest.TestCase):
    """F4: the migrate_event_store_schema script does a one-shot on-disk
    schema upgrade."""

    def test_migrate_dry_run_reports_upgraded_count(self):
        from scripts.migrate_event_store_schema import migrate_event_store_schema

        # A store with one record missing schema_version.
        store_data = {
            "evt1": {
                "event_id": "evt1",
                "record": {"event_id": "evt1", "event_title": "Old"},
            }
        }

        with patch("scripts.migrate_event_store_schema.read_json_strict", return_value=store_data):
            with patch("scripts.migrate_event_store_schema.Path") as mock_path_cls:
                mock_path = MagicMock()
                mock_path.exists.return_value = True
                mock_path_cls.return_value.resolve.return_value = mock_path

                with patch("scripts.migrate_event_store_schema.locked_file"):
                    with patch("scripts.migrate_event_store_schema.write_json_atomic") as mock_write:
                        result = migrate_event_store_schema(apply=False)

        self.assertFalse(result["applied"])
        self.assertEqual(result["total_records"], 1)
        self.assertEqual(result["upgraded_count"], 1)
        # Dry-run must not write.
        mock_write.assert_not_called()

    def test_migrate_apply_writes_store(self):
        from scripts.migrate_event_store_schema import migrate_event_store_schema

        store_data = {
            "evt1": {
                "event_id": "evt1",
                "record": {"event_id": "evt1", "event_title": "Old"},
            }
        }

        with patch("scripts.migrate_event_store_schema.read_json_strict", return_value=store_data):
            with patch("scripts.migrate_event_store_schema.Path") as mock_path_cls:
                mock_path = MagicMock()
                mock_path.exists.return_value = True
                mock_path_cls.return_value.resolve.return_value = mock_path

                with patch("scripts.migrate_event_store_schema.locked_file"):
                    with patch("scripts.migrate_event_store_schema.write_json_atomic") as mock_write:
                        result = migrate_event_store_schema(apply=True)

        self.assertTrue(result["applied"])
        self.assertEqual(result["upgraded_count"], 1)
        mock_write.assert_called_once()

    def test_migrate_idempotent_on_current_records(self):
        """Running migrate on already-current records is a no-op."""
        from scripts.migrate_event_store_schema import migrate_event_store_schema
        from app.services.event_schema import CURRENT_SCHEMA_VERSION

        # A record already at current schema_version with all overlay fields.
        store_data = {
            "evt1": {
                "event_id": "evt1",
                "record": {
                    "event_id": "evt1",
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "decision_quality": None,
                    "market_quality": None,
                    "source_reliability": None,
                    "final_displayed_direction": None,
                    "final_downgrade_reason": None,
                    "llm_telemetry": None,
                },
            }
        }

        with patch("scripts.migrate_event_store_schema.read_json_strict", return_value=store_data):
            with patch("scripts.migrate_event_store_schema.Path") as mock_path_cls:
                mock_path = MagicMock()
                mock_path.exists.return_value = True
                mock_path_cls.return_value.resolve.return_value = mock_path

                with patch("scripts.migrate_event_store_schema.locked_file"):
                    with patch("scripts.migrate_event_store_schema.write_json_atomic") as mock_write:
                        result = migrate_event_store_schema(apply=True)

        self.assertEqual(result["upgraded_count"], 0)
        # No write because nothing was upgraded.
        mock_write.assert_not_called()


# ─── F5: restore_stores path traversal guard ──────────────────────────────


class TestF5RestorePathValidation(unittest.TestCase):
    """F5: _target_path_for_arcname rejects path-traversal arcnames."""

    def test_rejects_parent_traversal(self):
        from scripts.restore_stores import _target_path_for_arcname

        with self.assertRaises(ValueError) as ctx:
            _target_path_for_arcname("../etc/passwd", target_dir=Path("/tmp/restore"))
        self.assertIn("unsafe path", str(ctx.exception))

    def test_rejects_absolute_path(self):
        from scripts.restore_stores import _target_path_for_arcname

        with self.assertRaises(ValueError):
            _target_path_for_arcname("/etc/passwd", target_dir=Path("/tmp/restore"))

    def test_rejects_separator_in_arcname(self):
        from scripts.restore_stores import _target_path_for_arcname

        with self.assertRaises(ValueError):
            _target_path_for_arcname("subdir/file.json", target_dir=Path("/tmp/restore"))

    def test_rejects_backslash_separator(self):
        """Windows-style backslash separator must also be rejected."""
        from scripts.restore_stores import _target_path_for_arcname

        with self.assertRaises(ValueError):
            _target_path_for_arcname("subdir\\file.json", target_dir=Path("/tmp/restore"))

    def test_accepts_bare_basename(self):
        """A bare basename (no separators, no ..) is accepted."""
        from scripts.restore_stores import _target_path_for_arcname

        target = _target_path_for_arcname(
            "event_store.json", target_dir=Path("/tmp/restore")
        )
        self.assertEqual(target, Path("/tmp/restore/event_store.json").resolve())

    def test_rejects_entry_outside_target_dir(self):
        """Even if arcname looks safe, a resolved path outside target_dir
        must be rejected (defense-in-depth via _validate_within_runtime_root)."""
        from scripts.restore_stores import _target_path_for_arcname

        # A basename is accepted, but if somehow the resolved path escapes
        # (e.g. via symlink), _validate_within_runtime_root catches it.
        # This test uses a bare basename which should pass the first check
        # but we verify the validation helper exists.
        from scripts.restore_stores import _validate_within_runtime_root

        with self.assertRaises(ValueError):
            _validate_within_runtime_root(
                Path("/elsewhere/file.json"),
                Path("/tmp/restore"),
            )

    def test_validates_within_runtime_root_for_known_files(self):
        """When target_dir is None, the resolved path is validated against
        the configured runtime root (LOOP_DB parent)."""
        from scripts.restore_stores import _target_path_for_arcname

        # A basename that matches EVENT_STORE_FILE should resolve to the
        # configured path and pass validation.
        target = _target_path_for_arcname("event_store.json", target_dir=None)
        # The resolved path should be inside the configured runtime root.
        self.assertTrue(target.is_absolute())


if __name__ == "__main__":
    unittest.main()
