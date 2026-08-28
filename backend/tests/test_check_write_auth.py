"""`scripts/check_write_auth.py` is what the launcher's exit-code branches read.

`start.bat` decides between "carry on", "explain and abort", and "note it and
carry on" purely from this script's exit code, so the codes are the contract —
not the text. They are asserted here by running the script, because the launcher
runs it the same way and a code that only holds when called in-process would not
be the thing the launcher sees.

The MISSING case is the one that mattered: `start.bat` only ever grepped `.env`
for a placeholder OPENAI_API_KEY, and a `.env` with no `API_WRITE_KEY` line at
all passes that grep — the placeholder is absent precisely because the line is.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

_AUTHORISED = 0
_FAIL_CLOSED = 1
_UNREADABLE = 2


def _run(**env_overrides: str) -> subprocess.CompletedProcess:
    """Run the script with write-auth settings pinned.

    Every relevant name is set explicitly, empty included: `_load_env_files`
    calls `load_dotenv()` without `override`, so the environment beats the
    developer's untracked `backend/.env`.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_DIR), env.get("PYTHONPATH", "")])
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    env["API_WRITE_KEY"] = ""
    env["ALLOW_OPEN_WRITES"] = "false"
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "scripts.check_write_auth"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


class ExitCodeTests(unittest.TestCase):
    def test_a_write_key_is_authorised(self):
        result = _run(API_WRITE_KEY="test-only-not-a-real-key")
        self.assertEqual(result.returncode, _AUTHORISED, result.stdout + result.stderr)
        self.assertIn("KEY", result.stdout)

    def test_open_writes_are_authorised(self):
        result = _run(ALLOW_OPEN_WRITES="true")
        self.assertEqual(result.returncode, _AUTHORISED, result.stdout + result.stderr)
        self.assertIn("OPEN", result.stdout)
        self.assertIn("PUBLIC", result.stdout)

    def test_neither_setting_is_fail_closed(self):
        result = _run()
        self.assertEqual(result.returncode, _FAIL_CLOSED, result.stdout + result.stderr)
        self.assertIn("MISSING", result.stdout)
        # Both remedies named, so the operator is not left guessing.
        self.assertIn("API_WRITE_KEY", result.stdout)
        self.assertIn("ALLOW_OPEN_WRITES", result.stdout)

    def test_the_three_states_do_not_share_an_exit_code(self):
        """Guard the contract: identical codes would collapse the launcher's branches."""
        codes = {
            "key": _run(API_WRITE_KEY="test-only-not-a-real-key").returncode,
            "open": _run(ALLOW_OPEN_WRITES="true").returncode,
            "missing": _run().returncode,
        }
        self.assertEqual(codes["key"], codes["open"], codes)
        self.assertNotEqual(codes["missing"], codes["key"], codes)

    def test_the_key_is_never_printed(self):
        secret = "sentinel-check-key-4a91cd"
        result = _run(API_WRITE_KEY=secret)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_an_unimportable_settings_module_reports_unreadable(self):
        """Missing dependencies must not read as "misconfigured".

        The launcher treats 1 as fatal and 2 as advisory. If an ImportError
        surfaced as 1, a fresh clone whose dependencies are not installed yet
        would be told to edit `.env`, which would not fix anything.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Shadow a dependency of app.core.config so the import fails.
            Path(tmp, "dotenv.py").write_text(
                'raise ImportError("simulated missing dependency")\n', encoding="utf-8"
            )
            env = {"PYTHONPATH": os.pathsep.join([tmp, str(BACKEND_DIR)])}
            base = os.environ.copy()
            base.update(
                {
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                    "API_WRITE_KEY": "",
                    "ALLOW_OPEN_WRITES": "false",
                    **env,
                }
            )
            result = subprocess.run(
                [sys.executable, "-m", "scripts.check_write_auth"],
                cwd=BACKEND_DIR,
                env=base,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        self.assertEqual(result.returncode, _UNREADABLE, result.stdout + result.stderr)
        self.assertIn("UNKNOWN", result.stdout)


class LauncherWiringTests(unittest.TestCase):
    """The exit codes are only useful if `start.bat` actually branches on them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.launcher = (BACKEND_DIR.parent / "start.bat").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_the_launcher_invokes_the_script(self):
        self.assertIn("scripts.check_write_auth", self.launcher)

    def test_the_launcher_branches_on_both_nonzero_codes(self):
        self.assertIn('"!WRITE_AUTH!"=="1"', self.launcher)
        self.assertIn('"!WRITE_AUTH!"=="2"', self.launcher)

    def test_no_launcher_path_runs_a_bare_python(self):
        """The launcher must use the venv interpreter it resolved, not PATH.

        `python run.py` on a machine whose PATH python is a newer release runs
        the app on an interpreter the project has never been tested on.
        """
        for name in ("start.bat", "backend/start.bat"):
            text = (BACKEND_DIR.parent / name).read_text(encoding="utf-8", errors="replace")
            offenders = [
                line.strip()
                for line in text.splitlines()
                if line.strip().startswith(("python ", "python.exe "))
                or " python run.py" in line
                or line.strip() == "python run.py"
            ]
            with self.subTest(launcher=name):
                self.assertEqual(offenders, [], f"{name} still calls PATH python directly")


if __name__ == "__main__":
    unittest.main()
