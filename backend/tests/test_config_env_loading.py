"""Tests for PMRF_ENV-driven multi-environment config loading."""
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# conftest.py patches dotenv.load_dotenv to a no-op before app modules are
# imported.  Tests that exercise real dotenv behavior must opt-in by patching
# the real function back into app.core.config's namespace.  We import from
# dotenv.main (the definition site) which is unaffected by the package-level
# patch in conftest.
from dotenv.main import load_dotenv as _real_load_dotenv


class TestEnvLoading(unittest.TestCase):
    def test_default_env_is_development(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PMRF_ENV", None)
            # Re-import to capture default. We test the helper function
            # rather than re-importing the module (which has side effects).
            from app.core.config import _resolve_env_file
            env_file = _resolve_env_file()
            self.assertEqual(env_file, ".env")

    def test_load_env_files_staging_overrides_base(self):
        """Staging .env file overrides base .env values via _load_env_files."""
        from app.core.config import _load_env_files
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, ".env"), "w") as f:
                f.write("OPENAI_MODEL=base-model\n")
            with open(os.path.join(tmp, ".env.staging"), "w") as f:
                f.write("OPENAI_MODEL=staging-model\n")
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                # patch.dict restores os.environ at exit (hermetic).
                with patch.dict(os.environ, {"PMRF_ENV": "staging"}, clear=False):
                    # Opt-in: restore real load_dotenv into config's namespace
                    # so _load_env_files() actually reads the temp .env files.
                    with patch("app.core.config.load_dotenv", _real_load_dotenv):
                        _load_env_files()
                        # Staging file must override base.
                        self.assertEqual(os.environ.get("OPENAI_MODEL"), "staging-model")
            finally:
                os.chdir(old_cwd)

    def test_resolve_env_file_returns_environment_specific(self):
        from app.core.config import _resolve_env_file
        with patch.dict(os.environ, {"PMRF_ENV": "production"}, clear=False):
            self.assertEqual(_resolve_env_file(), ".env.production")
        with patch.dict(os.environ, {"PMRF_ENV": "staging"}, clear=False):
            self.assertEqual(_resolve_env_file(), ".env.staging")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PMRF_ENV", None)
            self.assertEqual(_resolve_env_file(), ".env")


class TestOverlayFailsLoud(unittest.TestCase):
    """A named overlay that is not on disk must refuse to start.

    ``load_dotenv`` returns ``False`` for a missing file and logs nothing, so
    ``PMRF_ENV=production`` with no ``.env.production`` booted the process on
    every development default. The overlay only overrides the keys it names, so
    that is not "most of production" -- it is `CORS_ALLOWED_ORIGINS`,
    `SERVER_RELOAD`, `LLM_DAILY_COST_CAP_USD` and every feature flag at their
    development values, with nothing in the log to say so.
    """

    def _run_in(self, tmp: str, files: dict[str, str], env: dict[str, str]) -> dict[str, str]:
        """Run ``_load_env_files`` with ``tmp`` as cwd; return the resulting env.

        The snapshot is taken *inside* ``patch.dict``, which restores
        ``os.environ`` on exit -- reading it afterwards sees the restored copy.

        Only the overlay is read out of ``tmp``: the base ``load_dotenv()`` call
        takes no path, so ``find_dotenv`` walks up from ``app/core/config.py``
        and finds the repository's own ``backend/.env`` regardless of cwd. That
        asymmetry is why these tests assert on the overlay's value rather than on
        a base value written here.
        """
        from app.core.config import _load_env_files

        for name, body in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            with patch.dict(os.environ, env, clear=False):
                if "PMRF_ENV" not in env:
                    os.environ.pop("PMRF_ENV", None)
                with patch("app.core.config.load_dotenv", _real_load_dotenv):
                    _load_env_files()
                    return dict(os.environ)
        finally:
            os.chdir(old_cwd)

    def test_a_missing_production_overlay_refuses_to_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._run_in(
                    tmp,
                    {".env": "OPENAI_MODEL=dev-model\n"},
                    {"PMRF_ENV": "production"},
                )

    def test_a_missing_staging_overlay_refuses_to_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._run_in(
                    tmp,
                    {".env": "OPENAI_MODEL=dev-model\n"},
                    {"PMRF_ENV": "staging"},
                )

    def test_the_refusal_names_the_file_without_exposing_absolute_paths(self):
        """The missing filename is actionable; host paths are not safe output."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as caught:
                self._run_in(
                    tmp,
                    {".env": "OPENAI_MODEL=dev-model\n"},
                    {"PMRF_ENV": "production"},
                )
        message = str(caught.exception)
        self.assertIn(".env.production", message)
        self.assertNotIn(tmp, message)
        self.assertNotIn(os.path.realpath(tmp), message)

    def test_application_entrypoint_does_not_print_configuration_exception_text(self):
        from app import entrypoint

        sensitive = (
            "SELECT private_value FROM secret_table at "
            "D:/private/runtime/config.env Authorization=Bearer fake-api-key"
        )
        stderr = tempfile.SpooledTemporaryFile(mode="w+")
        self.addCleanup(stderr.close)
        real_import = entrypoint.importlib.import_module

        def importing(name: str):
            if name == "app.core.config":
                raise RuntimeError(sensitive)
            return real_import(name)

        with patch.object(entrypoint.importlib, "import_module", side_effect=importing), \
                patch.object(entrypoint.sys, "stderr", stderr):
            with self.assertRaises(SystemExit) as caught:
                entrypoint.load_application()

        stderr.seek(0)
        output = stderr.read()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Configuration refused startup", output)
        self.assertIn("RuntimeError", output)
        for fragment in (
            "SELECT private_value",
            "D:/private/runtime/config.env",
            "fake-api-key",
            "Traceback",
        ):
            self.assertNotIn(fragment, output)

    def _assert_import_refusal_is_safe(self, script_name: str) -> None:
        sensitive = (
            "SELECT private_value FROM secret_table at "
            "D:/private/runtime/config.env Authorization=Bearer fake-api-key"
        )
        backend = Path(__file__).resolve().parent.parent
        stderr = tempfile.SpooledTemporaryFile(mode="w+")
        self.addCleanup(stderr.close)
        real_import = __import__

        def importing(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "app.core.config":
                raise RuntimeError(sensitive)
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=importing), \
                patch.object(sys, "stderr", stderr):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(
                    str(backend / "scripts" / script_name),
                    run_name=f"import_refusal_{script_name}",
                )

        stderr.seek(0)
        output = stderr.read()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Configuration refused startup", output)
        self.assertIn("RuntimeError", output)
        for fragment in (
            "SELECT private_value",
            "D:/private/runtime/config.env",
            "fake-api-key",
            "Traceback",
        ):
            self.assertNotIn(fragment, output)

    def test_scheduler_import_refusal_does_not_print_exception_text(self):
        self._assert_import_refusal_is_safe("run_scheduler.py")

    def test_backup_import_refusal_does_not_print_exception_text(self):
        self._assert_import_refusal_is_safe("backup_stores.py")

    def test_production_entrypoints_refuse_without_exposing_source_paths(self):
        from pathlib import Path

        backend = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["PMRF_ENV"] = "production"
        env["PMRF_ENV_FILE_REQUIRED"] = "true"
        env.pop("PYTHONPATH", None)
        entrypoints = (
            [sys.executable, "-m", "uvicorn", "app.entrypoint:load_application", "--factory"],
            [sys.executable, "scripts/run_scheduler.py"],
            [sys.executable, "scripts/backup_stores.py"],
        )
        for command in entrypoints:
            with self.subTest(entrypoint=command[-1]):
                result = subprocess.run(
                    command,
                    cwd=backend,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Configuration refused startup", output)
                self.assertIn("RuntimeError", output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn(str(backend), output)
                self.assertNotIn(str(backend.resolve()), output)

    def test_an_empty_overlay_file_is_not_a_missing_one(self):
        """Present and empty is a deliberate state, not a misconfiguration.

        ``load_dotenv`` reports ``False`` for both, so the check has to ask the
        filesystem whether the file exists rather than whether it set anything.
        Both halves run against the *same* directory, so the only difference
        between the refusal and the acceptance is whether the file is there. The
        empty file is then compared against one that assigns a marker: an empty
        overlay has to be *read and override nothing*, which is not the same
        property as "did not raise".
        """
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._run_in(
                    tmp,
                    {".env": "OPENAI_MODEL=dev-model\n"},
                    {"PMRF_ENV": "production"},
                )
            empty = self._run_in(
                tmp,
                {".env": "OPENAI_MODEL=dev-model\n", ".env.production": "\n# nothing\n"},
                {"PMRF_ENV": "production"},
            )
            assigning = self._run_in(
                tmp,
                {".env.production": "PMRF_OVERLAY_MARKER=set\n"},
                {"PMRF_ENV": "production"},
            )
        self.assertIsNone(empty.get("PMRF_OVERLAY_MARKER"))
        self.assertEqual(assigning.get("PMRF_OVERLAY_MARKER"), "set")

    def test_development_does_not_need_an_overlay_file(self):
        """No overlay is named, so none is required -- and none is guessed.

        ``development`` maps to the base ``.env`` itself, not to a
        ``.env.development`` beside the other two overlays. A file with that name
        is never read, which is worth pinning: an operator who infers the third
        name from ``.env.staging`` and ``.env.production`` would otherwise get a
        file that is silently ignored.
        """
        with tempfile.TemporaryDirectory() as tmp:
            env = self._run_in(
                tmp,
                {
                    ".env": "OPENAI_MODEL=dev-model\n",
                    ".env.development": "OPENAI_MODEL=guessed-model\n",
                },
                {"PMRF_ENV": "development"},
            )
        self.assertNotEqual(
            env.get("OPENAI_MODEL"),
            "guessed-model",
            ".env.development was read; development resolves to the base .env, so "
            "a file by that name has no reader and must not acquire one silently",
        )

    def test_a_present_production_overlay_still_overrides_the_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._run_in(
                tmp,
                {
                    ".env": "OPENAI_MODEL=dev-model\n",
                    ".env.production": "OPENAI_MODEL=prod-model\n",
                },
                {"PMRF_ENV": "production"},
            )
        self.assertEqual(env.get("OPENAI_MODEL"), "prod-model")

    def test_pmrf_env_written_inside_the_overlay_cannot_select_it(self):
        """What the templates used to promise, measured.

        ``PMRF_ENV`` is read after the base ``.env`` and before any overlay, so
        the ``PMRF_ENV=production`` line *inside* ``.env.production`` is read
        only once that file has already been chosen. An operator who followed
        the template's "copy to .env.production and set PMRF_ENV=production to
        activate" got development -- silently then, and still silently now,
        because nothing names an overlay so nothing is missing. The templates now
        say to set it in the base ``.env`` or the process environment; this is the
        measurement behind that wording.
        """
        with tempfile.TemporaryDirectory() as tmp:
            env = self._run_in(
                tmp,
                {
                    ".env": "OPENAI_MODEL=dev-model\n",
                    ".env.production": "PMRF_ENV=production\nOPENAI_MODEL=prod-model\n",
                },
                {},
            )
        self.assertNotEqual(
            env.get("OPENAI_MODEL"),
            "prod-model",
            "the overlay was applied, so PMRF_ENV inside it selected it",
        )
        self.assertEqual(env.get("PMRF_ENV"), None)

    def test_an_unrecognized_pmrf_env_refuses_instead_of_meaning_development(self):
        """``prod`` is not ``production``, and it used to mean development.

        The old resolver tested two exact strings and returned ``.env`` for
        anything else, so a typo in the one variable that selects the production
        overlay resolved to the development file -- the same silent outcome as
        the missing-overlay case, reached from the other direction.
        """
        from app.core.config import _resolve_env_file

        for value in ("prod", "prd", "live", "PRD", "dev"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"PMRF_ENV": value}, clear=False):
                    with self.assertRaises(RuntimeError):
                        _resolve_env_file()

    def test_case_and_surrounding_space_are_still_forgiven(self):
        """The old resolver lowercased and stripped; refusing must not undo that."""
        from app.core.config import _resolve_env_file

        for value in ("PRODUCTION", " production ", "Staging"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"PMRF_ENV": value}, clear=False):
                    self.assertEqual(
                        _resolve_env_file(),
                        f".env.{value.strip().lower()}",
                    )

    def test_the_three_recognized_names_still_resolve(self):
        from app.core.config import ENV_OVERLAYS, _resolve_env_file

        for value, expected in ENV_OVERLAYS.items():
            with self.subTest(value=value):
                with patch.dict(os.environ, {"PMRF_ENV": value}, clear=False):
                    self.assertEqual(_resolve_env_file(), expected)

    def test_an_empty_pmrf_env_means_unset_not_invalid(self):
        """A template that ships ``PMRF_ENV=`` must not be a boot failure."""
        from app.core.config import _resolve_env_file

        for value in ("", "   "):
            with self.subTest(value=repr(value)):
                with patch.dict(os.environ, {"PMRF_ENV": value}, clear=False):
                    self.assertEqual(_resolve_env_file(), ".env")


if __name__ == "__main__":
    unittest.main()
