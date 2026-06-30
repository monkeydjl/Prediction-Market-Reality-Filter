"""Tests for PMRF_ENV-driven multi-environment config loading."""
import os
import tempfile
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
