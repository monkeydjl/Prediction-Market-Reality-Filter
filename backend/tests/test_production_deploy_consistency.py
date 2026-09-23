"""The documented production deployments must be able to *be* production.

Two gaps, one cause. `app.core.preflight` gates on `PMRF_ENV=production`, and
`_load_env_files` refuses to start when a named overlay is not on disk -- a guard
that was measured to matter, because an absent overlay booted every unnamed key at
its development value. But `.dockerignore` keeps `.env.*` out of the build
context, so `.env.production` is *always* absent inside the container. Compose
therefore had to leave `PMRF_ENV` unset, which means the documented Docker
production deployment could never declare itself production and the whole
preflight was unreachable there.

`PMRF_ENV_FILE_REQUIRED=false` is how a deployment says "I configure the process
environment directly, there is no overlay to miss". It defaults to true, so every
dotenv-based deploy (systemd, bare metal) keeps the guard exactly as measured.

The rest of this module keeps the shipped templates honest: every rule the
preflight enforces has to be named where an operator configures it, and compose's
own values have to satisfy the rules compose controls.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.conftest import _real_load_dotenv  # noqa: E402

from app.core import preflight  # noqa: E402

_REPO = _BACKEND.parent
PRODUCTION_EXAMPLE = _BACKEND / ".env.production.example"
COMPOSE = _REPO / "deploy" / "docker-compose.yml"


def assignments(path: Path) -> dict[str, str]:
    """Uncommented `KEY=value` pairs, last assignment wins."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("-").strip()
        if "=" not in stripped or stripped.startswith("#"):
            continue
        name, raw = stripped.split("=", 1)
        if name.strip().isupper():
            out[name.strip()] = raw.split("#", 1)[0].strip()
    return out


class OverlayRequirementTests(unittest.TestCase):
    """`PMRF_ENV=production` has to be reachable without an overlay file --
    and only when the deployment says so."""

    def _load_in(self, tmp: str, env: dict[str, str]) -> None:
        from app.core.config import _load_env_files

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            with patch.dict(os.environ, env, clear=False):
                with patch("app.core.config.load_dotenv", _real_load_dotenv):
                    _load_env_files()
        finally:
            os.chdir(old_cwd)

    def test_a_missing_overlay_still_refuses_by_default(self):
        """The guard keeps its measured strength for every dotenv deploy. This is
        the arm that would silently disappear if the opt-out defaulted wrong."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._load_in(tmp, {"PMRF_ENV": "production"})

    def test_a_missing_overlay_still_refuses_when_explicitly_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._load_in(tmp, {
                    "PMRF_ENV": "production",
                    "PMRF_ENV_FILE_REQUIRED": "true",
                })

    def test_a_deployment_that_configures_the_environment_directly_may_skip_it(self):
        """The Docker case: no overlay exists in the image by design.

        The assertion is deliberately stronger than "this did not raise". What
        compose depends on is that after the skip the *process* environment still
        declares production and still carries what it set directly -- a load that
        cleared or re-derived the environment would boot a container that reaches
        no production check at all, which is the exact gap this file exists for.
        """
        from app.core.config import _load_env_files

        env = {
            "PMRF_ENV": "production",
            "PMRF_ENV_FILE_REQUIRED": "false",
            "API_WRITE_KEY": "set-directly-never-via-an-overlay",
        }
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, env, clear=False):
                    with patch("app.core.config.load_dotenv", _real_load_dotenv):
                        self.assertIsNone(_load_env_files())
                    self.assertEqual(os.environ["PMRF_ENV"], "production")
                    self.assertEqual(
                        os.environ["API_WRITE_KEY"],
                        "set-directly-never-via-an-overlay",
                    )
            finally:
                os.chdir(old_cwd)

    def test_skipping_the_overlay_says_so_out_loud(self):
        """It is the one configuration where PMRF_ENV names a file nothing read,
        so it may not be silent -- silence is what the original guard was for."""
        # WARNING, not INFO: `_load_env_files` runs while `app.core.config` is
        # imported, which is *before* `app.main` calls `setup_logging()`. The root
        # logger has no handler yet, so logging's lastResort only prints WARNING
        # and above -- an INFO line here would pass this test and never appear in
        # a real deployment.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("app.core.config", level="WARNING") as logs:
                self._load_in(tmp, {
                    "PMRF_ENV": "production",
                    "PMRF_ENV_FILE_REQUIRED": "false",
                })
        blob = " ".join(logs.output)
        self.assertIn(".env.production", blob)
        self.assertIn("PMRF_ENV_FILE_REQUIRED", blob)

    def test_an_unrecognized_pmrf_env_still_raises_even_with_the_opt_out(self):
        """The opt-out excuses a missing *file*, never a typo in the name."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._load_in(tmp, {
                    "PMRF_ENV": "prod",
                    "PMRF_ENV_FILE_REQUIRED": "false",
                })


class ProductionTemplateMatchesThePreflightTests(unittest.TestCase):
    """Every rule the code enforces has to be named where it is configured.

    A rule the template does not mention is a boot failure an operator cannot
    anticipate: the overlay only overrides the keys it names, so an unmentioned
    key arrives at its development value and the process refuses to start with no
    hint in the file they copied.
    """

    def test_every_preflight_setting_is_named_in_the_production_example(self):
        assigned = assignments(PRODUCTION_EXAMPLE)
        missing = [
            name for name in preflight._CHECK_NAMES if name not in assigned
        ]
        self.assertEqual(
            missing, [],
            "the production preflight enforces these settings but "
            f".env.production.example never assigns them: {missing}",
        )

    def test_the_values_the_template_can_decide_are_already_correct(self):
        """Four of the rules need no operator input, so the template must not
        leave them to chance."""
        assigned = assignments(PRODUCTION_EXAMPLE)
        for name, expected in (
            ("ALLOW_OPEN_WRITES", "false"),
            ("OPENAPI_ENABLED", "false"),
            ("SERVER_RELOAD", "false"),
        ):
            with self.subTest(setting=name):
                self.assertEqual(assigned.get(name, "").lower(), expected)
        cap = assigned.get("LLM_DAILY_COST_CAP_USD", "0")
        self.assertGreater(
            float(cap), 0.0,
            "the template ships the cost cap that production requires",
        )

    def test_the_operator_supplied_secrets_are_present_but_empty(self):
        """`API_WRITE_KEY=` and `CORS_ALLOWED_ORIGINS=` are deliberately blank --
        the template cannot know them. Present-and-blank is what makes the
        preflight failure self-explanatory; absent would make it a surprise."""
        assigned = assignments(PRODUCTION_EXAMPLE)
        for name in ("API_WRITE_KEY", "CORS_ALLOWED_ORIGINS"):
            with self.subTest(setting=name):
                self.assertIn(name, assigned)
                self.assertEqual(assigned[name], "")

    def test_the_template_documents_the_backup_encryption_requirement(self):
        """`BACKUP_ENCRYPTION_KEY` was named in no overlay at all, while compose
        turned the scheduled backup on."""
        body = PRODUCTION_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("BACKUP_ENCRYPTION_KEY", body)
        self.assertIn("BACKUP_SCHEDULE_ENABLED", body)


class ComposeDeclaresProductionTests(unittest.TestCase):
    """The compose deployment is a production deployment; it has to say so.

    Before this it left `PMRF_ENV` unset, which meant `development`: no preflight,
    and the plaintext-backup refusal never fired -- in the one deployment that
    enables the scheduled backup.
    """

    def test_compose_declares_the_production_environment(self):
        assigned = assignments(COMPOSE)
        self.assertEqual(assigned.get("PMRF_ENV"), "production")

    def test_compose_declares_that_it_carries_no_overlay_file(self):
        """Without this the container cannot boot at all: `.dockerignore` keeps
        `.env.production` out of the image, so the loader would refuse."""
        assigned = assignments(COMPOSE)
        self.assertEqual(
            assigned.get("PMRF_ENV_FILE_REQUIRED", "").lower(), "false"
        )

    def test_compose_does_not_schedule_a_backup_it_cannot_encrypt(self):
        """`BACKUP_SCHEDULE_ENABLED=true` with no key named is what produced a
        daily plaintext archive of every state store."""
        body = COMPOSE.read_text(encoding="utf-8")
        assigned = assignments(COMPOSE)
        if assigned.get("BACKUP_SCHEDULE_ENABLED", "").lower() == "true":
            self.assertIn(
                "BACKUP_ENCRYPTION_KEY", body,
                "compose enables the scheduled backup but never names the "
                "encryption key production requires",
            )

    def test_compose_satisfies_the_preflight_rules_it_controls(self):
        """The two the image can decide for itself. The secrets come from
        `env_file`, so compose cannot assert those -- the preflight does, at boot.
        """
        assigned = assignments(COMPOSE)
        self.assertEqual(assigned.get("OPENAPI_ENABLED", "").lower(), "false")
        self.assertNotEqual(assigned.get("ALLOW_OPEN_WRITES", "").lower(), "true")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
