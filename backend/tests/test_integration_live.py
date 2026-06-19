#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Live integration tests for Event Intelligence Platform.

These tests make real API calls (LLM, news sources, Polymarket) and should be
run manually before deployment, not in CI. They cover both the core read paths
(event analysis / discovery / news collection) and the post-handoff write/
report paths (manual resolve + calibration, auto-resolve, calibration report,
semantics, cross-validation, open-web extraction).

Run with: set RUN_LIVE_TESTS=1 && python -m unittest tests.test_integration_live
Or directly: python tests/test_integration_live.py
"""
import asyncio
import tempfile
import unittest
import sys
import os
from pathlib import Path
from unittest.mock import patch

def _force_utf8_output(stream):
    """Best-effort UTF-8 output without assuming unittest streams expose buffer."""
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="strict")
        return stream
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return stream
    import codecs
    return codecs.getwriter("utf-8")(buffer, "strict")


# Force UTF-8 output on Windows.
if sys.platform == "win32":
    sys.stdout = _force_utf8_output(sys.stdout)
    sys.stderr = _force_utf8_output(sys.stderr)

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.event_intelligence_service import (
    analyze_event_question,
    discover_events,
)
from app.services.event_collection_service import (
    collect_shared_articles,
    collect_articles,
)
from app.services.event_resolve_service import (
    auto_resolve_events,
    resolve_with_calibration,
)
from app.services.calibration_service_event import summarize
from app.services.event_extraction_service import extract_candidate_events
from app.memory import event_store, event_cache
from app.services import event_audit_service
from app.utils import sqlite_db
from app.services.rss_service import fetch_news
from app.services.gnews_service import fetch_google_news


class LiveIntegrationTests(unittest.TestCase):
    """Live integration tests - require real API access."""

    @classmethod
    def setUpClass(cls):
        """Live tests are opt-in.

        They make real, paid API calls and hit the network, so they are skipped
        by default - including under ``unittest discover`` / CI. Run them on
        demand with ``RUN_LIVE_TESTS=1`` set, or by invoking this file directly
        (``python tests/test_integration_live.py``), which sets the flag.
        """
        if not os.getenv("RUN_LIVE_TESTS"):
            raise unittest.SkipTest(
                "live integration tests are opt-in - set RUN_LIVE_TESTS=1 to run"
            )
        from app.core.config import settings
        if not settings.OPENAI_API_KEY:
            raise unittest.SkipTest("OPENAI_API_KEY not set - skipping live tests")

    def test_event_analysis_with_llm(self):
        """Test event analysis with real LLM call."""
        print("\n[TEST] Event analysis with LLM...")

        async def run():
            result = await analyze_event_question(
                event_question="Will it rain tomorrow?",
                baseline_probability=50.0,
                news_context="Weather forecast shows cloudy skies."
            )
            return result

        result = asyncio.run(run())

        # Verify structure
        self.assertIn('event_id', result)
        self.assertIn('event_title', result)
        self.assertIn('probability', result)
        self.assertIn('credibility', result)
        self.assertIn('impact', result)
        self.assertIn('intelligence_report', result)

        # Verify probability fields
        prob = result['probability']
        self.assertIn('baseline', prob)
        self.assertIn('estimated', prob)
        self.assertIn('change', prob)
        self.assertIn('direction', prob)

        # Verify credibility fields
        cred = result['credibility']
        self.assertIn('score', cred)
        self.assertIn('level', cred)
        self.assertIsInstance(cred['score'], int)
        self.assertIn(cred['level'], ['LOW', 'MEDIUM', 'HIGH'])

        # Verify intelligence report
        report = result['intelligence_report']
        self.assertIn('headline', report)
        self.assertIn('why_it_matters', report)
        self.assertIn('probability_assessment', report)
        self.assertIn('recommended_action', report)

        print(f"  Event ID: {result['event_id']}")
        print(f"  Probability: {prob['baseline']}% -> {prob['estimated']}%")
        print(f"  Credibility: {cred['score']}/100 ({cred['level']})")
        print("  ✅ PASS")

    def test_rss_news_collection(self):
        """Test RSS news collection from real sources."""
        print("\n[TEST] RSS news collection...")

        async def run():
            articles = await fetch_news(limit=5)
            return articles

        articles = asyncio.run(run())

        self.assertIsInstance(articles, list)
        if articles:  # May be empty if sources are down
            article = articles[0]
            self.assertTrue(hasattr(article, 'title'))
            self.assertTrue(hasattr(article, 'source'))
            print(f"  Collected {len(articles)} articles")
            print(f"  Sample: {articles[0].title[:60]}...")
        else:
            print("  ⚠️ No articles collected (sources may be down)")

        print("  ✅ PASS")

    def test_google_news_collection(self):
        """Test Google News collection."""
        print("\n[TEST] Google News collection...")

        async def run():
            articles = await fetch_google_news("technology")
            return articles

        articles = asyncio.run(run())

        self.assertIsInstance(articles, list)
        if articles:
            article = articles[0]
            self.assertIn('title', article)
            self.assertIn('source', article)
            print(f"  Collected {len(articles)} articles")
            print(f"  Sample: {articles[0]['title'][:60]}...")
        else:
            print("  ⚠️ No articles collected")

        print("  ✅ PASS")

    def test_shared_articles_collection(self):
        """Test concurrent shared article collection."""
        print("\n[TEST] Shared articles collection...")

        async def run():
            articles = await collect_shared_articles()
            return articles

        articles = asyncio.run(run())

        self.assertIsInstance(articles, list)
        print(f"  Collected {len(articles)} shared articles")
        if articles:
            print(f"  Sample sources: {set(a.get('source', 'unknown') for a in articles[:5])}")

        print("  ✅ PASS")

    def test_event_discovery_flow(self):
        """Test full event discovery flow (takes longer - reduced limit)."""
        print("\n[TEST] Event discovery flow (this may take 30-60 seconds)...")

        async def run():
            result = await discover_events(limit=2, use_cache=False)
            return result

        result = asyncio.run(run())

        self.assertIsInstance(result, dict)
        self.assertIn('events', result)
        self.assertIn('count', result)
        self.assertIn('platform', result)

        events = result['events']
        print(f"  Discovered {len(events)} events")

        if events:
            event = events[0]
            self.assertIn('event_id', event)
            self.assertIn('event_title', event)
            self.assertIn('probability', event)
            self.assertIn('value_score', event)
            print(f"  Top event: {event['event_title'][:60]}...")
            print(f"  Value score: {event['value_score']}/100")
        else:
            print("  ⚠️ No events discovered")

        print("  ✅ PASS")

    # --- Live coverage for the post-handoff features (resolve / calibration /
    # auto-resolve / semantics / cross-validation / open-web extraction). ---
    # These exercise the new write/report paths that the original live tests
    # (analyze / discover / news) never touched. Each isolates the event store,
    # audit log, and compute cache to a temp dir so real data files are not
    # polluted.

    @staticmethod
    def _isolated_storage():
        """Patch event store / audit / cache to temp files for one test.

        Usage: ``with self._isolated_storage() as iso: ...``. Returns a small
        object whose .store / .audit / .cache are the temp paths. Cleans up on
        exit.
        """

        class _Isolated:
            def __init__(self):
                self._tmp = tempfile.TemporaryDirectory()
                tmpdir = self._tmp.name
                self.store = str(Path(tmpdir) / "event_store.json")
                self.audit = str(Path(tmpdir) / "event_audit.jsonl")
                self.cache = str(Path(tmpdir) / "event_cache.json")
                self.loop_db = str(Path(tmpdir) / "v2_loop.db")
                self._patches = []

            def __enter__(self):
                self._patches = [
                    patch.object(event_store, "_store_path", return_value=self.store),
                    patch.object(event_audit_service, "_audit_path", return_value=self.audit),
                    patch.object(event_cache, "_cache_file", return_value=self.cache),
                    patch.object(sqlite_db, "loop_db_path", return_value=self.loop_db),
                ]
                for p in self._patches:
                    p.start()
                return self

            def __exit__(self, *exc):
                for p in self._patches:
                    p.stop()
                self._tmp.cleanup()

        return _Isolated()

    def test_resolve_with_calibration_live(self):
        """Manual resolve end-to-end: LLM analyze -> resolve -> calibration."""
        print("\n[TEST] Resolve with calibration (live LLM)...")

        async def run():
            with self._isolated_storage() as iso:
                record = await analyze_event_question(
                    event_question="Will Bitcoin reach $100,000 by end of 2026?",
                    baseline_probability=50.0,
                    news_context="Institutional adoption is rising.",
                )
                event_id = record["event_id"]
                updated = await resolve_with_calibration(
                    event_id=event_id, actual_outcome=100.0, source="manual",
                )
                return updated

        updated = asyncio.run(run())
        self.assertIsNotNone(updated)
        outcome = updated["record"]["outcome"]
        cal = updated["record"]["calibration"]
        self.assertEqual(outcome["actual_outcome"], 100.0)
        self.assertEqual(outcome["source"], "manual")
        self.assertIn(cal["grade"], ["EXCELLENT", "GOOD", "ACCEPTABLE", "POOR", "RANDOM_LEVEL"])
        self.assertTrue(0.0 <= cal["brier_score"] <= 1.0)
        print(f"  Brier {cal['brier_score']} ({cal['grade']}) on estimate {cal['estimated_probability']}%")
        print("  ✅ PASS")

    def test_calibration_report_live(self):
        """Cross-event calibration report shape (overall + by_source + by_category)."""
        print("\n[TEST] Calibration report shape (live)...")

        async def run():
            with self._isolated_storage() as iso:
                # Resolve one event so there is a calibration to aggregate.
                record = await analyze_event_question(
                    event_question="Will the Fed raise rates in June?",
                    baseline_probability=50.0,
                    news_context="Inflation data is mixed.",
                )
                await resolve_with_calibration(
                    event_id=record["event_id"], actual_outcome=0.0,
                )
                from app.memory.event_store import list_resolved_events
                resolved = list_resolved_events()
                events = []
                for entry in resolved:
                    rec = entry.get("record") or {}
                    legacy = rec.get("legacy_analysis") or {}
                    events.append({
                        "calibration": rec.get("calibration"),
                        "source": rec.get("source") or {},
                        "base_rate_category": legacy.get("base_rate_category", "unknown"),
                    })
                return summarize(events)

        report = asyncio.run(run())
        self.assertIn("overall", report)
        self.assertIn("by_source", report)
        self.assertIn("by_base_rate_category", report)
        self.assertGreaterEqual(report["overall"]["n"], 1)
        print(f"  Overall brier {report['overall']['brier_score']} ({report['overall']['grade']}), n={report['overall']['n']}")
        print("  ✅ PASS")

    def test_auto_resolve_events_live(self):
        """Auto-resolve workflow: Polymarket fetch -> match -> resolve. May match 0."""
        print("\n[TEST] Auto-resolve events (live, may take 20-40s)...")

        async def run():
            with self._isolated_storage() as iso:
                # Seed one event so the store is non-empty.
                record = await analyze_event_question(
                    event_question="Will Bitcoin reach $100,000 by end of 2026?",
                    baseline_probability=50.0,
                    news_context="Institutional adoption is rising.",
                )
                result = await auto_resolve_events(resolved_limit=50)
                return result

        result = asyncio.run(run())
        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(result["resolved_count"], int)
        self.assertGreaterEqual(result["resolved_count"], 0)
        print(f"  Resolved {result['resolved_count']} of {result['checked_count']} checked Polymarket markets")
        print("  ✅ PASS")

    def test_discover_carries_semantics_live(self):
        """Discover with live LLM populates record.semantics (entities)."""
        print("\n[TEST] Discover carries semantics (live, may take 30-60s)...")

        async def run():
            with self._isolated_storage() as iso:
                result = await discover_events(limit=2, use_cache=False)
                return result

        result = asyncio.run(run())
        events = result.get("events", [])
        if not events:
            self.skipTest("no events discovered - cannot assert semantics")
        with_semantics = [
            e for e in events
            if e.get("semantics") and (e["semantics"].get("entities") or e["semantics"].get("resolution_criteria"))
        ]
        self.assertTrue(
            with_semantics,
            "expected at least one discovered event to carry semantics (entities/criteria)",
        )
        sample = with_semantics[0]["semantics"]
        print(f"  Sample semantics: entities={sample.get('entities', [])[:3]}, horizon='{sample.get('time_horizon', '')}'")
        print("  ✅ PASS")

    def test_cross_validation_live(self):
        """Cross-validation with a real second model (opt-in)."""
        from app.core.config import settings
        if not settings.CROSS_VALIDATION_MODEL:
            self.skipTest("CROSS_VALIDATION_MODEL not set - cross-validation disabled")
        print(f"\n[TEST] Cross-validation with {settings.CROSS_VALIDATION_MODEL} (live)...")

        async def run():
            with self._isolated_storage() as iso:
                record = await analyze_event_question(
                    event_question="Will the ECB cut rates this year?",
                    baseline_probability=50.0,
                    news_context="Growth is slowing.",
                )
                return record

        record = asyncio.run(run())
        self.assertIsNotNone(record.get("cross_validation"), "cross_validation field should be populated when enabled")
        xv = record["cross_validation"]
        self.assertIn(xv["agreement"], ["high", "medium", "low"])
        print(f"  Primary {xv['primary_probability']}% vs second {xv['probability']}% ({xv['agreement']})")
        print("  ✅ PASS")

    def test_open_web_extraction_live(self):
        """Open-web event extraction with a real LLM (opt-in)."""
        from app.core.config import settings
        if not settings.OPEN_WEB_EXTRACTION_MODEL:
            self.skipTest("OPEN_WEB_EXTRACTION_MODEL not set - extraction disabled")
        print(f"\n[TEST] Open-web extraction with {settings.OPEN_WEB_EXTRACTION_MODEL} (live)...")

        async def run():
            articles = await collect_shared_articles()
            if not articles:
                self.skipTest("no shared articles collected - cannot test extraction")
            candidates = await extract_candidate_events(articles[:10], limit=5)
            return candidates

        candidates = asyncio.run(run())
        self.assertIsInstance(candidates, list)
        if candidates:
            sample = candidates[0]
            self.assertIn("question", sample)
            self.assertEqual(sample["source"]["type"], "open_web")
            print(f"  Extracted {len(candidates)} candidate(s); sample: {sample['question'][:60]}")
        else:
            print("  ⚠️ Extraction returned no candidates (LLM may have found none resolvable)")
        print("  ✅ PASS")


def run_live_tests():
    """Run live integration tests with colored output."""
    print("=" * 70)
    print("EVENT INTELLIGENCE PLATFORM - LIVE INTEGRATION TESTS")
    print("=" * 70)
    print("\n⚠️  WARNING: These tests make real API calls:")
    print("  - LLM API (costs money)")
    print("  - News sources (network access)")
    print("  - Event discovery (Polymarket API)")
    print("\nEstimated runtime: 1-2 minutes")
    print("=" * 70)

    # Run tests
    suite = unittest.TestLoader().loadTestsFromTestCase(LiveIntegrationTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print("✅ ALL LIVE INTEGRATION TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 70)

    return result.wasSuccessful()


if __name__ == "__main__":
    # Direct invocation is an explicit request to run the live tests.
    os.environ.setdefault("RUN_LIVE_TESTS", "1")
    success = run_live_tests()
    sys.exit(0 if success else 1)
