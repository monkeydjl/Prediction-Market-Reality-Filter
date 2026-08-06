"""Tests for the TypeScript type generation script.

Verifies:
- generate_types.py main() produces generated-types.ts with all 14 allowlist
  root models.
- --check mode exits 0 when generated-types.ts is up to date.
- --check mode exits 1 when generated-types.ts is stale.
- Output file contains a header comment.
- Output is deterministic (running twice produces identical files).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_OUTPUT_PATH = _REPO_ROOT / "frontend" / "src" / "lib" / "generated-types.ts"


def _json2ts_available() -> bool:
    """Whether the json2ts CLI the script shells out to is installed.

    The generator needs ``json2ts`` from ``frontend/node_modules``, so these
    tests only run where the frontend deps are installed. The CI backend job
    installs Python only (Node lives in the frontend and type-sync jobs), and
    without this guard every case here fails on a RuntimeError that says
    nothing about the code under test. The dedicated ``type-sync-check`` job
    keeps the generator itself covered in CI.
    """
    from scripts.generate_types import _find_json2ts_cmd

    try:
        _find_json2ts_cmd()
    except RuntimeError:
        return False
    return True


@unittest.skipUnless(_json2ts_available(), "json2ts CLI not installed (frontend deps missing)")
class TestGenerateTypesScript(unittest.TestCase):
    """Tests run the actual generate_types.py script via subprocess."""

    def _run_script(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "scripts.generate_types", *args]
        return subprocess.run(
            cmd,
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_generates_file_with_all_allowlist_models(self):
        """Default mode writes generated-types.ts containing all 14 root models."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        self.assertTrue(_OUTPUT_PATH.exists(), "generated-types.ts not created")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        # All 14 allowlist model names should appear as exported interfaces
        for model_name in [
            "EventRecord",
            "EventStoreEntry",
            "EventListResponse",
            "EventMoversResponse",
            "EventHistoryResponse",
            "DecisionTimelineResponse",
            "AutoResolveResponse",
            "PendingLinksResponse",
            "RecentPredictionsResponse",
            "OpenDecisionsResponse",
            "FreshEdgesResponse",
            "SimilarEventsResponse",
            "EventAnalysisRequest",
            "EventDiscoveryResponse",
        ]:
            self.assertIn(model_name, content, f"{model_name} missing from generated types")

    def test_output_contains_header_comment(self):
        """Generated file should have a 'do not edit' header."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertIn("generated", content.lower())
        self.assertIn("do not edit", content.lower())

    def test_check_mode_exit_0_when_up_to_date(self):
        """--check exits 0 when generated-types.ts matches fresh generation."""
        # First ensure file is up to date
        self._run_script()
        # Then check
        result = self._run_script("--check")
        self.assertEqual(result.returncode, 0, f"--check failed on up-to-date file: {result.stderr}")

    def test_check_mode_exit_1_when_stale(self):
        """--check exits 1 when generated-types.ts differs from fresh generation."""
        # Ensure file is up to date first
        self._run_script()
        original = _OUTPUT_PATH.read_text(encoding="utf-8")
        # Tamper: append a fake interface
        _OUTPUT_PATH.write_text(original + "\nexport interface FakeTampered { x: number; }\n", encoding="utf-8")
        try:
            result = self._run_script("--check")
            self.assertEqual(result.returncode, 1, f"--check should exit 1 on stale file, got {result.returncode}")
        finally:
            # Restore
            self._run_script()

    def test_output_is_deterministic(self):
        """Running generate twice produces identical output."""
        self._run_script()
        first = _OUTPUT_PATH.read_text(encoding="utf-8")
        self._run_script()
        second = _OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertEqual(first, second, "Generated output is not deterministic")

    def test_nested_models_included(self):
        """Nested models (DecisionQuality, LLMTelemetry, etc.) appear in output."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        for nested_model in ["DecisionQuality", "MarketQuality", "SourceReliability", "LLMTelemetry", "CrossValidation"]:
            self.assertIn(nested_model, content, f"Nested model {nested_model} missing")


if __name__ == "__main__":
    unittest.main()
