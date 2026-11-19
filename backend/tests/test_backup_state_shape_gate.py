"""A backup of the wrong file restores cleanly and loses everything.

Measured 2026-11-09: `backend/backups/` held exactly one archive,
`pmrf-backup-20260909-051504Z.zip`, 13 bytes, containing `{"secret": 1}` -- the
byte string a test fixture writes -- while the live event store held 208 events
in 3.7 MB. `restore_stores` only checksums what it finds, so that archive would
have "verified" and restored an empty store, and "a pmrf-backup-*.zip exists"
was the only signal that a backup had run.

Size cannot separate that from a legitimately empty install (both are tiny), so
the gate is on shape: a JSON state store's top level is an object, and every
event-store entry is an object because every consumer indexes `entry["record"]`.

Deliberately **not** refused: JSON that does not parse. A broken store is
exactly when an operator wants a copy of the bytes.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402
from scripts import backup_stores  # noqa: E402

WELL_FORMED = '{"evt-1": {"event_id": "evt-1", "record": {}}}'


@contextmanager
def only_event_store(base: Path, payload: str):
    """Point every state store at ``base``, with only event_store.json present.

    Same shape as `test_backup_plaintext_guard.one_store_in`: the other paths aim
    at files that do not exist, so `backup_paths` skips them and the archive
    contents are predictable.
    """
    event_store = base / "event_store.json"
    event_store.write_text(payload, encoding="utf-8")
    with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
            patch.object(settings, "EVENT_AUDIT_FILE", str(base / "no.jsonl")), \
            patch.object(settings, "EVENT_CACHE_FILE", str(base / "no.json")), \
            patch.object(settings, "LOOP_DB_FILE", str(base / "no.db")), \
            patch.object(settings, "KERNEL_DB_FILE", str(base / "no-k.db")), \
            patch.object(settings, "WORLD_CUP_PREDICTION_DB_FILE", str(base / "no-wc.db")), \
            patch.object(settings, "DOMAIN_RELIABILITY_DB_PATH", str(base / "no-dr.db")), \
            patch.object(settings, "SPORTS_FACT_FILE", str(base / "no-sf.json")), \
            patch.object(settings, "PMRF_ENV", "development"), \
            patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
        yield event_store


def archives_in(directory: Path) -> list[Path]:
    return sorted(directory.glob("pmrf-backup-*.zip")) if directory.exists() else []


class ShapeGateTests(unittest.TestCase):
    def test_a_fixture_shaped_event_store_is_refused(self):
        """The exact 13-byte payload found in `backups/`, now refused."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with only_event_store(base, '{"secret": 1}'):
                with self.assertRaises(RuntimeError) as caught:
                    backup_stores.create_backup(str(backups))

            message = str(caught.exception)
            self.assertIn("event_store.json", message)
            self.assertIn("expected an object", message)
            self.assertEqual(
                archives_in(backups), [],
                "a refusal must leave no archive behind",
            )

    def test_a_non_object_top_level_is_refused(self):
        """A JSON array is not any of the declared state stores."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with only_event_store(base, "[1, 2, 3]"):
                with self.assertRaises(RuntimeError) as caught:
                    backup_stores.create_backup(str(backups))

            self.assertIn("expected an object", str(caught.exception))
            self.assertEqual(archives_in(backups), [])

    def test_an_empty_store_is_a_legitimate_install(self):
        """`{}` must still back up: a fresh install has no events, not a bug."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with only_event_store(base, "{}"):
                archive = backup_stores.create_backup(str(backups))

            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(zf.namelist(), ["event_store.json"])
                self.assertEqual(zf.read("event_store.json").decode("utf-8"), "{}")

    def test_a_well_formed_store_is_archived_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with only_event_store(base, WELL_FORMED):
                archive = backup_stores.create_backup(str(backups))

            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(zf.read("event_store.json").decode("utf-8"), WELL_FORMED)

    def test_unparseable_json_is_still_archived(self):
        """The boundary: shape-mismatch refuses, corruption does not.

        Refusing here would take the copy away exactly when it is needed, and
        the bytes are what a recovery starts from.
        """
        truncated = '{"evt-1": '
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with only_event_store(base, truncated):
                archive = backup_stores.create_backup(str(backups))

            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(zf.read("event_store.json").decode("utf-8"), truncated)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
