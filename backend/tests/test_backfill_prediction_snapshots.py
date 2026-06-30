"""Tests for the Phase 3 snapshot backfill script.

Verifies that pre-Phase-3 frozen predictions (rows with empty snapshot columns)
get their snapshots populated from the stored event record, and that the
--dry-run mode previews without writing.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make scripts/ importable.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.utils import sqlite_db  # noqa: E402
from app.memory import event_store as store  # noqa: E402
from app.memory import prediction_store as preds  # noqa: E402

# Import the script as a module (it's in scripts/, not app/).
_SCRIPTS = _BACKEND / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import backfill_prediction_snapshots as backfill  # noqa: E402


def _record_with_snapshot(event_id="evtBackfill", *, with_snapshot=False):
    """An event record that, when passed to build_prediction_snapshot,
    produces a non-empty snapshot."""
    rec = {
        "event_id": event_id,
        "event_title": "Will BTC hit $100k?",
        "event_summary": "summary",
        "probability": {
            "baseline": 50.0, "estimated": 65.0, "change": 15.0, "direction": "rising",
        },
        "credibility": {
            "score": 60, "level": "MEDIUM", "confidence": 0.6,
            "news_quality": 0.5, "evidence_strength": 0.7, "source_count": 3,
        },
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["momentum"]},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {
            "direction": "supports", "strength": 0.7, "conflict": 0.2,
            "freshness": 0.8, "resolution_relevance": 0.6,
        },
        "source": {
            "type": "prediction_market", "platform": "Polymarket",
            "source_id": "poly-xyz", "liquidity": 100.0, "volume": 200.0,
        },
        "value_score": 50,
        "tracking": {"status": "watching", "priority": "medium"},
        "intelligence_report": {
            "headline": "h", "why_it_matters": "w",
            "probability_assessment": "p", "recommended_action": "r",
        },
        "legacy_analysis": {"base_rate_category": "crypto", "signal": "ACT",
                            "signal_direction": "LONG", "signal_strength": "HIGH",
                            "market_probability": 50, "ai_probability": 65,
                            "expected_edge": 0.15, "position_size": 0.05,
                            "risk_level": "low", "evidence_strength": 0.7,
                            "confidence_score": 0.6, "news_quality_score": 0.5,
                            "source_count": 3, "risk_flags": []},
        "semantics": {"resolution_criteria": "", "time_horizon": "", "entities": []},
        "actionable_recommendation": {
            "direction": "YES", "confidence": "high",
            "suggested_allocation_pct": 5.0, "edge": 15.0,
            "risk_level": "low", "rationale": "test",
            "calibration_status": "uncalibrated_provisional",
        },
        "evidence_breakdown": [],
    }
    if with_snapshot:
        # Add market_quality so snapshot_market_quality_score is non-null.
        rec["market_quality"] = {"score": 0.85}
    return rec


class BackfillPredictionSnapshotsTests(unittest.TestCase):
    """Tests for the backfill script."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "v2_loop.db")
        self.store_path = str(Path(self.tmp.name) / "event_store.json")
        self._patches = [
            patch.object(sqlite_db, "loop_db_path", return_value=self.db_path),
            patch.object(store, "_store_path", return_value=self.store_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _freeze_pre_phase3_prediction(self, record):
        """Freeze a prediction WITHOUT a snapshot (simulates pre-Phase-3).

        We disable PREDICTION_CALIBRATION_ENABLED during freeze so the
        snapshot columns remain empty (byte-identical to pre-Phase-3)."""
        with patch.object(preds.settings, "PREDICTION_CALIBRATION_ENABLED", False):
            preds.freeze_prediction(record)

    # --- find_rows_needing_backfill ---

    def test_find_rows_finds_empty_snapshot_rows(self):
        rec = _record_with_snapshot()
        self._freeze_pre_phase3_prediction(rec)
        event_ids = backfill.find_rows_needing_backfill(self.db_path)
        self.assertEqual(len(event_ids), 1)
        self.assertEqual(event_ids[0], "evtBackfill")

    def test_find_rows_skips_rows_with_snapshot(self):
        """When a row already has snapshot_question populated, it's not found."""
        rec = _record_with_snapshot()
        # Freeze WITH calibration enabled (writes the snapshot).
        with patch.object(preds.settings, "PREDICTION_CALIBRATION_ENABLED", True):
            preds.freeze_prediction(rec)
        event_ids = backfill.find_rows_needing_backfill(self.db_path)
        self.assertEqual(len(event_ids), 0)

    def test_find_rows_empty_when_no_predictions(self):
        event_ids = backfill.find_rows_needing_backfill(self.db_path)
        self.assertEqual(event_ids, [])

    # --- backfill_row (dry-run) ---

    def test_dry_run_does_not_write(self):
        rec = _record_with_snapshot()
        self._freeze_pre_phase3_prediction(rec)
        store.save_events([rec])

        result = backfill.backfill_row("evtBackfill", dry_run=True, path=self.db_path)
        self.assertEqual(result["status"], "would_backfill")

        # Verify the row was NOT updated.
        row = preds.get_prediction("evtBackfill")
        self.assertEqual(row["snapshot_question"], "")

    # --- backfill_row (apply) ---

    def test_backfill_populates_snapshot_columns(self):
        rec = _record_with_snapshot(with_snapshot=True)
        self._freeze_pre_phase3_prediction(rec)
        store.save_events([rec])

        result = backfill.backfill_row("evtBackfill", dry_run=False, path=self.db_path)
        self.assertEqual(result["status"], "backfilled")

        # Verify the row now has snapshot columns populated.
        row = preds.get_prediction("evtBackfill")
        self.assertEqual(row["snapshot_question"], "Will BTC hit $100k?")
        self.assertEqual(row["snapshot_recommendation"], "YES")
        self.assertEqual(row["snapshot_confidence"], "high")
        self.assertAlmostEqual(row["snapshot_evidence_strength"], 0.7)
        self.assertAlmostEqual(row["snapshot_conflict_score"], 0.2)
        self.assertAlmostEqual(row["snapshot_market_quality_score"], 0.85)
        self.assertEqual(row["snapshot_source_platform"], "Polymarket")

    def test_backfill_skips_when_event_not_in_store(self):
        """When the event record was deleted from event_store, the prediction
        row cannot be backfilled."""
        rec = _record_with_snapshot()
        self._freeze_pre_phase3_prediction(rec)
        # Do NOT save the event record to event_store.

        result = backfill.backfill_row("evtBackfill", dry_run=False, path=self.db_path)
        self.assertEqual(result["status"], "skipped_no_event")

        # Verify the row was NOT updated.
        row = preds.get_prediction("evtBackfill")
        self.assertEqual(row["snapshot_question"], "")

    def test_backfill_skips_when_record_lacks_title(self):
        """When the event record has no event_title, snapshot_question is empty
        and the row is skipped (no meaningful snapshot to write)."""
        rec = _record_with_snapshot()
        rec["event_title"] = ""
        self._freeze_pre_phase3_prediction(rec)
        store.save_events([rec])

        result = backfill.backfill_row("evtBackfill", dry_run=False, path=self.db_path)
        self.assertEqual(result["status"], "skipped_no_event")

    # --- run (integration) ---

    def test_run_backfills_all_eligible_rows(self):
        """End-to-end: freeze 2 pre-Phase-3 predictions, run the backfill,
        verify both rows are populated."""
        rec1 = _record_with_snapshot("evt1")
        rec2 = _record_with_snapshot("evt2")
        self._freeze_pre_phase3_prediction(rec1)
        self._freeze_pre_phase3_prediction(rec2)
        store.save_events([rec1, rec2])

        summary = backfill.run(dry_run=False)
        self.assertEqual(summary.get("backfilled"), 2)

        for eid in ("evt1", "evt2"):
            row = preds.get_prediction(eid)
            self.assertEqual(row["snapshot_question"], "Will BTC hit $100k?")
            self.assertEqual(row["snapshot_recommendation"], "YES")

    def test_run_dry_run_reports_would_backfill(self):
        rec1 = _record_with_snapshot("evtDry")
        self._freeze_pre_phase3_prediction(rec1)
        store.save_events([rec1])

        summary = backfill.run(dry_run=True)
        self.assertEqual(summary.get("would_backfill"), 1)
        self.assertEqual(summary.get("backfilled", 0), 0)

        # Verify no changes were written.
        row = preds.get_prediction("evtDry")
        self.assertEqual(row["snapshot_question"], "")

    def test_run_skips_rows_without_event_records(self):
        rec = _record_with_snapshot()
        self._freeze_pre_phase3_prediction(rec)
        # Do NOT save the event record.

        summary = backfill.run(dry_run=False)
        self.assertEqual(summary.get("skipped_no_event"), 1)
        self.assertEqual(summary.get("backfilled", 0), 0)

    def test_run_idempotent(self):
        """Running the backfill twice: second run finds 0 rows to backfill."""
        rec = _record_with_snapshot()
        self._freeze_pre_phase3_prediction(rec)
        store.save_events([rec])

        summary1 = backfill.run(dry_run=False)
        self.assertEqual(summary1.get("backfilled"), 1)

        summary2 = backfill.run(dry_run=False)
        self.assertEqual(len(summary2), 0)  # no rows found


if __name__ == "__main__":
    unittest.main()
