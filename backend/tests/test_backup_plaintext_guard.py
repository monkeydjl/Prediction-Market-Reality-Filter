"""Production must never write a plaintext backup archive.

`deploy/docker-compose.yml` sets `BACKUP_SCHEDULE_ENABLED=true` and never set
`BACKUP_ENCRYPTION_KEY`; neither `.env.production.example` nor
`.env.staging.example` mentioned the key at all. The documented Docker production
deployment therefore wrote a daily plaintext ZIP of every state store -- events,
committed predictions, kernel history -- into the backup volume.

The boundary is `PMRF_ENV`. Non-production keeps the documented legacy behaviour
(empty key -> plaintext), because a dev box archiving a temp store onto its own
disk is not the thing being protected. Production refuses, and it refuses inside
`create_backup` rather than only at startup preflight, so a manual CLI run cannot
produce one either.
"""
import contextlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402
from scripts import backup_stores  # noqa: E402

# A key value distinctive enough that a substring search for it is meaningful.
SECRET_KEY = "backup-passphrase-DO-NOT-LOG-9f3a"


@contextlib.contextmanager
def one_store_in(base: Path, payload: str = '{"secret": 1}'):
    """Point every state store at `base`, with only event_store.json present.

    Mirrors the sibling fixtures in test_operational_readiness.py: the other
    paths are aimed at non-existent files so the archive contents are
    predictable (`backup_paths` skips what does not exist).
    """
    event_store = base / "event_store.json"
    event_store.write_text(payload, encoding="utf-8")
    with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
            patch.object(settings, "EVENT_AUDIT_FILE", str(base / "no.jsonl")), \
            patch.object(settings, "EVENT_CACHE_FILE", str(base / "no.json")), \
            patch.object(settings, "LOOP_DB_FILE", str(base / "no.db")), \
            patch.object(settings, "KERNEL_DB_FILE", str(base / "no-k.db")), \
            patch.object(
                settings, "WORLD_CUP_PREDICTION_DB_FILE", str(base / "no-wc.db")), \
            patch.object(
                settings, "DOMAIN_RELIABILITY_DB_PATH", str(base / "no-dr.db")), \
            patch.object(settings, "SPORTS_FACT_FILE", str(base / "no-sf.json")):
        yield event_store


def requires_pyzipper(test):
    try:
        import pyzipper  # noqa: F401
    except ImportError:
        test.skipTest("pyzipper not installed")


def archives_in(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("pmrf-backup-*.zip"))


class ProductionRefusesPlaintextTests(unittest.TestCase):

    def test_production_without_a_key_refuses_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                with self.assertRaises(RuntimeError) as caught:
                    backup_stores.create_backup(str(backups))
            self.assertIn("BACKUP_ENCRYPTION_KEY", str(caught.exception))
            # A half-written archive is worse than none: the operator would see a
            # file in the volume and believe the backup ran.
            self.assertEqual(
                archives_in(backups), [],
                "production must leave no archive behind when it refuses",
            )

    def test_an_explicit_empty_key_argument_is_also_refused(self):
        """`--encryption-key ''` is an explicit request for plaintext.

        The guard reads the *resolved* key, so opting out on the command line
        cannot get past it either.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                with self.assertRaises(RuntimeError):
                    backup_stores.create_backup(
                        str(base / "backups"), encryption_key=""
                    )

    def test_a_whitespace_only_key_is_not_a_key(self):
        """pyzipper would accept `" "` and produce an archive encrypted under a
        guessable password, which reads as success everywhere downstream."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", "   "):
                with self.assertRaises(RuntimeError):
                    backup_stores.create_backup(str(base / "backups"))

    def test_production_with_a_key_writes_an_encrypted_archive(self):
        """The positive arm. Without it, a guard that refused unconditionally
        would satisfy every test above and no production backup could run."""
        requires_pyzipper(self)
        import pyzipper

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                archive = backup_stores.create_backup(str(base / "backups"))

            self.assertTrue(archive.exists())
            # Not readable as a plain zip => genuinely encrypted.
            with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                with zipfile.ZipFile(archive) as zf:
                    zf.read("event_store.json")
            with pyzipper.AESZipFile(archive) as zf:
                zf.setpassword(SECRET_KEY.encode("utf-8"))
                self.assertEqual(
                    zf.read("event_store.json").decode("utf-8"), '{"secret": 1}'
                )


class CliEntrypointRunsTheFullPreflightTests(unittest.TestCase):
    """`main()` is a production entrypoint and owes the whole preflight.

    `deploy/prediction-market-reality-filter-backup.service` runs
    `python scripts/backup_stores.py`, so the timer boots a process that never
    touches the API lifespan or `run_scheduler.py` -- the two places
    `validate_production_config()` was already called. What it did have was
    `_resolve_encryption_key`, which enforces exactly one of the eight production
    rules. The other seven were unenforced in this process, so a deployment the
    API refuses to serve could still run its nightly backup and report success.

    Every test here keeps `BACKUP_ENCRYPTION_KEY` populated on purpose. A test
    that left it empty would be refused by the pre-existing plaintext guard and
    would pass before the fix, proving nothing about the preflight.
    """

    #: Production-valid except where a test overrides it. Mirrors PRODUCTION_OK in
    #: test_production_preflight.py; kept local so neither file's baseline can be
    #: quietly changed on the other's behalf.
    PRODUCTION_OK = {
        "PMRF_ENV": "production",
        "API_WRITE_KEY": "prod-write-key",
        "ALLOW_OPEN_WRITES": False,
        "LLM_DAILY_COST_CAP_USD": 25.0,
        "OPENAPI_ENABLED": False,
        "SERVER_RELOAD": False,
        "BACKUP_SCHEDULE_ENABLED": False,
        "BACKUP_ENCRYPTION_KEY": SECRET_KEY,
        "CORS_ALLOWED_ORIGINS": ["https://app.example.com"],
        "PHASE10_REALTIME_PUSH_ENABLED": False,
    }

    @contextlib.contextmanager
    def _cli(self, base: Path, argv: list[str], **overrides):
        """Run the CLI the way the systemd unit does, in a temp store tree."""
        values = {**self.PRODUCTION_OK, **overrides}
        with contextlib.ExitStack() as stack:
            stack.enter_context(one_store_in(base))
            for name, value in values.items():
                stack.enter_context(patch.object(settings, name, value))
            stack.enter_context(
                patch.object(sys, "argv", ["backup_stores.py", *argv])
            )
            yield

    def test_the_baseline_this_class_uses_really_is_production_valid(self):
        """Without this, every refusal below could come from a baseline that was
        broken in some unrelated way, and the injected violation would be
        decorative."""
        from app.core import preflight

        with contextlib.ExitStack() as stack:
            for name, value in self.PRODUCTION_OK.items():
                stack.enter_context(patch.object(settings, name, value))
            self.assertEqual(preflight.production_config_failures(), [])

    def test_an_unrelated_production_violation_refuses_the_cli_backup(self):
        """`ALLOW_OPEN_WRITES=true` in production, with a valid backup key.

        Nothing in the backup path can see this setting, which is the point: the
        encryption guard passes, and pre-fix `main()` returned 0 and wrote an
        archive for a deployment whose write endpoints were public.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with self._cli(
                base, ["--output-dir", str(backups)], ALLOW_OPEN_WRITES=True
            ):
                with self.assertRaises(RuntimeError) as caught:
                    backup_stores.main()

            message = str(caught.exception)
            self.assertIn("ALLOW_OPEN_WRITES", message)
            self.assertEqual(
                archives_in(backups), [],
                "the refusal must leave no archive behind",
            )

    def test_the_refusal_is_the_preflight_verdict_not_a_local_copy(self):
        """The gate must delegate, not re-implement.

        A second copy of the rules here would drift from `preflight` silently, so
        this injects a sentinel failure into the preflight's own verdict and
        requires it to reach the operator verbatim. It also pins that *any*
        failure refuses, not only the ones this file thought to enumerate.
        """
        sentinel = "SOME_FUTURE_SETTING is not configured"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with self._cli(base, ["--output-dir", str(backups)]):
                with patch(
                    "app.core.preflight.production_config_failures",
                    return_value=[sentinel],
                ):
                    with self.assertRaises(RuntimeError) as caught:
                        backup_stores.main()

            self.assertIn(sentinel, str(caught.exception))
            self.assertEqual(archives_in(backups), [])

    def test_a_valid_production_config_still_backs_up(self):
        """The positive arm. A gate that refused every production run would
        satisfy both tests above and silently end production backups."""
        requires_pyzipper(self)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with self._cli(base, ["--output-dir", str(backups)]):
                self.assertEqual(backup_stores.main(), 0)

            written = archives_in(backups)
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].exists())

    def test_non_production_is_not_gated_by_the_preflight(self):
        """The preflight is a no-op off production, and the CLI must stay usable
        on a dev box whose config would never satisfy it."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            with self._cli(
                base,
                ["--output-dir", str(backups)],
                PMRF_ENV="development",
                ALLOW_OPEN_WRITES=True,
                API_WRITE_KEY="",
                BACKUP_ENCRYPTION_KEY="",
                CORS_ALLOWED_ORIGINS=["*"],
            ):
                self.assertEqual(backup_stores.main(), 0)

            self.assertEqual(len(archives_in(backups)), 1)

    def test_an_argument_error_is_still_reported_as_an_argument_error(self):
        """Ordering, pinned. The gate runs after argparse validation, so a
        misconfigured production box still gets told its `--keep` is invalid
        instead of a config error that hides the typo it can actually fix.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self._cli(
                base, ["--keep", "0"], ALLOW_OPEN_WRITES=True
            ):
                # argparse.error() exits 2; a RuntimeError here would mean the
                # preflight had pre-empted it.
                with self.assertRaises(SystemExit) as caught:
                    backup_stores.main()
            self.assertEqual(caught.exception.code, 2)

    def test_the_refusal_never_prints_the_backup_key(self):
        """The message reaches the systemd journal and, via the scheduler path,
        the `loop_runs.error` column that /api/health returns."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self._cli(
                base,
                ["--output-dir", str(base / "backups")],
                ALLOW_OPEN_WRITES=True,
            ):
                with self.assertRaises(RuntimeError) as caught:
                    backup_stores.main()
            self.assertNotIn(SECRET_KEY, str(caught.exception))
            self.assertNotIn("prod-write-key", str(caught.exception))


class NonProductionBoundaryTests(unittest.TestCase):
    """The other half of the boundary, stated explicitly rather than implied.

    `config.py` documents "when empty the legacy plaintext zip is produced" and
    `test_backup_plaintext_when_key_empty` in test_operational_readiness.py
    depends on it. That stays true off production -- otherwise every dev box and
    the whole restore-drill suite would need a passphrase.
    """

    def test_development_without_a_key_still_writes_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "development"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                archive = backup_stores.create_backup(str(base / "backups"))
            with zipfile.ZipFile(archive) as zf:
                self.assertIn("event_store.json", zf.namelist())

    def test_staging_without_a_key_still_writes_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "staging"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                archive = backup_stores.create_backup(str(base / "backups"))
            with zipfile.ZipFile(archive) as zf:
                self.assertIn("event_store.json", zf.namelist())

    def test_development_with_a_key_still_encrypts(self):
        """The guard changes when plaintext is *refused*, not when a configured
        key is honored."""
        requires_pyzipper(self)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "development"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                archive = backup_stores.create_backup(str(base / "backups"))
            with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                with zipfile.ZipFile(archive) as zf:
                    zf.read("event_store.json")


class KeyNeverLeaksTests(unittest.TestCase):

    def test_the_refusal_message_does_not_contain_the_configured_key(self):
        """The refusal fires when the key is *absent*, but the message is built
        while other secrets are in scope -- assert on the real one anyway."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "API_WRITE_KEY", SECRET_KEY), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                with self.assertRaises(RuntimeError) as caught:
                    backup_stores.create_backup(str(base / "backups"))
        self.assertNotIn(SECRET_KEY, str(caught.exception))

    def test_the_archive_filename_does_not_contain_the_key(self):
        requires_pyzipper(self)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                archive = backup_stores.create_backup(str(base / "backups"))
        self.assertNotIn(SECRET_KEY, archive.name)
        self.assertNotIn(SECRET_KEY, str(archive))
        self.assertRegex(archive.name, r"^pmrf-backup-\d{8}-\d{6}Z\.zip$")

    def test_the_scheduler_job_records_a_failure_without_leaking_the_key(self):
        """The job's `except` writes `str(exc)` into the loop_runs `error`
        column, which `/api/health` returns verbatim to an authenticated caller.
        """
        from app.core import scheduler

        recorded: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "API_WRITE_KEY", SECRET_KEY), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""), \
                    patch.object(scheduler, "_start_run", return_value="run-1"), \
                    patch.object(
                        scheduler, "_finish_run",
                        lambda run_id, status, **kw: recorded.append(
                            (status, str(kw.get("error") or ""))
                        )):
                import asyncio
                asyncio.run(scheduler._job_backup_stores())

        self.assertEqual([status for status, _ in recorded], ["failed"], recorded)
        self.assertIn("BACKUP_ENCRYPTION_KEY", recorded[0][1])
        self.assertNotIn(SECRET_KEY, recorded[0][1])


class RestoreStaysCompatibleTests(unittest.TestCase):
    """Disaster recovery has to still work on what production now writes.

    A guard that forced encryption but broke the restore path would trade a
    confidentiality problem for an availability one.
    """

    def _restore(self, archive: Path, target_dir: Path, key: str | None):
        sys.path.insert(0, str(_BACKEND / "scripts"))
        import restore_stores

        with patch.object(
            restore_stores, "_check_service_running", return_value=False
        ):
            return restore_stores.restore_from_backup(
                archive, apply=True, encryption_key=key, target_dir=target_dir,
            )

    def test_a_production_encrypted_archive_round_trips(self):
        requires_pyzipper(self)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base, payload='{"round": "trip"}'), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                archive = backup_stores.create_backup(str(base / "backups"))

                target = base / "restored"
                result = self._restore(archive, target, SECRET_KEY)

            self.assertTrue(result["applied"])
            self.assertEqual(
                (target / "event_store.json").read_text(encoding="utf-8"),
                '{"round": "trip"}',
            )

    def test_a_plaintext_archive_is_not_mistaken_for_an_encrypted_one(self):
        """Detection reads the archive's own flag bits, not the configured key.

        A restore that decided "encrypted" from `BACKUP_ENCRYPTION_KEY` being set
        would fail on every legacy archive taken before the key existed -- which
        is exactly the archive an operator reaches for during a migration.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base, payload='{"legacy": true}'), \
                    patch.object(settings, "PMRF_ENV", "development"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                archive = backup_stores.create_backup(str(base / "backups"))

            # A key is configured now; the archive predates it and is plaintext.
            with one_store_in(base), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                target = base / "restored"
                result = self._restore(archive, target, SECRET_KEY)

            self.assertTrue(result["applied"])
            self.assertEqual(
                (target / "event_store.json").read_text(encoding="utf-8"),
                '{"legacy": true}',
            )

    def test_an_encrypted_archive_without_a_key_is_refused_not_read_as_garbage(self):
        """The inverse arm: the two archive kinds must not be confusable in
        either direction. Silently restoring ciphertext as store content would
        corrupt every store it touched."""
        requires_pyzipper(self)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with one_store_in(base), \
                    patch.object(settings, "PMRF_ENV", "production"), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", SECRET_KEY):
                archive = backup_stores.create_backup(str(base / "backups"))

            with one_store_in(base), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                with self.assertRaises(RuntimeError):
                    self._restore(archive, base / "restored", "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
