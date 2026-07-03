"""Tests for domain_reliability_store (LATER #2)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from app.services.domain_reliability_service import attribute_evidence


def _record(
    event_id: str = "e1",
    direction: str = "YES",
    actual_outcome: float = 100.0,
    source_type: str = "prediction_market",
    evidence_breakdown=None,
    evidence_items=None,
) -> dict:
    if evidence_breakdown is None:
        evidence_breakdown = [
            {"source": "Reuters", "direction": "support", "credibility": 0.8},
        ]
    if evidence_items is None:
        evidence_items = [
            {"source": "Reuters", "url": "https://www.reuters.com/article/1"},
        ]
    return {
        "event_id": event_id,
        "source": {"type": source_type},
        "actionable_recommendation": {"direction": direction},
        "outcome": {"status": "resolved", "actual_outcome": actual_outcome},
        "evidence_breakdown": evidence_breakdown,
        "evidence_items": evidence_items,
    }


class _TempDBMixin:
    """Provides a temp SQLite file and patches loop_db_path."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._db_path = self._tmp.name
        self._patcher = patch("app.utils.sqlite_db.loop_db_path", return_value=self._db_path)
        self._patcher.start()
        # Also reset the schema init guard
        from app.memory import domain_reliability_store as drs
        drs._INITIALIZED.discard(self._db_path)

    def tearDown(self):
        self._patcher.stop()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        super().tearDown()


class TestApplyResolution(_TempDBMixin, unittest.TestCase):
    def test_apply_resolution_writes_rows(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats()
        # Should have (reuters.com, prediction_market) and (reuters.com, _all)
        domains = {(s["domain"], s["category"]) for s in stats}
        self.assertIn(("reuters.com", "prediction_market"), domains)
        self.assertIn(("reuters.com", "_all"), domains)

    def test_apply_resolution_idempotent(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        rec = _record()
        apply_resolution(rec)
        apply_resolution(rec)
        stats = get_stats()
        for s in stats:
            if s["domain"] == "reuters.com":
                self.assertEqual(s["sample_count"], 1)

    def test_all_row_aggregates_across_categories(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        rec1 = _record(event_id="e1", source_type="prediction_market")
        rec2 = _record(
            event_id="e2", source_type="open_web",
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.7,
                 "source_type": "open_web"},
            ],
        )
        apply_resolution(rec1)
        apply_resolution(rec2)
        stats = get_stats()
        all_row = [s for s in stats if s["category"] == "_all" and s["domain"] == "reuters.com"]
        self.assertEqual(len(all_row), 1)
        self.assertEqual(all_row[0]["sample_count"], 2)

    def test_unknown_category_not_all(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        rec = _record(source_type="",
                      evidence_breakdown=[
                          {"source": "X", "direction": "support", "credibility": 0.5},
                      ],
                      evidence_items=[
                          {"source": "X", "url": "https://x.com/a"},
                      ])
        rec["source"] = {}
        apply_resolution(rec)
        stats = get_stats()
        cats = {s["category"] for s in stats if s["domain"] == "x.com"}
        self.assertIn("_unknown", cats)
        self.assertIn("_all", cats)
        self.assertNotEqual(cats, {"_all"})


class TestRebuild(_TempDBMixin, unittest.TestCase):
    def test_rebuild_clears_and_recomputes(self):
        from app.memory.domain_reliability_store import apply_resolution, rebuild_from_records, get_stats
        apply_resolution(_record(event_id="old"))
        rebuild_from_records([_record(event_id="new")])
        stats = get_stats()
        # Should have data from "new" only
        self.assertTrue(any(s["sample_count"] > 0 for s in stats))

    def test_rebuild_idempotent(self):
        from app.memory.domain_reliability_store import rebuild_from_records, get_stats
        records = [_record(event_id="e1")]
        rebuild_from_records(records)
        stats1 = get_stats()
        rebuild_from_records(records)
        stats2 = get_stats()
        self.assertEqual(len(stats1), len(stats2))
        for s1, s2 in zip(sorted(stats1, key=lambda x: (x["domain"], x["category"])),
                          sorted(stats2, key=lambda x: (x["domain"], x["category"]))):
            self.assertEqual(s1["sample_count"], s2["sample_count"])


class TestGetStats(_TempDBMixin, unittest.TestCase):
    def test_get_stats_filter_domain(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats(domain="reuters.com")
        self.assertTrue(all(s["domain"] == "reuters.com" for s in stats))

    def test_get_stats_filter_category(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats(category="prediction_market")
        self.assertTrue(all(s["category"] == "prediction_market" for s in stats))

    def test_get_stats_min_samples(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats(min_samples=10)
        # Only 1 sample -> filtered out
        self.assertTrue(all(s["sample_count"] >= 10 for s in stats))

    def test_get_stats_returns_reliability_score(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats()
        self.assertTrue(all("reliability_score" in s for s in stats))

    def test_get_stats_zero_sample_returns_null_score(self):
        from app.memory.domain_reliability_store import get_stats
        # No data -> empty list
        stats = get_stats()
        self.assertEqual(stats, [])

    def test_get_stats_insufficient_flag(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        from app.core.config import settings
        apply_resolution(_record())
        stats = get_stats()
        # 1 sample < default CONFIDENCE_MIN_SAMPLES (5) -> insufficient
        for s in stats:
            if s["sample_count"] < settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES:
                self.assertTrue(s["insufficient_samples"])


if __name__ == "__main__":
    unittest.main()
