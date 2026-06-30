"""Unit tests for restore_stores script.

Tests cover:
- Dry-run preview (no writes)
- Apply mode (writes + pre-restore backup)
- Encrypted backup support
- Checksum verification
- Target-dir override
- File mapping (event_store.json, v2_loop.db, -wal, -shm)
- Error cases (missing archive, no key for encrypted)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
_SCRIPTS = _BACKEND / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from restore_stores import (  # noqa: E402
    _format_report,
    _list_backup_contents,
    _target_path_for_arcname,
    main,
    restore_from_backup,
)


def _create_test_backup(
    archive_path: Path,
    files: dict[str, bytes],
    *,
    encryption_key: str | None = None,
) -> Path:
    """Create a test backup zip with the given {arcname: content} mapping."""
    if encryption_key:
        try:
            import pyzipper  # type: ignore[import-not-found]
            with pyzipper.AESZipFile(
                archive_path, "w",
                compression=zipfile.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as zf:
                zf.setpassword(encryption_key.encode("utf-8"))
                for name, content in files.items():
                    zf.writestr(name, content)
        except ImportError:
            # Skip encrypted tests if pyzipper not installed.
            raise unittest.SkipTest("pyzipper not installed")
    else:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, content in files.items():
                zf.writestr(name, content)
    return archive_path


class TestTargetPathMapping(unittest.TestCase):
    """arcname → target path resolution."""

    def test_event_store_json_maps_to_setting(self):
        with patch("restore_stores.settings") as mock_settings:
            mock_settings.EVENT_STORE_FILE = "/data/event_store.json"
            mock_settings.EVENT_AUDIT_FILE = "/data/event_audit.jsonl"
            mock_settings.EVENT_CACHE_FILE = "/data/event_cache.json"
            mock_settings.LOOP_DB_FILE = "/data/v2_loop.db"
            target = _target_path_for_arcname("event_store.json", None)
            self.assertEqual(target, Path("/data/event_store.json").resolve())

    def test_loop_db_sidecars_mapped(self):
        with patch("restore_stores.settings") as mock_settings:
            mock_settings.EVENT_STORE_FILE = "/data/event_store.json"
            mock_settings.EVENT_AUDIT_FILE = "/data/event_audit.jsonl"
            mock_settings.EVENT_CACHE_FILE = "/data/event_cache.json"
            mock_settings.LOOP_DB_FILE = "/data/v2_loop.db"
            target_wal = _target_path_for_arcname("v2_loop.db-wal", None)
            target_shm = _target_path_for_arcname("v2_loop.db-shm", None)
            self.assertEqual(target_wal.name, "v2_loop.db-wal")
            self.assertEqual(target_shm.name, "v2_loop.db-shm")

    def test_target_dir_override_ignores_settings(self):
        with patch("restore_stores.settings") as mock_settings:
            mock_settings.EVENT_STORE_FILE = "/data/event_store.json"
            target_dir = Path("/tmp/restore-test")
            target = _target_path_for_arcname("event_store.json", target_dir)
            # _target_path_for_arcname returns the resolved path; compare by
            # name and parent to be platform-agnostic (Windows adds a drive).
            self.assertEqual(target.name, "event_store.json")
            self.assertEqual(target.parent.name, "restore-test")


class TestListBackupContents(unittest.TestCase):
    """_list_backup_contents extracts arcname, size, sha256, current state."""

    def test_lists_all_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {
                "event_store.json": b'{"evt1": {}}',
                "v2_loop.db": b"sqlite binary",
            })
            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(Path(tmp) / "event_store.json")
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "event_audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "event_cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "v2_loop.db")

                entries = _list_backup_contents(archive, None, None)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["arcname"], "event_store.json")
            self.assertEqual(entries[0]["size"], 12)  # '{"evt1": {}}' = 12 bytes
            self.assertEqual(len(entries[0]["sha256"]), 64)
            self.assertFalse(entries[0]["exists_currently"])
            self.assertTrue(entries[0]["would_change"])  # new file

    def test_detects_changed_file(self):
        """When target exists with different content, would_change=True."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": b'{"new": true}'})

            # Create a different current file at the target path.
            current_file = Path(tmp) / "event_store.json"
            current_file.write_text('{"old": true}')

            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(current_file)
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "loop.db")

                entries = _list_backup_contents(archive, None, None)
            self.assertTrue(entries[0]["exists_currently"])
            self.assertNotEqual(entries[0]["sha256"], entries[0]["current_sha256"])
            self.assertTrue(entries[0]["would_change"])

    def test_detects_unchanged_file(self):
        """When target exists with same content, would_change=False."""
        with tempfile.TemporaryDirectory() as tmp:
            content = b'{"same": true}'
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": content})

            # Create same current file.
            current_file = Path(tmp) / "event_store.json"
            current_file.write_bytes(content)

            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(current_file)
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "loop.db")

                entries = _list_backup_contents(archive, None, None)
            self.assertFalse(entries[0]["would_change"])


class TestDryRunDoesNotWrite(unittest.TestCase):
    """Dry-run (default) must not modify any files."""

    def test_dry_run_preserves_current_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": b'{"new": true}'})

            current_file = Path(tmp) / "event_store.json"
            current_file.write_bytes(b'{"old": true}')
            original_content = current_file.read_bytes()

            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(current_file)
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "loop.db")

                result = restore_from_backup(archive, apply=False)
            self.assertFalse(result["applied"])
            self.assertEqual(current_file.read_bytes(), original_content)


class TestApplyMode(unittest.TestCase):
    """--apply writes files and creates pre-restore backup."""

    def test_apply_creates_pre_restore_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": b'{"new": true}'})

            current_file = Path(tmp) / "event_store.json"
            current_file.write_bytes(b'{"old": true}')

            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(current_file)
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "loop.db")

                result = restore_from_backup(archive, apply=True)
            self.assertTrue(result["applied"])
            self.assertIn("pre_restore_dir", result)
            # File was restored.
            self.assertEqual(current_file.read_bytes(), b'{"new": true}')
            # Pre-restore backup preserved old content.
            pre_restore_file = Path(result["pre_restore_dir"]) / "event_store.json"
            self.assertTrue(pre_restore_file.exists())
            self.assertEqual(pre_restore_file.read_bytes(), b'{"old": true}')

    def test_apply_creates_new_file_when_target_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": b'{"new": true}'})

            current_file = Path(tmp) / "event_store.json"
            self.assertFalse(current_file.exists())

            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(current_file)
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "loop.db")

                result = restore_from_backup(archive, apply=True)
            self.assertTrue(current_file.exists())
            self.assertEqual(current_file.read_bytes(), b'{"new": true}')

    def test_target_dir_override(self):
        """--target-dir restores into a custom location."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": b'{"new": true}'})

            target_dir = Path(tmp) / "restore-target"
            with patch("restore_stores.settings"), \
                 patch("restore_stores._check_service_running", return_value=False):
                result = restore_from_backup(
                    archive, apply=True, target_dir=target_dir,
                )
            restored = target_dir / "event_store.json"
            self.assertTrue(restored.exists())
            self.assertEqual(restored.read_bytes(), b'{"new": true}')


class TestEncryptedBackup(unittest.TestCase):
    """Encrypted backup support via pyzipper."""

    def test_encrypted_backup_requires_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            try:
                _create_test_backup(
                    archive, {"event_store.json": b'{"data": true}'},
                    encryption_key="secret",
                )
            except unittest.SkipTest:
                self.skipTest("pyzipper not installed")

            with patch("restore_stores.settings") as mock_settings, \
                 patch("restore_stores._check_service_running", return_value=False):
                mock_settings.EVENT_STORE_FILE = str(Path(tmp) / "event_store.json")
                mock_settings.EVENT_AUDIT_FILE = str(Path(tmp) / "audit.jsonl")
                mock_settings.EVENT_CACHE_FILE = str(Path(tmp) / "cache.json")
                mock_settings.LOOP_DB_FILE = str(Path(tmp) / "loop.db")
                mock_settings.BACKUP_ENCRYPTION_KEY = ""

                # No key provided → RuntimeError.
                with self.assertRaises(RuntimeError):
                    restore_from_backup(archive, apply=False, encryption_key="")

    def test_encrypted_backup_restores_with_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            try:
                _create_test_backup(
                    archive, {"event_store.json": b'{"data": true}'},
                    encryption_key="secret",
                )
            except unittest.SkipTest:
                self.skipTest("pyzipper not installed")

            target_dir = Path(tmp) / "restore-target"
            with patch("restore_stores.settings"), \
                 patch("restore_stores._check_service_running", return_value=False):
                result = restore_from_backup(
                    archive, apply=True,
                    encryption_key="secret",
                    target_dir=target_dir,
                )
            self.assertTrue(result["applied"])
            self.assertEqual(
                (target_dir / "event_store.json").read_bytes(),
                b'{"data": true}',
            )


class TestErrorCases(unittest.TestCase):
    """Error handling."""

    def test_missing_archive_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            restore_from_backup("/nonexistent/backup.zip", apply=False)

    def test_main_returns_1_for_missing_archive(self):
        rc = main(["/nonexistent/backup.zip"])
        self.assertEqual(rc, 1)


class TestReportFormatting(unittest.TestCase):
    """Human-readable report output."""

    def test_dry_run_report(self):
        result = {
            "applied": False,
            "archive": "/tmp/backup.zip",
            "entries": [
                {"arcname": "event_store.json", "size": 100, "exists_currently": False},
            ],
            "warnings": [],
        }
        out = _format_report(result, verbose=False)
        self.assertIn("[DRY-RUN]", out)
        self.assertIn("event_store.json", out)
        self.assertIn("new", out)

    def test_apply_report(self):
        result = {
            "applied": True,
            "archive": "/tmp/backup.zip",
            "pre_restore_dir": "/tmp/.pre_restore_20260630",
            "entries": [],
            "warnings": [],
        }
        out = _format_report(result, verbose=False)
        self.assertIn("Restored", out)
        self.assertIn("Pre-restore backup", out)

    def test_warnings_displayed(self):
        result = {
            "applied": False,
            "archive": "/tmp/backup.zip",
            "entries": [],
            "warnings": ["service is running"],
        }
        out = _format_report(result, verbose=False)
        self.assertIn("service is running", out)


if __name__ == "__main__":
    unittest.main()
