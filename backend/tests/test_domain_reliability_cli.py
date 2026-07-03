"""Tests for domain_reliability CLI (LATER #2)."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestDomainReliabilityCli(unittest.TestCase):
    def _run_main(self, argv):
        import domain_reliability_cli as drc
        orig_stdout, orig_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            rc = drc.main(argv)
            return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = orig_stdout, orig_stderr

    def test_cli_list_empty_exit_0(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            rc, stdout, _ = self._run_main(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("0 domains", stdout)

    def test_cli_list_json_shape(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            rc, stdout, _ = self._run_main(["list", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("domains", data)
        self.assertIn("total_domains", data)
        self.assertIn("total_rows", data)

    def test_cli_rebuild_dry_run(self):
        with patch("app.memory.domain_reliability_store.rebuild_from_records") as mock_rb, \
             patch("app.memory.event_store.list_resolved_events", return_value=[]):
            rc, stdout, _ = self._run_main(["rebuild", "--dry-run"])
        self.assertEqual(rc, 0)
        mock_rb.assert_not_called()

    def test_cli_rebuild_limit_preview(self):
        with patch("app.memory.domain_reliability_store.rebuild_from_records") as mock_rb, \
             patch("app.memory.event_store.list_resolved_events", return_value=[]):
            rc, stdout, _ = self._run_main(["rebuild", "--limit", "5"])
        self.assertEqual(rc, 0)
        mock_rb.assert_not_called()

    def test_cli_rebuild_full(self):
        with patch("app.memory.event_store.list_resolved_events", return_value=[]), \
             patch("app.services.domain_reliability_service.attribute_evidence", return_value=[]):
            rc, stdout, _ = self._run_main(["rebuild"])
        self.assertEqual(rc, 0)

    def test_cli_rebuild_limit_does_not_write(self):
        with patch("app.memory.domain_reliability_store.rebuild_from_records") as mock_rb, \
             patch("app.memory.event_store.list_resolved_events", return_value=[]):
            self._run_main(["rebuild", "--limit", "5"])
        mock_rb.assert_not_called()

    def test_cli_no_emoji(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            rc, stdout, _ = self._run_main(["list"])
        for ch in stdout:
            cp = ord(ch)
            self.assertFalse(
                0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF,
                f"Output contains emoji-like char U+{cp:04X}",
            )


if __name__ == "__main__":
    unittest.main()
