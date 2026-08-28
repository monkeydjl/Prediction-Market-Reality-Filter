"""The backup/restore drill: the two scripts exercised as a pair.

Before this file, `create_backup` and `restore_from_backup` were each tested only
against a hand-written model of the other. `tests/test_operational_readiness.py`
mentioned `create_backup` 10 times and `restore_from_backup` never;
`tests/test_restore_stores.py` the reverse, building every input archive with
`zipfile.writestr`. So the archive *format* — which arcnames the writer emits and
which the reader knows how to place — was asserted on both sides against a
constant that only the tests contained.

That is the seam-held-open-by-a-mock shape, and it had already let a real defect
through: until 2026-08-28 the two scripts kept separate store lists, and a
complete archive restored by the shipped script would silently place four stores
in the loop DB's directory (`_target_path_for_arcname`'s fallback) rather than at
their configured paths.

Measured, by changing the writer's arcname scheme (`path.name` ->
`"stores/" + path.name`) so that each script stayed self-consistent while the two
disagreed: this file goes 5 red and `test_operational_readiness.py::BackupTests`
goes 17 red, while `test_restore_stores.py` and `test_runtime_stores.py` pass all
30. The reader's tests cannot observe a writer-side format change, because the
only archive they ever see is one they built themselves. That blind spot is what
this file covers.

These tests are the runbook drill (backlog O1) in executable form: real writer,
real reader, real zip on disk, bytes compared. Health and dead-man are drilled in
`test_operational_readiness.py`.
"""
from __future__ import annotations

import contextlib
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.core import runtime_stores
from app.core.config import settings
from scripts.backup_stores import create_backup
from scripts.restore_stores import restore_from_backup


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@contextlib.contextmanager
def _live_install(root: Path, *, populate: bool = True, flat: bool = False):
    """A throwaway install with every declared state store under `root`.

    Yields `{basename: path}` covering the stores *and* the SQLite sidecars, so a
    caller can assert over the whole archive population rather than a subset.
    Content is derived from the basename, which is what makes a store restored to
    the wrong path detectable: swapping two files would keep the file count and
    the hash multiset intact but fail the per-name comparison.

    **Each store gets its own subdirectory unless `flat=True`, and that is not a
    stylistic choice.** `_target_path_for_arcname` falls back to the loop DB's
    directory for an arcname it does not recognise. A real install keeps every
    store in `backend/`, so under a flat layout the fallback and the correct
    answer are the same path and a misplaced restore is undetectable — the
    realistic layout is the vacuous one. This was measured: with a flat fixture,
    the round-trip test passed against a restore script reverted to its old
    four-store list, which is the defect the file exists to cover. Use
    `flat=True` only where the assertion does not depend on placement.
    """
    files: dict[str, Path] = {}
    with contextlib.ExitStack() as stack:
        for name in runtime_stores.state_setting_names():
            parent = root if flat else root / name.lower()
            target = parent / Path(getattr(settings, name)).name
            stack.enter_context(patch.object(settings, name, str(target)))
            files[target.name] = target
            if target.suffix == ".db":
                for suffix in runtime_stores.SQLITE_SIDECAR_SUFFIXES:
                    sidecar = Path(str(target) + suffix)
                    files[sidecar.name] = sidecar
        for path in files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        if populate:
            for basename, path in files.items():
                path.write_text(f"LIVE-CONTENT-{basename}\n", encoding="utf-8")
        yield files


class BackupRestoreDrillTests(unittest.TestCase):
    def test_a_real_archive_restores_every_store_to_its_configured_path(self):
        """The drill. Real writer, real reader, bytes compared per file."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live") as files:
                before = {name: _md5(path) for name, path in files.items()}
                self.assertGreaterEqual(
                    len(before), 16,
                    "the drill must cover every state store and sidecar; a "
                    "shrinking population means the fixture stopped tracking "
                    "runtime_stores",
                )

                archive = create_backup(str(base / "backups"))

                # Wipe, so a restore that silently does nothing cannot pass.
                for path in files.values():
                    path.unlink()
                self.assertEqual(
                    [p.name for p in files.values() if p.exists()], [],
                    "the wipe step did not actually remove the live files",
                )

                result = restore_from_backup(str(archive), apply=True)
                self.assertTrue(result["applied"])
                self.assertEqual(list(result["warnings"]), [])

                for name, want in sorted(before.items()):
                    with self.subTest(store=name):
                        path = files[name]
                        self.assertTrue(
                            path.exists(),
                            f"{name} was in the archive but is not at its "
                            f"configured path after the restore",
                        )
                        self.assertEqual(
                            _md5(path), want,
                            f"{name} restored with different bytes",
                        )

    def test_the_archive_contains_exactly_the_declared_population(self):
        """Writer and reader must agree on the member set, not just overlap."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live") as files:
                archive = create_backup(str(base / "backups"))
                members = set(zipfile.ZipFile(archive).namelist())
        self.assertEqual(
            members, set(files),
            f"missing={sorted(set(files) - members)} "
            f"unexpected={sorted(members - set(files))}",
        )

    def test_no_archive_member_lands_on_the_unknown_arcname_fallback(self):
        """Every member must be placed by name, not by the catch-all.

        `_target_path_for_arcname` falls back to the loop DB's directory for an
        arcname it does not recognise. On a real install every store already
        lives there, so the fallback and the correct answer coincide and a
        misplacement is invisible. Giving each store its own directory is what
        makes this assertion able to fail.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            live = base / "live"
            stack = contextlib.ExitStack()
            with stack:
                files: dict[str, Path] = {}
                for name in runtime_stores.state_setting_names():
                    target = live / name.lower() / Path(getattr(settings, name)).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stack.enter_context(patch.object(settings, name, str(target)))
                    files[target.name] = target
                    if target.suffix == ".db":
                        for suffix in runtime_stores.SQLITE_SIDECAR_SUFFIXES:
                            sidecar = Path(str(target) + suffix)
                            files[sidecar.name] = sidecar
                for basename, path in files.items():
                    path.write_text(f"LIVE-CONTENT-{basename}\n", encoding="utf-8")

                archive = create_backup(str(base / "backups"))
                for path in files.values():
                    path.unlink()
                restore_from_backup(str(archive), apply=True)

                fallback_dir = Path(settings.LOOP_DB_FILE).parent
                misplaced = []
                for basename, path in files.items():
                    if not path.exists():
                        stray = fallback_dir / basename
                        misplaced.append(
                            f"{basename} -> {'fallback dir' if stray.exists() else 'nowhere'}"
                        )
                self.assertEqual(
                    misplaced, [],
                    "these members did not reach their configured path: "
                    + "; ".join(misplaced),
                )

    def test_the_rollback_snapshot_covers_everything_the_restore_overwrites(self):
        """The pre-restore snapshot is the only way back from a wrong restore.

        Archive content differs from live content for every member, so each file
        is genuinely overwritten and the snapshot has something to capture. A
        snapshot narrower than the overwrite set means unrecoverable data.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live") as files:
                live_hashes = {name: _md5(path) for name, path in files.items()}

                archive = base / "incoming.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    for basename in files:
                        zf.writestr(basename, f"ARCHIVE-CONTENT-{basename}\n")

                result = restore_from_backup(str(archive), apply=True)
                snapshot_dir = Path(result["pre_restore_dir"])
                self.assertTrue(snapshot_dir.exists())

                overwritten = {
                    name for name, path in files.items()
                    if path.exists() and _md5(path) != live_hashes[name]
                }
                self.assertEqual(
                    overwritten, set(files),
                    "the restore did not overwrite every file, so this test is "
                    "not measuring the snapshot against a real overwrite set",
                )

                snapshotted = {
                    p.name for p in snapshot_dir.rglob("*") if p.is_file()
                }
                self.assertEqual(
                    overwritten - snapshotted, set(),
                    f"overwritten with no rollback copy: "
                    f"{sorted(overwritten - snapshotted)}",
                )

                for path in snapshot_dir.rglob("*"):
                    if path.is_file() and path.name in live_hashes:
                        with self.subTest(snapshot=path.name):
                            self.assertEqual(
                                _md5(path), live_hashes[path.name],
                                "the rollback copy does not hold the bytes that "
                                "were live before the restore",
                            )

    def test_dry_run_reports_the_targets_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live") as files:
                archive = create_backup(str(base / "backups"))
                before = {name: _md5(path) for name, path in files.items()}

                result = restore_from_backup(str(archive), apply=False)
                self.assertFalse(result["applied"])

                after = {name: _md5(path) for name, path in files.items()}
        self.assertEqual(before, after, "a dry run modified the live stores")

    def test_an_encrypted_archive_round_trips(self):
        """The configuration the runbook recommends for production.

        `pyzipper==0.4.0` is pinned in requirements.txt, so this runs rather than
        skips. If the encrypted path did not round-trip, every production backup
        would be unrecoverable — the highest-stakes path in the pair and the one
        with no test that used both halves.
        """
        key = "drill-passphrase-é中"  # non-ASCII: encoding must survive
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live") as files:
                before = {name: _md5(path) for name, path in files.items()}

                archive = create_backup(str(base / "backups"), encryption_key=key)
                # Proves the archive is genuinely encrypted rather than a plain
                # zip the restore happens to accept. Matched on the message so a
                # different failure cannot satisfy the assertion.
                with self.assertRaisesRegex(RuntimeError, "encrypted"):
                    zipfile.ZipFile(archive).read(next(iter(before)))

                for path in files.values():
                    path.unlink()

                result = restore_from_backup(str(archive), apply=True, encryption_key=key)
                self.assertTrue(result["applied"])

                for name, want in sorted(before.items()):
                    with self.subTest(store=name):
                        self.assertTrue(files[name].exists(), f"{name} not restored")
                        self.assertEqual(_md5(files[name]), want)

    def test_the_wrong_passphrase_does_not_damage_the_live_stores(self):
        """A failed restore must leave the install as it was."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live") as files:
                archive = create_backup(str(base / "backups"), encryption_key="right-key")
                before = {name: _md5(path) for name, path in files.items()}

                # "Bad password", not merely "some error": a signature mismatch or
                # a missing-file error would otherwise satisfy this.
                with self.assertRaisesRegex(RuntimeError, "(?i)bad password"):
                    restore_from_backup(str(archive), apply=True, encryption_key="wrong-key")

                after = {
                    name: (_md5(path) if path.exists() else "MISSING")
                    for name, path in files.items()
                }
        self.assertEqual(
            before, after,
            "a restore that failed on the passphrase modified the live stores",
        )

    def test_a_store_absent_at_backup_time_is_absent_after_restore(self):
        """The archive is not required to be complete; the restore must not invent."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _live_install(base / "live", populate=False) as files:
                (base / "live").mkdir(parents=True, exist_ok=True)
                kept = files["event_store.json"]
                kept.write_text("{}", encoding="utf-8")

                archive = create_backup(str(base / "backups"))
                self.assertEqual(
                    set(zipfile.ZipFile(archive).namelist()), {"event_store.json"},
                )

                kept.unlink()
                restore_from_backup(str(archive), apply=True)

                self.assertTrue(kept.exists())
                for name, path in files.items():
                    if name != "event_store.json":
                        with self.subTest(store=name):
                            self.assertFalse(
                                path.exists(),
                                f"{name} was not in the archive but exists after "
                                f"the restore",
                            )
