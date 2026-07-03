"""CLI tests for model_eval_lab."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from scripts.model_eval_lab import main


def _record(event_id="evt-001", model="gpt-4o-mini", **overrides):
    rec = {
        "event_id": event_id,
        "source": {"type": "prediction_market"},
        "llm_telemetry": {
            "model": model,
            "analysis_quality": "llm",
            "degraded_mode": False,
            "estimated_token_cost": 0.02,
        },
        "actionable_recommendation": {"direction": "YES", "edge": 12.0},
        "outcome": {"status": "resolved", "actual_outcome": 100.0},
        "calibration": {"brier_score": 0.16, "estimated_probability": 72.0},
        "source_reliability": {"overall_score": 0.65},
        "guardrail_fired": [],
    }
    rec.update(overrides)
    return rec


def _entry(event_id="evt-001", **overrides):
    return {"event_id": event_id, "record": _record(event_id, **overrides)}


def _extract_items(*entries):
    """Extract model metrics from entry dicts for mocking _collect_entries."""
    from app.services.model_eval_lab_service import extract_model_metrics
    return [extract_model_metrics(e["record"]) for e in entries]


class TestCliExitCodes(unittest.TestCase):
    def test_no_args_returns_0(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main([])
        self.assertEqual(rc, 0)

    def test_sample_negative_returns_2(self):
        rc = main(["--sample", "-1"])
        self.assertEqual(rc, 2)

    def test_min_samples_negative_returns_2(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main(["--min-samples", "-1"])
        self.assertEqual(rc, 2)

    def test_event_ids_empty_after_parse_returns_2(self):
        rc = main(["--event-ids", ","])
        self.assertEqual(rc, 2)

    def test_sample_zero_legal_empty_report(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main(["--sample", "0"])
        self.assertEqual(rc, 0)


class TestCliOutput(unittest.TestCase):
    def test_json_mode_outputs_pure_json(self):
        items = _extract_items(_entry())
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = main(["--json"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        # Must be pure JSON, no [INFO] prefix
        self.assertFalse(out.startswith("[INFO]"))
        data = json.loads(out)
        self.assertIn("overview", data)
        self.assertIn("by_model", data)

    def test_text_mode_has_overview_header(self):
        items = _extract_items(_entry())
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("== Overview", out)
        self.assertIn("[INFO]", out)

    def test_ascii_only_no_emoji_or_box_chars(self):
        items = _extract_items(_entry())
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main([])
        out = buf.getvalue()
        # No box drawing chars or emoji
        for bad in ("─", "═", "│", "📊", "⚠️"):
            self.assertNotIn(bad, out, f"found forbidden char {bad!r}")

    def test_insufficient_flag_in_output(self):
        items = _extract_items(_entry(model="rare"))
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main(["--min-samples", "5"])
        out = buf.getvalue()
        self.assertIn("[INSUFFICIENT]", out)

    def test_cost_na_when_no_cost_data(self):
        items = _extract_items(_entry(llm_telemetry={
            "model": "x", "analysis_quality": "llm",
            "degraded_mode": False, "estimated_token_cost": None,
        }))
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main(["--json"])
        out = buf.getvalue()
        data = json.loads(out)
        self.assertIsNone(data["overview"]["cost_avg"])


class TestCliCollectEntries(unittest.TestCase):
    def test_event_ids_filter_first_then_sample(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [
            _entry("a"), _entry("b"), _entry("c"),
        ]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=1, event_ids=["a", "b"])
        # Filtered to a+b, then sampled 1
        self.assertEqual(len(items), 1)
        self.assertIn(items[0]["event_id"], {"a", "b"})

    def test_event_ids_none_returns_all(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [_entry("a"), _entry("b")]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=None, event_ids=None)
        self.assertEqual(len(items), 2)

    def test_report_errors_for_non_dict_record(self):
        from scripts.model_eval_lab import _collect_entries
        # An entry whose record is not a dict
        entries = [{"event_id": "bad", "record": "not a dict"}]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=None, event_ids=None)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("not a dict", errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
