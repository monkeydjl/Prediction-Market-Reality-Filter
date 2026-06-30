"""Tests for the historical overlay backfill script.

Mirrors the TestF4MigrationScript mocking pattern: patches the script
module's IO helpers (read_json_strict / locked_file / write_json_atomic /
Path) so tests run hermetically without touching real disk state.
"""
import unittest
from unittest.mock import MagicMock, patch


def _synthetic_record(event_id: str, missing_overlays: bool = True) -> dict:
    """A minimal record that replay_record can process."""
    rec = {
        "event_id": event_id,
        "title": "Will X happen?",
        "source": {"type": "open_web", "url": "https://example.com/article"},
        "market": {"yes_price": 0.65, "no_price": 0.35},
        "actionable_recommendation": {
            "direction": "YES",
            "signal": "WATCHLIST",
            "ai_probability": 0.62,
            "edge": 0.0,
            "confidence": "medium",
        },
        "evidence_breakdown": [
            {
                "source": "https://example.com/article",
                "direction": "support",
                "strength": 0.7,
                "summary": "supports YES",
            }
        ],
    }
    if missing_overlays:
        # Pre-Phase records have no overlay fields.
        for key in (
            "decision_quality",
            "market_quality",
            "source_reliability",
            "llm_telemetry",
            "final_displayed_direction",
            "final_downgrade_reason",
        ):
            rec.pop(key, None)
    return rec


def _store_data_for(record: dict) -> dict:
    """Wrap a record in the on-disk entry shape keyed by event_id."""
    eid = record["event_id"]
    return {
        eid: {
            "event_id": eid,
            "first_seen": "2026-01-01",
            "last_updated": "2026-01-01",
            "record": record,
        }
    }


class TestBackfillQualityOverlays(unittest.TestCase):
    """Follows the TestF4MigrationScript pattern: mock read_json_strict /
    locked_file / write_json_atomic / Path in the script module."""

    def test_dry_run_does_not_write(self):
        from scripts.backfill_quality_overlays import backfill_quality_overlays

        store_data = _store_data_for(_synthetic_record("e1"))
        with patch("scripts.backfill_quality_overlays.read_json_strict", return_value=store_data), \
             patch("scripts.backfill_quality_overlays.Path") as mock_path_cls, \
             patch("scripts.backfill_quality_overlays.locked_file"), \
             patch("scripts.backfill_quality_overlays.write_json_atomic") as mock_write:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path_cls.return_value.resolve.return_value = mock_path

            result = backfill_quality_overlays(dry_run=True)

        # Dry run reports what would change but does not persist.
        self.assertGreaterEqual(result["would_backfill"], 1)
        mock_write.assert_not_called()

    def test_apply_writes_overlay_fields(self):
        from scripts.backfill_quality_overlays import backfill_quality_overlays

        store_data = _store_data_for(_synthetic_record("e1"))
        with patch("scripts.backfill_quality_overlays.read_json_strict", return_value=store_data), \
             patch("scripts.backfill_quality_overlays.Path") as mock_path_cls, \
             patch("scripts.backfill_quality_overlays.locked_file"), \
             patch("scripts.backfill_quality_overlays.write_json_atomic") as mock_write:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path_cls.return_value.resolve.return_value = mock_path

            result = backfill_quality_overlays(dry_run=False)

        self.assertGreaterEqual(result["backfilled"], 1)
        # Apply mode must persist via write_json_atomic.
        mock_write.assert_called_once()
        # The store_data passed to write_json_atomic should now have
        # decision_quality populated on the record.
        args, kwargs = mock_write.call_args
        written_store = args[1] if len(args) > 1 else kwargs.get("obj")
        written_record = written_store["e1"]["record"]
        self.assertIn("decision_quality", written_record)

    def test_skips_records_already_having_overlays(self):
        from scripts.backfill_quality_overlays import backfill_quality_overlays

        rec_with_overlay = _synthetic_record("e1", missing_overlays=False)
        rec_with_overlay["decision_quality"] = {"score": 0.5}
        store_data = _store_data_for(rec_with_overlay)
        with patch("scripts.backfill_quality_overlays.read_json_strict", return_value=store_data), \
             patch("scripts.backfill_quality_overlays.Path") as mock_path_cls, \
             patch("scripts.backfill_quality_overlays.locked_file"), \
             patch("scripts.backfill_quality_overlays.write_json_atomic"):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path_cls.return_value.resolve.return_value = mock_path

            result = backfill_quality_overlays(dry_run=True)

        self.assertEqual(result["would_backfill"], 0)
        self.assertEqual(result["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
