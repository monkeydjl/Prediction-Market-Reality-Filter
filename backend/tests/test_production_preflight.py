"""Production configuration preflight (PMRF_ENV=production).

`PMRF_ENV` used to have exactly one reader: `_resolve_env_file`, which picks
which dotenv overlay to load. Nothing then checked what the overlay actually
set, so every production-only requirement was enforced by the *template* rather
than by the code -- and a template only binds the operator who copies it. An
overlay that omits a key inherits the development default, which is how
`OPENAPI_ENABLED=true` (189 KB of schema over 184 paths, naming all 79 write
operations), `LLM_DAILY_COST_CAP_USD=0` (unlimited spend) and
`ALLOW_OPEN_WRITES=true` (every write endpoint public) were all reachable in a
process that believed it was production.

These tests pin the boundary from both sides: production refuses to boot on each
unmet requirement, and development keeps every one of those values.
"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.core import preflight  # noqa: E402
from app.core.config import settings  # noqa: E402

# A configuration that satisfies every production requirement. Each test below
# flips exactly one key, so a test failure names the requirement that moved.
PRODUCTION_OK = {
    "PMRF_ENV": "production",
    "API_WRITE_KEY": "prod-write-key",
    "ALLOW_OPEN_WRITES": False,
    "LLM_DAILY_COST_CAP_USD": 25.0,
    "OPENAPI_ENABLED": False,
    "SERVER_RELOAD": False,
    "BACKUP_SCHEDULE_ENABLED": False,
    "BACKUP_ENCRYPTION_KEY": "",
    "CORS_ALLOWED_ORIGINS": ["https://app.example.com"],
    "PHASE10_REALTIME_PUSH_ENABLED": False,
}


@contextlib.contextmanager
def configured(**overrides):
    """Apply PRODUCTION_OK plus `overrides` to the live settings object."""
    values = {**PRODUCTION_OK, **overrides}
    with contextlib.ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(patch.object(settings, name, value))
        yield


class ProductionPreflightTests(unittest.TestCase):
    """`check_production_config` returns one failure string per unmet rule."""

    def test_a_fully_configured_production_env_has_no_failures(self):
        """The negative arm. Without it, a check that always failed would
        satisfy every test below and no production deploy could ever boot."""
        with configured():
            self.assertEqual(preflight.production_config_failures(), [])

    def test_empty_write_key_fails(self):
        with configured(API_WRITE_KEY=""):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("API_WRITE_KEY" in item for item in failures), failures
        )

    def test_open_writes_fails_even_with_a_key_set(self):
        """ALLOW_OPEN_WRITES is what makes the keyless boot guard skippable.

        `lifespan` accepts `ALLOW_OPEN_WRITES=true` as an explicit opt-in to
        public writes, which is correct for a dev box and never correct for
        production. With a key *also* set the opt-in is not even coherent, and
        that combination booted with only an INFO line.
        """
        with configured(ALLOW_OPEN_WRITES=True):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("ALLOW_OPEN_WRITES" in item for item in failures), failures
        )

    def test_zero_cost_cap_fails(self):
        with configured(LLM_DAILY_COST_CAP_USD=0.0):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("LLM_DAILY_COST_CAP_USD" in item for item in failures), failures
        )

    def test_negative_cost_cap_fails(self):
        with configured(LLM_DAILY_COST_CAP_USD=-1.0):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("LLM_DAILY_COST_CAP_USD" in item for item in failures), failures
        )

    def test_openapi_enabled_fails(self):
        with configured(OPENAPI_ENABLED=True):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("OPENAPI_ENABLED" in item for item in failures), failures
        )

    def test_server_reload_fails(self):
        with configured(SERVER_RELOAD=True):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("SERVER_RELOAD" in item for item in failures), failures
        )

    def test_scheduled_backup_without_encryption_key_fails(self):
        with configured(
            BACKUP_SCHEDULE_ENABLED=True, BACKUP_ENCRYPTION_KEY=""
        ):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("BACKUP_ENCRYPTION_KEY" in item for item in failures), failures
        )

    def test_scheduled_backup_with_encryption_key_passes(self):
        """The pair, not either half: enabling backups is fine once keyed."""
        with configured(
            BACKUP_SCHEDULE_ENABLED=True, BACKUP_ENCRYPTION_KEY="passphrase"
        ):
            self.assertEqual(preflight.production_config_failures(), [])

    def test_whitespace_only_encryption_key_fails(self):
        """`BACKUP_ENCRYPTION_KEY=" "` is not configured. pyzipper would accept
        it and produce an archive encrypted under a guessable password."""
        with configured(
            BACKUP_SCHEDULE_ENABLED=True, BACKUP_ENCRYPTION_KEY="   "
        ):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("BACKUP_ENCRYPTION_KEY" in item for item in failures), failures
        )

    def test_empty_cors_origins_fails(self):
        """The shipped production template ships `CORS_ALLOWED_ORIGINS=` empty,
        which blocks every browser client instead of allowing the real one."""
        with configured(CORS_ALLOWED_ORIGINS=[]):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("CORS_ALLOWED_ORIGINS" in item for item in failures), failures
        )

    def test_wildcard_cors_origin_fails(self):
        with configured(CORS_ALLOWED_ORIGINS=["*"]):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("CORS_ALLOWED_ORIGINS" in item for item in failures), failures
        )

    def test_wildcard_among_real_origins_still_fails(self):
        """A wildcard alongside a real origin is still a wildcard to Starlette."""
        with configured(
            CORS_ALLOWED_ORIGINS=["https://app.example.com", "*"]
        ):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("CORS_ALLOWED_ORIGINS" in item for item in failures), failures
        )

    def test_every_failure_names_its_setting_and_no_secret_value(self):
        """Failures are read by whoever reads the crash log. They may name the
        *setting*, never the value: this text is the first thing pasted into an
        issue tracker."""
        secrets = {
            "API_WRITE_KEY": "write-key-SECRET-1",
            "BACKUP_ENCRYPTION_KEY": "backup-key-SECRET-2",
        }
        with configured(
            OPENAPI_ENABLED=True,  # force at least one failure
            API_WRITE_KEY=secrets["API_WRITE_KEY"],
            BACKUP_SCHEDULE_ENABLED=True,
            BACKUP_ENCRYPTION_KEY=secrets["BACKUP_ENCRYPTION_KEY"],
        ):
            failures = preflight.production_config_failures()
        blob = " ".join(failures)
        self.assertTrue(failures)
        for name, value in secrets.items():
            self.assertNotIn(value, blob, f"{name} value leaked into {blob!r}")

    def test_validate_raises_and_lists_every_failure_at_once(self):
        """One boot, one report. Failing on the first unmet rule would make an
        operator restart once per misconfigured key."""
        with configured(
            API_WRITE_KEY="",
            OPENAPI_ENABLED=True,
            SERVER_RELOAD=True,
        ):
            with self.assertRaises(RuntimeError) as caught:
                preflight.validate_production_config()
        message = str(caught.exception)
        for name in ("API_WRITE_KEY", "OPENAPI_ENABLED", "SERVER_RELOAD"):
            self.assertIn(name, message)

    def test_validate_is_a_noop_when_production_is_configured(self):
        with configured():
            self.assertIsNone(preflight.validate_production_config())


class RealtimePushPostureTests(unittest.TestCase):
    """The posture must track enforcement, not assert it.

    This class used to pin `authenticated`/`connection_limited` as unconditionally
    `False`, which was true of the code at the time: `price_stream` gated only on
    `PHASE10_REALTIME_PUSH_ENABLED` and `manager.connect` accepted every socket.
    The socket now authenticates its handshake and caps its fan-out, so the
    interesting property is no longer "both are false" -- it is that each one is
    *derived from the setting that decides the behaviour* and therefore cannot be
    made to lie by editing a constant. The behavioural side lives in
    tests/test_realtime_ws_hardening.py; this file owns the gate.
    """

    def test_posture_follows_the_settings_that_decide_the_behaviour(self):
        with configured(API_WRITE_KEY="k", WEBSOCKET_MAX_CONNECTIONS_TOTAL=10):
            self.assertTrue(preflight.realtime_push_posture()["authenticated"])
            self.assertTrue(
                preflight.realtime_push_posture()["connection_limited"]
            )
        with configured(API_WRITE_KEY="", WEBSOCKET_MAX_CONNECTIONS_TOTAL=0):
            self.assertFalse(preflight.realtime_push_posture()["authenticated"])
            self.assertFalse(
                preflight.realtime_push_posture()["connection_limited"]
            )

    def test_authenticated_is_the_enforcing_modules_own_answer(self):
        """Not a second copy of the rule. A posture that re-derived "is auth on"
        could disagree with the handshake, and the disagreement would be silent.
        """
        from app.realtime import ws_auth

        with configured(API_WRITE_KEY="k"):
            self.assertEqual(
                preflight.realtime_push_posture()["authenticated"],
                ws_auth.auth_is_enforced(),
            )

    def test_realtime_push_enabled_fails_production_when_a_cap_is_unset(self):
        """The flag alone is no longer the failure -- an unenforced cap is.

        `WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=0` is the shape that matters: an
        operator writing 0 to mean "no limit" gets a refusal instead of the
        unbounded endpoint they asked for by accident.
        """
        with configured(
            PHASE10_REALTIME_PUSH_ENABLED=True,
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=0,
        ):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("PHASE10_REALTIME_PUSH_ENABLED" in item for item in failures),
            failures,
        )
        self.assertTrue(
            any(
                "WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT" in item
                for item in failures
            ),
            "the failure must name the cap the operator has to fix",
        )

    def test_realtime_push_enabled_passes_production_once_enforced(self):
        """The arm that did not exist before: the gate has to be openable, or
        the feature is permanently unshippable and the rule is a ban."""
        with configured(PHASE10_REALTIME_PUSH_ENABLED=True):
            self.assertEqual(preflight.production_config_failures(), [])

    def test_realtime_push_disabled_passes_production(self):
        with configured(PHASE10_REALTIME_PUSH_ENABLED=False):
            self.assertEqual(preflight.production_config_failures(), [])

    def test_an_unmet_posture_is_logged_at_warning(self):
        """"Do not silently default it on" also means: say what it is.

        Now reached by leaving a cap unset rather than by the flag alone, since
        the flag on its own is a satisfiable state.
        """
        with configured(
            PHASE10_REALTIME_PUSH_ENABLED=True,
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=0,
        ):
            with self.assertLogs("app.core.preflight", level="WARNING") as logs:
                preflight.log_realtime_push_posture()
        blob = " ".join(logs.output)
        self.assertIn("PHASE10_REALTIME_PUSH_ENABLED", blob)
        self.assertIn("WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT", blob)

    def test_a_satisfied_posture_says_so_without_warning(self):
        """A startup that warns even when the posture is met trains operators to
        ignore the line, which is how the original silent default survived."""
        with configured(PHASE10_REALTIME_PUSH_ENABLED=True):
            with self.assertLogs("app.core.preflight", level="INFO") as logs:
                preflight.log_realtime_push_posture()
        self.assertEqual(
            [rec for rec in logs.records if rec.levelname == "WARNING"], []
        )
        self.assertIn("caps are finite", " ".join(logs.output))


class NonProductionKeepsDevelopmentExperienceTests(unittest.TestCase):
    """The gate is production-only on purpose.

    Every rule above describes a value that is *correct* on a dev box: no write
    key, interactive docs, `--reload`, an unlimited (unused) cost cap on a
    keyless LLM route. Applying them everywhere would make the repo unusable
    locally, so the tests below are the other half of the contract.
    """

    def test_development_is_not_gated(self):
        with configured(
            PMRF_ENV="development",
            API_WRITE_KEY="",
            ALLOW_OPEN_WRITES=True,
            LLM_DAILY_COST_CAP_USD=0.0,
            OPENAPI_ENABLED=True,
            SERVER_RELOAD=True,
            CORS_ALLOWED_ORIGINS=["*"],
            BACKUP_SCHEDULE_ENABLED=True,
            BACKUP_ENCRYPTION_KEY="",
            PHASE10_REALTIME_PUSH_ENABLED=True,
        ):
            self.assertEqual(preflight.production_config_failures(), [])
            preflight.validate_production_config()  # must not raise

    def test_staging_is_not_gated(self):
        """Staging is a rehearsal environment, not the thing being protected.
        `.env.staging.example` deliberately keeps docs on and a dev cost cap."""
        with configured(
            PMRF_ENV="staging",
            OPENAPI_ENABLED=True,
            LLM_DAILY_COST_CAP_USD=0.0,
        ):
            self.assertEqual(preflight.production_config_failures(), [])

    def test_is_production_reads_the_setting_case_insensitively(self):
        for value, expected in (
            ("production", True),
            ("PRODUCTION", True),
            ("  production  ", True),
            ("development", False),
            ("staging", False),
            ("", False),
        ):
            with self.subTest(value=value):
                with patch.object(settings, "PMRF_ENV", value):
                    self.assertEqual(preflight.is_production(), expected)


class PreflightIsReachableFromEveryProductionEntrypointTests(unittest.TestCase):
    """A check with no caller is the defect, not the check.

    Three processes read this configuration in production: the API (uvicorn ->
    `app.main.lifespan`), the scheduler worker (`scripts/run_scheduler.py`, its
    own systemd unit) and the backup script. The scheduler is the one that spends
    LLM budget unattended and writes the backups, so a gate on the API alone
    would leave the money-spending process ungated.
    """

    def test_api_lifespan_refuses_a_misconfigured_production_boot(self):
        from app.main import app

        with configured(OPENAPI_ENABLED=True), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertRaisesRegex(RuntimeError, "OPENAPI_ENABLED"):
                with TestClient(app):
                    pass

    def test_api_lifespan_boots_a_correctly_configured_production(self):
        from app.main import app

        started = {"n": 0}
        with configured(), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.sqlite_db.maintain_all", return_value={
                    "ok": True, "failed": [], "stores": {},
                }), \
                patch("app.main.start_scheduler",
                      lambda: started.__setitem__("n", started["n"] + 1)), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        self.assertEqual(started["n"], 1)

    def test_scheduler_worker_refuses_a_misconfigured_production_boot(self):
        import asyncio

        from scripts.run_scheduler import run_scheduler_worker

        started = {"n": 0}
        with configured(ALLOW_OPEN_WRITES=True), \
                patch("scripts.run_scheduler.start_scheduler",
                      lambda: started.__setitem__("n", started["n"] + 1)), \
                patch("scripts.run_scheduler.stop_scheduler", lambda: None), \
                patch("scripts.run_scheduler.sqlite_db.maintain",
                      return_value={"ok": True}):
            with self.assertRaisesRegex(RuntimeError, "ALLOW_OPEN_WRITES"):
                asyncio.run(run_scheduler_worker())
        self.assertEqual(
            started["n"], 0, "the scheduler must not start jobs after a failed preflight"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
