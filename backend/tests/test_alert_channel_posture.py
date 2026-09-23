"""What reaches an operator when a job fails -- and what says so at boot.

Three push channels exist, and each needs *two* settings to be live: Sentry
(``SENTRY_DSN`` plus an importable ``sentry_sdk``), the scheduler-failure webhook
(``SCHEDULER_FAILURE_ALERT_ENABLED`` plus ``SCHEDULER_FAILURE_ALERT_WEBHOOK_URL``)
and the calibration-drift webhook (``DRIFT_ALERTS_ENABLED`` plus
``DRIFT_ALERT_WEBHOOK_URL``). All five ship off, and none of the five is named in
``.env.staging.example``, ``.env.production.example``,
``deploy/docker-compose.yml``, ``deploy/Dockerfile`` or the systemd unit.
Measured by merging the base template with each overlay the way
``_load_env_files`` does:

    PMRF_ENV=staging     channels live: NONE
    PMRF_ENV=production  channels live: NONE
    code defaults        channels live: NONE

A failed job is still *recorded* -- a ``failed`` ledger row,
``pmrf_scheduler_failed_runs_total``, and ``/api/health`` answering 503 -- so
anybody who looks can see it. Nothing pushes: the entire trace of a 3am failure
is a line in the local log file. That is a fine default for a dev box, and it
was the one posture nothing announced. ``main.lifespan`` reads ``SENTRY_DSN``
only to hand it to ``init_sentry``, which logs the neutral "Sentry disabled"
either way, and the other four settings it never reads at all.

Warn rather than refuse to boot, for the same reason as
``LLM_DAILY_COST_CAP_USD=0``: absent alerting breaks nothing that already works,
and a fail-closed check would take down every existing install.
"""
import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings


class AlertChannelCensusTests(unittest.TestCase):
    """`_live_alert_channels` is what the boot line reports, so it is what has
    to be right: a channel counted while it cannot reach anybody turns the
    reassuring line into the defect."""

    def _channels(self, sentry_live: bool = False, **overrides) -> list[str]:
        from app.main import _live_alert_channels

        with patch.multiple(settings, **overrides):
            return _live_alert_channels(sentry_live)

    def test_the_shipped_defaults_leave_no_push_channel(self):
        self.assertEqual(
            self._channels(
                SENTRY_DSN="",
                SCHEDULER_FAILURE_ALERT_ENABLED=False,
                SCHEDULER_FAILURE_ALERT_WEBHOOK_URL="",
                DRIFT_ALERTS_ENABLED=False,
                DRIFT_ALERT_WEBHOOK_URL="",
            ),
            [],
        )

    def test_sentry_counts_only_when_the_sdk_initialised(self):
        """A DSN is not a channel; an initialised client is.

        ``init_sentry`` returns False when the DSN is set but ``sentry_sdk`` is
        not installed -- it logs that and moves on. Reading the setting instead
        of the return value would report a channel that captures nothing, which
        is the failure mode this whole file is about.
        """
        dsn = "https://public@example.ingest.sentry.io/1"
        self.assertEqual(self._channels(True, SENTRY_DSN=dsn), ["Sentry"])
        self.assertEqual(self._channels(False, SENTRY_DSN=dsn), [])

    def test_an_enabled_webhook_with_no_url_is_not_a_channel(self):
        """Enabled-but-unaddressed reaches the log and a no-op Sentry call.

        With ``SCHEDULER_FAILURE_ALERT_ENABLED=true`` and no URL the dispatcher
        runs to completion: it writes ``[SCHEDULER-FAILURE-ALERT]`` at WARNING
        and calls ``capture_message``, which is a no-op with no DSN. Both halves
        are needed before anything leaves the process.
        """
        self.assertEqual(
            self._channels(
                SCHEDULER_FAILURE_ALERT_ENABLED=True,
                SCHEDULER_FAILURE_ALERT_WEBHOOK_URL="",
            ),
            [],
        )
        self.assertEqual(
            self._channels(
                SCHEDULER_FAILURE_ALERT_ENABLED=False,
                SCHEDULER_FAILURE_ALERT_WEBHOOK_URL="https://hooks.example/x",
            ),
            [],
        )

    def test_each_channel_is_reported_by_its_own_name(self):
        self.assertEqual(
            self._channels(
                True,
                SENTRY_DSN="https://public@example.ingest.sentry.io/1",
                SCHEDULER_FAILURE_ALERT_ENABLED=True,
                SCHEDULER_FAILURE_ALERT_WEBHOOK_URL="https://hooks.example/sched",
                DRIFT_ALERTS_ENABLED=True,
                DRIFT_ALERT_WEBHOOK_URL="https://hooks.example/drift",
            ),
            ["Sentry", "scheduler-failure webhook", "calibration-drift webhook"],
        )

    def test_every_webhook_setting_is_in_the_census(self):
        """The partition, so a fourth channel cannot arrive uncounted.

        A hand-kept list of channels is the shape that let this finding exist in
        the first place. Both sides are read from the source: the population
        from `Settings`' own attribute names, the coverage from the census
        function's body.
        """
        from app import main

        declared = sorted(
            name for name in vars(type(settings))
            if name.endswith("_WEBHOOK_URL")
        )
        self.assertGreaterEqual(len(declared), 2, declared)
        source = inspect.getsource(main._live_alert_channels)
        uncovered = [name for name in declared if name not in source]
        self.assertEqual(
            uncovered, [],
            f"these webhook settings exist but _live_alert_channels never asks "
            f"about them, so a deploy that configures one is still reported as "
            f"having no channel: {uncovered}",
        )


class StartupPostureTests(unittest.TestCase):
    """The boot line itself. The census above can be right while nothing says
    it out loud, which is the state this replaced."""

    def _boot(self, *, sentry_live: bool = False, **overrides) -> list[str]:
        from app.main import app

        defaults = {
            "API_WRITE_KEY": "secret",
            "SENTRY_DSN": "",
            "SCHEDULER_FAILURE_ALERT_ENABLED": False,
            "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL": "",
            "DRIFT_ALERTS_ENABLED": False,
            "DRIFT_ALERT_WEBHOOK_URL": "",
        }
        defaults.update(overrides)
        from fastapi.testclient import TestClient

        with patch.multiple(settings, **defaults), \
                patch("app.utils.sentry.init_sentry", return_value=sentry_live), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertLogs("app.main", level="INFO") as captured:
                with TestClient(app):
                    pass
        return captured.output

    def test_boot_warns_when_nothing_would_notify(self):
        output = self._boot()
        warnings = [
            line for line in output
            if line.startswith("WARNING") and "alert push channel" in line
        ]
        self.assertEqual(len(warnings), 1, output)
        # The line has to say what an operator still has, or it reads as "you
        # are blind" when the ledger and /api/health both hold the failure.
        self.assertIn("/api/health", warnings[0])

    def test_boot_names_the_channels_that_are_live(self):
        output = self._boot(
            sentry_live=True,
            SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        )
        live = [line for line in output if "Alert push channels live" in line]
        self.assertEqual(len(live), 1, output)
        self.assertIn("Sentry", live[0])
        self.assertFalse(
            [ln for ln in output if "No alert push channel" in ln], output
        )


class DeployArtefactTests(unittest.TestCase):
    """Where an operator configures is where the knobs have to be visible.

    Docker and systemd never read the overlay templates at all --
    ``.dockerignore`` keeps ``.env.*`` out of the build context -- so the boot
    warning is the only surface those two deployments share with the others.
    That is asserted here as the reason the fix is a log line and not a template
    edit alone.
    """

    def test_no_deploy_artefact_configures_a_channel(self):
        deploy = Path(__file__).resolve().parent.parent.parent / "deploy"
        keys = (
            "SENTRY_DSN",
            "SCHEDULER_FAILURE_ALERT_ENABLED",
            "DRIFT_ALERTS_ENABLED",
        )
        for name in ("docker-compose.yml", "prediction-market-reality-filter.service"):
            path = deploy / name
            with self.subTest(artefact=name):
                self.assertTrue(path.is_file(), f"{name} is missing")
                text = path.read_text(encoding="utf-8")
                named = [k for k in keys if k in text]
                self.assertEqual(
                    named, [],
                    f"{name} now names {named}. That is fine, but the boot "
                    f"warning's claim that no deploy artefact configures a "
                    f"channel is what justified putting the fix in the log "
                    f"rather than in these files -- update this test with the "
                    f"new posture",
                )

    def test_the_warning_is_reachable_from_the_lifespan(self):
        """A census function nobody calls is the defect one step later."""
        from app import main

        tree = ast.parse(inspect.getsource(main.lifespan))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_live_alert_channels", called)

class RunbookPostureTests(unittest.TestCase):
    """The RUNBOOK framed OFF as the safe default.

    Both alert sections read as tuning advice -- "set true only after
    webhook/Sentry ready", "Default OFF keeps installs byte-identical to pre-E8"
    -- and neither says what a production install with all five off actually
    has, which is nothing that pushes. Following that advice literally is how a
    deploy ends up with no channel and no reason to think anything is missing.

    Narrow on purpose, like `test_runbook_restore_contract.py`: it pins the
    decision that was wrong, not the prose.
    """

    def _runbook(self) -> str:
        path = (Path(__file__).resolve().parents[2]
                / "docs" / "ops" / "RUNBOOK.md")
        self.assertTrue(path.is_file(), f"{path} is missing")
        return path.read_text(encoding="utf-8")

    def test_required_production_settings_names_the_alert_posture(self):
        """The checklist an operator works through before the first boot.

        The two alert sections live 600 lines down under Monitoring; a reader who
        configures from the checklist alone never reaches them.
        """
        text = self._runbook()
        start = text.find("## Required Production Settings")
        end = text.find("\n## ", start + 1)
        section = text[start:end if end > 0 else len(text)]
        self.assertIn(
            "SCHEDULER_FAILURE_ALERT_ENABLED", section,
            "the production checklist never names an alert channel, so an "
            "operator who works through it top to bottom ships with none",
        )
        self.assertIn(
            "No alert push channel", section,
            "the checklist does not quote the startup WARNING, which is the "
            "one signal a Docker or systemd install gets (neither reads the "
            "overlay templates)",
        )

    def test_the_scheduler_alert_section_says_what_off_costs(self):
        text = self._runbook()
        start = text.find("### Scheduler failure alerts (E8)")
        self.assertGreater(start, 0, "the E8 alert section is gone")
        end = text.find("\n### ", start + 1)
        section = text[start:end if end > 0 else len(text)]
        self.assertIn(
            "pull-only", section,
            "the section lists the three things a failed job already does "
            "without saying that all three have to be looked at; that framing "
            "is what made OFF read as the complete posture",
        )


if __name__ == "__main__":
    unittest.main()
