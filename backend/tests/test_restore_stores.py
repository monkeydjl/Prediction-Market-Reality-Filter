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

import contextlib
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

from app.core import runtime_stores  # noqa: E402
from app.core.config import settings  # noqa: E402
from restore_stores import (  # noqa: E402
    _format_report,
    _list_backup_contents,
    _target_path_for_arcname,
    main,
    restore_from_backup,
)


@contextlib.contextmanager
def _stores_in(root: Path, *, per_store_dirs: bool = False, **overrides: str):
    """Point every declared state store inside `root` for the duration.

    These tests used to `patch("restore_stores.settings")` with a MagicMock and
    set the four attributes that existed when they were written. That mock *was*
    the four-store assumption: a fifth store would read an auto-created Mock
    instead of a path, so the tests could never notice one arriving. Patching the
    real settings object attribute-by-attribute over
    `runtime_stores.state_setting_names()` means a new row in the store table is
    covered here the moment it is declared.

    `overrides` maps a setting name to an explicit path when a test needs a
    specific file (e.g. one it pre-populated); everything else gets
    ``root/<default basename>``.

    `per_store_dirs` gives each store its own subdirectory. Required by any test
    that asserts a store does *not* land on the unknown-arcname fallback path:
    that fallback is ``Path(settings.LOOP_DB_FILE).parent``, so with every store
    in one directory the wrong answer and the right answer are the same path and
    the assertion cannot fail. Flat layout stays the default because it matches
    how a real install is laid out.
    """
    with contextlib.ExitStack() as stack:
        for name in runtime_stores.state_setting_names():
            value = overrides.get(name)
            if value is None:
                basename = Path(getattr(settings, name)).name
                parent = root / name.lower() if per_store_dirs else root
                value = str(parent / basename)
            stack.enter_context(patch.object(settings, name, value))
        yield


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
        with _stores_in(Path("/data")):
            target = _target_path_for_arcname("event_store.json", None)
            self.assertEqual(target, Path("/data/event_store.json").resolve())

    def test_loop_db_sidecars_mapped(self):
        with _stores_in(Path("/data")):
            target_wal = _target_path_for_arcname("v2_loop.db-wal", None)
            target_shm = _target_path_for_arcname("v2_loop.db-shm", None)
            self.assertEqual(target_wal.name, "v2_loop.db-wal")
            self.assertEqual(target_shm.name, "v2_loop.db-shm")

    def test_every_state_store_maps_to_its_configured_path(self):
        """No declared store falls through to the unknown-arcname fallback.

        The fallback drops a member next to the loop DB, so a store missing from
        the mapping still "restores" — to the wrong path, silently. This is the
        defect the four-store list actually caused, so it is asserted per store
        rather than for one representative.
        """
        with _stores_in(Path("/data"), per_store_dirs=True):
            loop_parent = Path(settings.LOOP_DB_FILE).parent.resolve()
            checked_against_fallback = 0
            for name in runtime_stores.state_setting_names():
                configured = Path(getattr(settings, name))
                with self.subTest(store=name):
                    target = _target_path_for_arcname(configured.name, None)
                    self.assertEqual(target, configured.resolve())
                    # Every store except the loop DB itself lives in its own
                    # directory here, so the fallback is a *different* path and
                    # this assertion can actually fail.
                    if configured.parent.resolve() != loop_parent:
                        self.assertNotEqual(
                            target, (loop_parent / configured.name).resolve()
                        )
                        checked_against_fallback += 1
            # Guard the guard: if the layout ever collapses back to one shared
            # directory, the assertion above silently stops running.
            self.assertEqual(
                checked_against_fallback,
                len(runtime_stores.state_setting_names()) - 1,
                "the fallback comparison was skipped for some stores, which "
                "means their configured parent equalled the loop DB's parent",
            )

    def test_sidecars_mapped_for_every_sqlite_store(self):
        """WAL/SHM follow their own store, not only the loop DB's.

        Sidecar handling was written for LOOP_DB_FILE alone; the kernel and
        World Cup DBs are equally WAL-mode SQLite.
        """
        # Separate directories on purpose: in a flat layout the unknown-arcname
        # fallback (`loop_db.parent`) coincides with every store's own parent, so
        # a sidecar missing from the map would still resolve to the expected path
        # and this test would pass against a loop-DB-only implementation.
        with _stores_in(Path("/data"), per_store_dirs=True):
            sqlite_stores = [
                Path(getattr(settings, name))
                for name in runtime_stores.state_setting_names()
                if Path(getattr(settings, name)).suffix == ".db"
            ]
            self.assertGreaterEqual(len(sqlite_stores), 4)
            for store in sqlite_stores:
                for suffix in runtime_stores.SQLITE_SIDECAR_SUFFIXES:
                    arcname = store.name + suffix
                    with self.subTest(arcname=arcname):
                        target = _target_path_for_arcname(arcname, None)
                        self.assertEqual(
                            target, (store.parent / arcname).resolve()
                        )

    def test_target_dir_override_ignores_settings(self):
        with _stores_in(Path("/data")):
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
            with _stores_in(Path(tmp)), \
                 patch("restore_stores._check_service_running", return_value=False):
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

            with _stores_in(Path(tmp), EVENT_STORE_FILE=str(current_file)), \
                 patch("restore_stores._check_service_running", return_value=False):
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

            with _stores_in(Path(tmp), EVENT_STORE_FILE=str(current_file)), \
                 patch("restore_stores._check_service_running", return_value=False):
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

            with _stores_in(Path(tmp), EVENT_STORE_FILE=str(current_file)), \
                 patch("restore_stores._check_service_running", return_value=False):
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

            with _stores_in(Path(tmp), EVENT_STORE_FILE=str(current_file)), \
                 patch("restore_stores._check_service_running", return_value=False):
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

            with _stores_in(Path(tmp), EVENT_STORE_FILE=str(current_file)), \
                 patch("restore_stores._check_service_running", return_value=False):
                result = restore_from_backup(archive, apply=True)
            self.assertTrue(current_file.exists())
            self.assertEqual(current_file.read_bytes(), b'{"new": true}')

    def test_target_dir_override(self):
        """--target-dir restores into a custom location."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "backup.zip"
            _create_test_backup(archive, {"event_store.json": b'{"new": true}'})

            target_dir = Path(tmp) / "restore-target"
            with _stores_in(Path(tmp)), \
                 patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""), \
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

            with _stores_in(Path(tmp)), \
                 patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""), \
                 patch("restore_stores._check_service_running", return_value=False):
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
            with _stores_in(Path(tmp)), \
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
