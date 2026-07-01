"""Unit tests for analyze_feature_flag_impact CLI (Plan 5 §1.5)."""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "scripts"))

from analyze_feature_flag_impact import (
    _compute_direction_matrix,
    _format_matrix,
    main,
)


def _record(event_id="evt-001", direction="YES"):
    return {
        "event_id": event_id,
        "event_title": "Test",
        "source": {"type": "prediction_market"},
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": direction},
        "actionable_recommendation": {"direction": direction, "signal": "act",
                                       "ai_probability": 0.65},
        "evidence_breakdown": [],
        "evidence_items": [],
        "legacy_analysis": {},
        "market_quote": {"volume": 1000.0, "liquidity": 500.0,
                         "bid_ask": {"bid": 0.55, "ask": 0.56, "spread": 0.01},
                         "last_updated": "2026-07-01T00:00:00Z"},
    }


class TestAnalyzeFeatureFlagImpact(unittest.TestCase):
    def test_compute_direction_matrix_no_changes(self):
        records = [_record("e1", "YES"), _record("e2", "NO")]
        from app.replay.config import ReplayConfig
        with patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            matrix = _compute_direction_matrix(records,
                                                ReplayConfig.preset_all_off(),
                                                ReplayConfig.preset_all_on())
        self.assertEqual(matrix["YES"]["YES"], 1)
        self.assertEqual(matrix["NO"]["NO"], 1)
        self.assertEqual(matrix["YES"]["WAIT"], 0)
        self.assertEqual(matrix["YES"]["NO"], 0)

    def test_compute_direction_matrix_records_yes_to_wait(self):
        records = [_record("e1", "YES")]
        def fake_replay(record, cfg):
            # cfg "off" → keep YES; cfg "on" → flip to WAIT.
            from app.replay.config import ReplayConfig
            if cfg == ReplayConfig.preset_all_on():
                return {**record, "final_displayed_direction": "WAIT"}
            return {**record, "final_displayed_direction": "YES"}
        with patch("analyze_feature_flag_impact.replay_record",
                   side_effect=fake_replay):
            from app.replay.config import ReplayConfig
            matrix = _compute_direction_matrix(
                records,
                ReplayConfig.preset_all_off(),
                ReplayConfig.preset_all_on(),
            )
        self.assertEqual(matrix["YES"]["WAIT"], 1)
        self.assertEqual(matrix["YES"]["YES"], 0)

    def test_format_matrix_renders_ascii_table(self):
        matrix = {"YES": {"YES": 5, "WAIT": 2, "NO": 0, "AVOID": 0},
                  "NO": {"YES": 0, "WAIT": 0, "NO": 3, "AVOID": 1},
                  "WAIT": {"YES": 0, "WAIT": 4, "NO": 0, "AVOID": 0},
                  "AVOID": {"YES": 0, "WAIT": 0, "NO": 0, "AVOID": 1}}
        out = _format_matrix(matrix, total=16)
        self.assertIn("[INFO]", out)
        self.assertIn("YES", out)
        self.assertIn("WAIT", out)
        # Should report change rate.
        self.assertIn("change rate", out.lower())

    def test_format_matrix_excludes_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        matrix = {"YES": {"YES": 1, "WAIT": 0, "NO": 0, "AVOID": 0},
                  "NO": {"YES": 0, "WAIT": 0, "NO": 1, "AVOID": 0},
                  "WAIT": {"YES": 0, "WAIT": 0, "NO": 0, "AVOID": 0},
                  "AVOID": {"YES": 0, "WAIT": 0, "NO": 0, "AVOID": 0}}
        out = _format_matrix(matrix, total=2).lower()
        for term in banned:
            self.assertNotIn(term, out)

    def test_main_runs_and_prints_report(self):
        records = [_record("e1", "YES")]
        with patch("analyze_feature_flag_impact._load_records",
                   return_value=records), \
             patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--sample-size", "10"])
            self.assertEqual(rc, 0)
            self.assertIn("[OK]", buf.getvalue())

    def test_main_writes_json_when_output_specified(self):
        import tempfile
        records = [_record("e1", "YES")]
        with patch("analyze_feature_flag_impact._load_records",
                   return_value=records), \
             patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            with tempfile.TemporaryDirectory() as tmp:
                out_path = Path(tmp) / "report.json"
                rc = main(["--sample-size", "10", "--json", str(out_path)])
                self.assertEqual(rc, 0)
                self.assertTrue(out_path.exists())
                data = json.loads(out_path.read_text(encoding="utf-8"))
                self.assertIn("matrix", data)
                self.assertIn("total", data)

    def test_main_handles_empty_records(self):
        with patch("analyze_feature_flag_impact._load_records",
                   return_value=[]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--sample-size", "10"])
            self.assertEqual(rc, 0)
            self.assertIn("[WARN]", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
