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
                                                   r["actionable_recommendation"]["direction"]}):
            matrix, counted = _compute_direction_matrix(records,
                                                ReplayConfig.preset_all_off(),
                                                ReplayConfig.preset_all_on())
        self.assertEqual(matrix["YES"]["YES"], 1)
        self.assertEqual(matrix["NO"]["NO"], 1)
        self.assertEqual(matrix["YES"]["WAIT"], 0)
        self.assertEqual(matrix["YES"]["NO"], 0)
        self.assertEqual(counted, 2)

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
            matrix, counted = _compute_direction_matrix(
                records,
                ReplayConfig.preset_all_off(),
                ReplayConfig.preset_all_on(),
            )
        self.assertEqual(matrix["YES"]["WAIT"], 1)
        self.assertEqual(matrix["YES"]["YES"], 0)
        self.assertEqual(counted, 1)

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
                                                   r["actionable_recommendation"]["direction"]}):
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
                                                   r["actionable_recommendation"]["direction"]}):
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

    def test_effective_direction_falls_back_to_recommendation(self):
        """Regression test: when all_off strips final_displayed_direction,
        _effective_direction must fall back to actionable_recommendation.direction
        (not default to WAIT). Without this chain, a YES record under all_off
        would be counted as WAIT, misreporting YES->YES as WAIT->YES and
        YES->WAIT as WAIT->WAIT.

        This test does NOT mock replay_record — it exercises the real
        replay path with all_off (which strips overlays but preserves
        actionable_recommendation).
        """
        from app.replay.config import ReplayConfig
        from analyze_feature_flag_impact import _effective_direction
        from app.replay.runner import replay_record

        record = _record("e-fallback", "YES")
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        # all_off strips final_displayed_direction
        self.assertNotIn("final_displayed_direction", replayed)
        # Fallback chain must recover YES from actionable_recommendation
        self.assertEqual(_effective_direction(replayed), "YES")

    def test_effective_direction_excludes_probability_direction(self):
        """probability.direction holds rising/falling/stable (see
        scoring_service.probability_direction), NOT a decision direction.
        _effective_direction must NOT use it as a fallback. Records with
        no actionable_recommendation and no final_displayed_direction
        must return None so they are excluded from the matrix."""
        from analyze_feature_flag_impact import _effective_direction

        record = {
            "event_id": "e-no-rec",
            "probability": {"baseline": 50.0, "estimated": 55.0,
                            "change": 5.0, "direction": "rising"},
            # no actionable_recommendation, no final_displayed_direction
        }
        self.assertIsNone(_effective_direction(record))

    def test_direction_matrix_uses_recommendation_fallback(self):
        """End-to-end regression: all_off vs all_off on a YES record must
        report YES->YES (no change), not WAIT->WAIT. Uses real replay_record
        (no mock) to verify the fallback chain works through the matrix."""
        from app.replay.config import ReplayConfig

        records = [_record("e-yes", "YES"), _record("e-no", "NO")]
        matrix, counted = _compute_direction_matrix(
            records,
            ReplayConfig.preset_all_off(),
            ReplayConfig.preset_all_off(),
        )
        # Both configs are all_off → no change → diagonal should be 1 each
        self.assertEqual(matrix["YES"]["YES"], 1)
        self.assertEqual(matrix["NO"]["NO"], 1)
        # No spurious WAIT entries
        self.assertEqual(matrix["WAIT"]["YES"], 0)
        self.assertEqual(matrix["YES"]["WAIT"], 0)
        self.assertEqual(counted, 2)

    def test_direction_matrix_excludes_records_without_direction(self):
        """Records lacking actionable_recommendation.direction must be
        excluded from the matrix AND from the counted total, so the
        change-rate denominator stays correct."""
        from app.replay.config import ReplayConfig

        records = [
            _record("e-yes", "YES"),
            {"event_id": "e-no-rec", "probability": {"direction": "rising"}},  # no actionable_rec
        ]
        matrix, counted = _compute_direction_matrix(
            records,
            ReplayConfig.preset_all_off(),
            ReplayConfig.preset_all_off(),
        )
        # Only the YES record is counted
        self.assertEqual(counted, 1)
        self.assertEqual(matrix["YES"]["YES"], 1)


if __name__ == "__main__":
    unittest.main()
