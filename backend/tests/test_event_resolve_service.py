"""Tests for event_resolve_service.

Covers resolve_with_calibration (the shared manual/auto resolve path) and
auto_resolve_events (the Polymarket-match workflow). Network is mocked:
fetch_resolved_markets is patched, so no real Polymarket call is made.

The _make_record fixture is imported from tests.test_event_store to keep one
canonical record builder.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.memory import event_store as store
from app.services import event_audit_service as audit
from app.services import event_resolve_service as ers
from app.services import polymarket_history_service as phs
from app.services import manifold_event_source as mfs
from app.services import kalshi_event_source as kes

# Reuse the canonical record builder.
from tests.test_event_store import _make_record


class ResolveWithCalibrationTests(unittest.TestCase):
    """resolve_with_calibration is the shared path for manual + auto resolve."""

    def test_attaches_outcome_and_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path):
                store.save_event(_make_record("evtR", estimated=70.0, value_score=30))
                # Record a probability trajectory: latest estimate 80%.
                audit.record_event(_make_record("evtR", estimated=80.0))
                updated = asyncio.run(ers.resolve_with_calibration(
                    event_id="evtR", actual_outcome=100.0, source="manual",
                ))
                after = store.get_event("evtR")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["record"]["outcome"]["actual_outcome"], 100.0)
        self.assertEqual(updated["record"]["outcome"]["source"], "manual")
        # Brier((80-100)/100)^2 = 0.04 -> EXCELLENT
        self.assertAlmostEqual(updated["record"]["calibration"]["brier_score"], 0.04)
        self.assertEqual(updated["record"]["calibration"]["grade"], "EXCELLENT")
        self.assertEqual(after["record"]["outcome"]["status"], "resolved")

    def test_unknown_event_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=store_path):
                result = asyncio.run(ers.resolve_with_calibration(
                    event_id="missing", actual_outcome=0.0,
                ))
        self.assertIsNone(result)

    def test_auto_source_is_propagated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path):
                store.save_event(_make_record("evtAuto", value_score=30))
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtAuto", actual_outcome=0.0,
                    source="auto_polymarket", notes="matched: some market",
                ))
                after = store.get_event("evtAuto")
        self.assertEqual(after["record"]["outcome"]["source"], "auto_polymarket")
        self.assertIn("matched", after["record"]["outcome"]["notes"])


class AutoResolveEventsTests(unittest.TestCase):
    """auto_resolve_events: fetch resolved markets, match, resolve each."""

    def setUp(self):
        # Multi-source auto-resolve also pulls Manifold + Kalshi; default both to
        # empty so these Polymarket-focused tests stay network-free. Individual
        # tests re-patch as needed.
        for module in (mfs, kes):
            patcher = patch.object(
                module, "fetch_resolved_markets", new=AsyncMock(return_value=[])
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_resolved_markets_returns_no_data(self):
        with patch.object(phs, "fetch_resolved_markets",
                          new=AsyncMock(return_value=[])):
            result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
        self.assertEqual(result["status"], "no_resolved_markets")
        self.assertEqual(result["resolved_count"], 0)

    def test_matches_and_resolves_unresolved_event(self):
        resolved_market = {
            "question": "Will Bitcoin reach $100,000 by end of 2026?",
            "actual_outcome": 100.0,
        }
        # The stored event uses a near-identical title so the match is exact.
        record = _make_record("evtMatch", value_score=30)
        record["event_title"] = "Will Bitcoin reach $100,000 by end of 2026?"
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtMatch")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["matches"][0]["actual_outcome"], 100.0)
        self.assertEqual(after["record"]["outcome"]["source"], "auto_market")
        self.assertIsNotNone(after["record"]["calibration"])

    def test_skips_already_resolved_events(self):
        resolved_market = {
            "question": "Already resolved question",
            "actual_outcome": 0.0,
        }
        record = _make_record("evtDone", value_score=30)
        record["event_title"] = "Already resolved question"
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                store.save_event(record)
                # Pre-resolve it.
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtDone", actual_outcome=100.0, source="manual",
                ))
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
        # The already-resolved event is not matched again.
        self.assertEqual(result["resolved_count"], 0)

    def test_fetch_failure_degrades_gracefully(self):
        # A failing source is isolated; with the other two empty (setUp), the
        # merged pool is empty and auto-resolve reports no resolved markets
        # instead of crashing.
        with patch.object(phs, "fetch_resolved_markets",
                          new=AsyncMock(side_effect=RuntimeError("network down"))):
            result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
        self.assertEqual(result["status"], "no_resolved_markets")
        self.assertEqual(result["resolved_count"], 0)

    def test_scans_beyond_top_200_low_value_event_is_resolved(self):
        """Regression for Bug 1: auto-resolve must scan EVERY stored event, not
        just list_events' top-200 by value_score. A matching event with the
        lowest value_score must still be resolved when >200 events exist."""
        resolved_market = {
            "question": "Will the Fed raise rates in June?",
            "actual_outcome": 100.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                # Seed 201 unrelated high-value events, then the target with the
                # lowest value_score. list_events(limit=200) would drop it.
                for i in range(201):
                    rec = _make_record(f"filler{i}", value_score=50)
                    rec["event_title"] = f"Unrelated filler event number {i}"
                    store.save_event(rec)
                target = _make_record("target", value_score=10)
                target["event_title"] = "Will the Fed raise rates in June?"
                store.save_event(target)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("target")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolved_count"], 1)
        self.assertIsNotNone(after["record"]["outcome"])

    def test_skips_event_with_empty_title_without_summary_fallback(self):
        """Regression for #7: an event with a blank title must be skipped, not
        matched against its event_summary narrative (which would produce garbage
        fuzzy matches)."""
        resolved_market = {
            "question": "Something completely unrelated",
            "actual_outcome": 100.0,
        }
        record = _make_record("evtBlank", value_score=30)
        record["event_title"] = ""  # blank title
        # event_summary is non-empty narrative; the old code would match on it.
        record["event_summary"] = "A narrative about Fed rates and inflation."
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtBlank")
        self.assertEqual(result["resolved_count"], 0)
        self.assertIsNone(after["record"].get("outcome"))

    def test_merges_sources_and_reports_by_source(self):
        """Resolved markets merge across platforms; a failing source is isolated;
        an event is resolved by whichever source carries its market."""
        record = _make_record("evtMulti", value_score=30)
        record["event_title"] = "Will it rain in Seattle tomorrow?"
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[
                                     {"question": "unrelated poly", "actual_outcome": 100.0}])), \
                    patch.object(mfs, "fetch_resolved_markets",
                                 new=AsyncMock(side_effect=RuntimeError("down"))), \
                    patch.object(kes, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[
                                     {"question": "Will it rain in Seattle tomorrow?",
                                      "actual_outcome": 0.0}])):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtMulti")
        self.assertEqual(result["status"], "ok")
        # Manifold raised -> excluded from by_source; the other two are counted.
        self.assertEqual(result["by_source"], {"Polymarket": 1, "Kalshi": 1})
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(after["record"]["outcome"]["actual_outcome"], 0.0)
        self.assertEqual(after["record"]["outcome"]["source"], "auto_market")


if __name__ == "__main__":
    unittest.main()
