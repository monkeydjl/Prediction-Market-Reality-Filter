"""Tests for event_resolve_service.

Covers resolve_with_calibration (the shared manual/auto resolve path) and
auto_resolve_events (the multi-source prediction-market match workflow). Network is mocked:
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
from app.memory import event_market_link_store as links
from app.memory import prediction_store as preds
from app.utils import sqlite_db
from app.services import event_audit_service as audit
from app.services import event_resolve_service as ers
from app.services import polymarket_history_service as phs
from app.services import manifold_event_source as mfs
from app.services import kalshi_event_source as kes

# Reuse the canonical record builder.
from tests.test_event_store import _make_record


def _seed_open_act(event_id, *, ai_probability=80.0, market_probability=50.0):
    """Insert an OPEN prediction row already marked decision='act', so a resolve
    can score it without bootstrapping the diagnose() qualification math."""
    path = sqlite_db.loop_db_path()
    preds._ensure_schema(path)
    with sqlite_db.writing(path) as conn:
        conn.execute(
            """
            INSERT INTO predictions (
                id, event_id, base_rate_category, ai_probability,
                market_probability, raw_edge, decision, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'act', 'open', ?)
            """,
            (event_id, event_id, "cpi", ai_probability, market_probability,
             ai_probability - market_probability, "t0"),
        )


class ResolveWithCalibrationTests(unittest.TestCase):
    """resolve_with_calibration is the shared path for manual + auto resolve."""

    def test_attaches_outcome_and_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtR", estimated=70.0, value_score=30))
                # Record a probability trajectory: latest estimate 80%.
                audit.record_event(_make_record("evtR", estimated=80.0))
                updated = ers.resolve_with_calibration(
                    event_id="evtR", actual_outcome=100.0, source="manual",
                )
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
                result = ers.resolve_with_calibration(
                    event_id="missing", actual_outcome=0.0,
                )
        self.assertIsNone(result)

    def test_auto_source_is_propagated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtAuto", value_score=30))
                ers.resolve_with_calibration(
                    event_id="evtAuto", actual_outcome=0.0,
                    source="auto_market", notes="matched: some market",
                )
                after = store.get_event("evtAuto")
        self.assertEqual(after["record"]["outcome"]["source"], "auto_market")
        self.assertIn("matched", after["record"]["outcome"]["notes"])

    def test_auto_resolve_low_confidence_hook_enqueues_review_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(ers.settings, "REVIEW_QUEUE_ENABLED", True), \
                    patch.object(ers.settings, "REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE", 0.95), \
                    patch("app.memory.review_queue_store.enqueue_item") as enqueue:
                store.save_event(_make_record("evtReview", value_score=30))
                updated = ers.resolve_with_calibration(
                    event_id="evtReview",
                    actual_outcome=100.0,
                    confidence=0.92,
                    source="auto_market",
                    notes="matched: fuzzy market",
                )

        self.assertIsNotNone(updated)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["event_id"], "evtReview")
        self.assertEqual(
            enqueue.call_args.kwargs["trigger"],
            "auto_resolve_low_confidence",
        )
        self.assertEqual(enqueue.call_args.kwargs["severity"], "WARN")
        self.assertEqual(
            enqueue.call_args.kwargs["context"]["outcome_confidence"],
            0.92,
        )

    def test_review_queue_hook_failure_does_not_block_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(ers.settings, "REVIEW_QUEUE_ENABLED", True), \
                    patch.object(ers.settings, "REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE", 0.95), \
                    patch(
                        "app.services.review_queue_detectors.detect_auto_resolve_low_confidence",
                        side_effect=RuntimeError("detector boom"),
                    ), \
                    self.assertLogs("app.services.event_resolve_service", level="WARNING") as logs:
                store.save_event(_make_record("evtReviewFail", value_score=30))
                updated = ers.resolve_with_calibration(
                    event_id="evtReviewFail",
                    actual_outcome=0.0,
                    confidence=0.92,
                    source="auto_market",
                )

        self.assertIsNotNone(updated)
        self.assertTrue(
            any("review_queue detector run failed" in msg for msg in logs.output)
        )


class MarketlessCommitmentTests(unittest.TestCase):
    """A market-less event is graded on the estimate it committed to.

    `freeze_prediction` is market-gated, so a `sports_event` never gets a frozen
    prediction row - nothing pins its verdict. Meanwhile `record_event` appends a
    fresh snapshot on every re-scan, so grading the *latest* estimate graded
    whichever number the model produced most recently, and a re-scan late in a
    tournament runs after the outcome has begun leaking into the news context.

    Every test here needs two snapshots that DISAGREE: with one snapshot the
    first and the latest coincide and the assertion passes either way.
    """

    def _sports_record(self, event_id, *, estimated, baseline=40.0):
        record = _make_record(event_id, estimated=estimated)
        record["probability"]["baseline"] = baseline
        record["source"] = {
            "type": "sports_event",
            "platform": "world_cup_2026",
            "source_id": f"world-cup-2026:{event_id}",
            "tournament": "world_cup_2026",
        }
        return record

    def _resolve(self, tmp, record, snapshot_estimates, *, actual_outcome=100.0):
        store_path = str(Path(tmp) / "event_store.json")
        audit_path = str(Path(tmp) / "event_audit.jsonl")
        with patch.object(store, "_store_path", return_value=store_path), \
                patch.object(audit, "_audit_path", return_value=audit_path), \
                patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
            store.save_event(record)
            for estimate in snapshot_estimates:
                audit.record_event(
                    self._sports_record(record["event_id"], estimated=estimate)
                )
            return ers.resolve_with_calibration(
                event_id=record["event_id"],
                actual_outcome=actual_outcome,
                source="auto_sports",
            )

    def test_sports_event_is_graded_on_its_first_estimate_not_its_latest(self):
        # First sight said 30, a later re-scan said 95 once the facts were out.
        # Grading the latest would score Brier 0.0025 (EXCELLENT) on a number
        # that already half-knew the answer; the committed 30 scores 0.49.
        with tempfile.TemporaryDirectory() as tmp:
            updated = self._resolve(
                tmp,
                self._sports_record("wcCommit", estimated=30.0),
                [30.0, 95.0],
            )
        calibration = updated["record"]["calibration"]
        self.assertEqual(calibration["estimated_probability"], 30.0)
        self.assertEqual(calibration["estimate_basis"], "first_sight")
        self.assertAlmostEqual(calibration["brier_score"], 0.49)

    def test_market_event_still_graded_on_its_latest_estimate(self):
        # Same two snapshots, but a market-derived event: the trajectory tracks a
        # live price, so the latest point remains the right one. This is the
        # test that fails if the new branch is applied to every source type.
        with tempfile.TemporaryDirectory() as tmp:
            record = _make_record("mktLatest", estimated=30.0)
            record["probability"]["baseline"] = 40.0
            record["source"] = {
                "type": "prediction_market",
                "platform": "polymarket",
                "source_id": "0xabc",
            }
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path",
                                 return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(record)
                for estimate in (30.0, 95.0):
                    snap = _make_record("mktLatest", estimated=estimate)
                    snap["source"] = record["source"]
                    audit.record_event(snap)
                updated = ers.resolve_with_calibration(
                    event_id="mktLatest", actual_outcome=100.0, source="manual",
                )
        calibration = updated["record"]["calibration"]
        self.assertEqual(calibration["estimated_probability"], 95.0)
        self.assertEqual(calibration["estimate_basis"], "trajectory_latest")

    def test_compacted_trajectory_falls_back_to_baseline_not_latest(self):
        # The audit log keeps only the most recent EVENT_AUDIT_MAX_PER_EVENT
        # snapshots, so once that many survive the oldest is no longer the
        # first-sight estimate. Grading it would grade a drifted number under
        # the commitment's name; the curated baseline cannot have drifted.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ers.settings, "EVENT_AUDIT_MAX_PER_EVENT", 2):
                updated = self._resolve(
                    tmp,
                    self._sports_record("wcCompact", estimated=30.0, baseline=40.0),
                    [30.0, 95.0],
                )
        calibration = updated["record"]["calibration"]
        self.assertEqual(calibration["estimated_probability"], 40.0)
        self.assertEqual(calibration["estimate_basis"], "baseline_trajectory_compacted")

    def test_sports_event_with_no_trajectory_uses_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            updated = self._resolve(
                tmp,
                self._sports_record("wcNoTraj", estimated=30.0, baseline=40.0),
                [],
            )
        calibration = updated["record"]["calibration"]
        self.assertEqual(calibration["estimated_probability"], 40.0)
        self.assertEqual(calibration["estimate_basis"], "baseline_no_trajectory")

    def test_sports_event_writes_no_prediction_row(self):
        # The commitment is applied at scoring time precisely because there is
        # no prediction row to freeze. If one ever appears here, the edge would
        # be computed against a curated baseline rather than a market price.
        with tempfile.TemporaryDirectory() as tmp:
            self._resolve(
                tmp,
                self._sports_record("wcNoPred", estimated=30.0),
                [30.0, 95.0],
            )
            with patch.object(sqlite_db, "loop_db_path",
                              return_value=str(Path(tmp) / "v2_loop.db")):
                self.assertIsNone(preds.get_prediction("wcNoPred"))


class AutoResolveEventsTests(unittest.TestCase):
    """auto_resolve_events: fetch resolved markets, match, resolve each."""

    def setUp(self):
        # Multi-source auto-resolve also pulls Manifold + Kalshi; default both to
        # empty so these unit tests stay network-free. Individual
        # tests re-patch as needed.
        for module in (mfs, kes):
            patcher = patch.object(
                module, "fetch_resolved_markets", new=AsyncMock(return_value=[])
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        direct_patcher = patch.object(
            phs, "fetch_markets_by_ids", new=AsyncMock(return_value=[])
        )
        direct_patcher.start()
        self.addCleanup(direct_patcher.stop)

        # Seal the real stores for the whole class. auto_resolve_events now runs
        # reconcile_predictions() first, which reads the event store and (for any
        # resolved event) the loop DB - so even an early-return test must be
        # isolated or it leaks backend/v2_loop.db. Per-test `with` blocks that set
        # their own tmp paths still override these (inner patch wins).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        for target in (
            patch.object(store, "_store_path", return_value=str(base / "event_store.json")),
            patch.object(audit, "_audit_path", return_value=str(base / "event_audit.jsonl")),
            patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_auto_resolve_does_not_fetch_manifold_resolved_markets(self):
        with patch.object(phs, "fetch_resolved_markets",
                          new=AsyncMock(return_value=[])), \
                patch.object(kes, "fetch_resolved_markets",
                             new=AsyncMock(return_value=[])), \
                patch.object(mfs, "fetch_resolved_markets",
                             new=AsyncMock(return_value=[{
                                 "question": "Manifold resolved market",
                                 "actual_outcome": 100.0,
                                 "id": "m1",
                             }])) as manifold_fetch:
            result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))

        manifold_fetch.assert_not_awaited()
        self.assertNotIn("Manifold", result["by_source"])

    def test_manifold_verified_link_is_not_direct_fetched(self):
        record = _make_record("evtOldManifold", value_score=30)
        record["source"] = {
            "type": "prediction_market",
            "platform": "Manifold",
            "source_id": "old-manifold-1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[])), \
                    patch.object(kes, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[])), \
                    patch.object(mfs, "fetch_markets_by_ids",
                                 new=AsyncMock(return_value=[{
                                     "question": "Should not be used",
                                     "actual_outcome": 100.0,
                                     "id": "old-manifold-1",
                                 }])) as direct_fetch:
                store.save_event(record)
                links.upsert_link("evtOldManifold", contract_id="old-manifold-1",
                                  market_name="Manifold", verified=True)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtOldManifold")

        direct_fetch.assert_not_awaited()
        self.assertEqual(result["resolved_count"], 0)
        self.assertIsNone(after["record"].get("outcome"))

    def test_no_resolved_markets_returns_no_data(self):
        with patch.object(phs, "fetch_resolved_markets",
                          new=AsyncMock(return_value=[])):
            result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
        self.assertEqual(result["status"], "no_resolved_markets")
        self.assertEqual(result["resolved_count"], 0)

    def test_warns_when_kalshi_open_events_exist_but_resolved_is_empty(self):
        record = _make_record("evtKalshi", value_score=30)
        record["source"] = {
            "type": "prediction_market",
            "platform": "Kalshi",
            "source_id": "KALSHI-1",
        }
        store.save_event(record)

        with patch.object(phs, "fetch_resolved_markets",
                          new=AsyncMock(return_value=[])), \
                self.assertLogs("app.services.event_resolve_service", level="WARNING") as logs:
            result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))

        self.assertEqual(result["status"], "no_resolved_markets")
        self.assertTrue(
            any("Kalshi returned 0 resolved markets" in msg for msg in logs.output)
        )

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
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
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

    def test_dry_run_returns_matches_without_writing(self):
        resolved_market = {
            "question": "Will Bitcoin reach $100,000 by end of 2026?",
            "actual_outcome": 100.0,
            "id": "poly-dry",
        }
        record = _make_record("evtDry", value_score=30)
        record["event_title"] = "Will Bitcoin reach $100,000 by end of 2026?"
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                store.save_event(record)
                _seed_open_act("evtDry", ai_probability=80.0)
                result = asyncio.run(ers.auto_resolve_events(
                    resolved_limit=50,
                    dry_run=True,
                ))
                after = store.get_event("evtDry")
                prediction = preds.get_prediction("evtDry")
                pending = links.list_pending()

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(result["matches"][0]["result"], "would_resolve")
        self.assertEqual(result["matches"][0]["contract_id"], "poly-dry")
        self.assertIsNone(after["record"].get("outcome"))
        self.assertEqual(prediction["status"], "open")
        self.assertEqual(pending, [])

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
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                store.save_event(record)
                # Pre-resolve it.
                ers.resolve_with_calibration(
                    event_id="evtDone", actual_outcome=100.0, source="manual",
                )
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
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
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
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
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
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
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

    def test_cancelled_source_is_isolated(self):
        """A cancelled source must be dropped like a failing one.

        asyncio.CancelledError is a BaseException, not an Exception, so
        gather(return_exceptions=True) hands it back as a result value that an
        `isinstance(result, Exception)` guard lets through. It then reached
        len(result) and lost the whole auto-resolve pass rather than just the
        one source.
        """
        record = _make_record("evtCancelled", value_score=30)
        record["event_title"] = "Will it rain in Seattle tomorrow?"
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path",
                              return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path",
                                 return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path",
                                 return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[
                                     {"question": "Will it rain in Seattle tomorrow?",
                                      "actual_outcome": 100.0}])), \
                    patch.object(kes, "fetch_resolved_markets",
                                 new=AsyncMock(side_effect=asyncio.CancelledError())):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtCancelled")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["by_source"], {"Polymarket": 1})
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(after["record"]["outcome"]["actual_outcome"], 100.0)

    def test_fuzzy_verified_match_uses_match_score_as_confidence(self):
        market_question = (
            "Will alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        )
        event_question = (
            "Will alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
        )
        expected_score = 11 / 12
        market = {
            "question": market_question,
            "actual_outcome": 100.0,
            "id": "poly-fuzzy-verified",
        }
        record = _make_record("evtFuzzyVerified", value_score=30)
        record["event_title"] = event_question
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(ers.settings, "AUTO_VERIFY_THRESHOLD", 0.90), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[market])):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtFuzzyVerified")

        self.assertEqual(result["resolved_count"], 1)
        self.assertAlmostEqual(
            after["record"]["outcome"]["confidence"],
            expected_score,
        )

    def test_legacy_manifold_source_id_is_not_direct_settled(self):
        record = _make_record("evtDirect", value_score=30)
        record["event_title"] = "A local Manifold event that search will not match"
        record["source"] = {
            "type": "prediction_market",
            "platform": "Manifold",
            "source_id": "manifold-direct-1",
        }
        unrelated_market = {
            "question": "Completely unrelated resolved market",
            "actual_outcome": 100.0,
            "id": "poly-unrelated",
        }
        direct_market = {
            "question": "Direct Manifold settled market",
            "actual_outcome": 0.0,
            "id": "manifold-direct-1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[unrelated_market])), \
                    patch.object(mfs, "fetch_markets_by_ids",
                                 new=AsyncMock(return_value=[direct_market])) as direct_fetch:
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtDirect")

        direct_fetch.assert_not_awaited()
        self.assertEqual(result["resolved_count"], 0)
        self.assertIsNone(after["record"].get("outcome"))

    def test_direct_polymarket_linked_settles_when_resolved_list_misses_contract(self):
        record = _make_record("evtPolyDirect", value_score=30)
        record["event_title"] = "A Polymarket event missing from top resolved list"
        record["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": "poly-direct-1",
        }
        unrelated_market = {
            "question": "Completely unrelated resolved market",
            "actual_outcome": 100.0,
            "id": "poly-unrelated",
        }
        direct_market = {
            "question": "Direct Polymarket settled market",
            "actual_outcome": 0.0,
            "id": "poly-direct-1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[unrelated_market])), \
                    patch.object(phs, "fetch_markets_by_ids",
                                 new=AsyncMock(return_value=[direct_market]), create=True) as direct_fetch:
                store.save_event(record)
                links.upsert_link("evtPolyDirect", contract_id="poly-direct-1",
                                  market_name="Polymarket", verified=True)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtPolyDirect")

        direct_fetch.assert_awaited_once_with(["poly-direct-1"])
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["matches"][-1]["result"], "resolved_by_contract")
        self.assertEqual(after["record"]["outcome"]["actual_outcome"], 0.0)
        self.assertEqual(after["record"]["outcome"]["source"], "auto_market")


class Milestone0LinkGateTests(unittest.TestCase):
    """M0: fail-closed event->market link gating in auto-resolve."""

    def setUp(self):
        for module in (mfs, kes):
            patcher = patch.object(
                module, "fetch_resolved_markets", new=AsyncMock(return_value=[])
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        direct_patcher = patch.object(
            phs, "fetch_markets_by_ids", new=AsyncMock(return_value=[])
        )
        direct_patcher.start()
        self.addCleanup(direct_patcher.stop)

    def test_fuzzy_match_is_pending_not_scored(self):
        """A non-exact (fuzzy) match is below the default auto-verify threshold
        (1.0): it is recorded as an unverified link and NOT scored - fail-closed."""
        # 5 shared tokens + 1 extra -> Jaccard 5/6 = 0.833 (>= 0.82, < 1.0).
        market = {"question": "Will Bitcoin reach 100000 dollars by 2026 soon",
                  "actual_outcome": 100.0, "id": "poly-1"}
        record = _make_record("evtFuzzy", value_score=30)
        record["event_title"] = "Will Bitcoin reach 100000 dollars by 2026"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=db_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[market])):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtFuzzy")
                pending = links.list_pending()
        self.assertEqual(result["resolved_count"], 0)
        self.assertEqual(result["pending_count"], 1)
        self.assertIsNone(after["record"].get("outcome"))  # not scored
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0]["verified"])
        self.assertEqual(pending[0]["event_id"], "evtFuzzy")

    def test_exact_match_creates_verified_link_and_scores(self):
        market = {"question": "Will the Fed cut rates in July 2026?",
                  "actual_outcome": 100.0, "id": "poly-2"}
        record = _make_record("evtExact", value_score=30)
        record["event_title"] = "Will the Fed cut rates in July 2026?"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=db_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[market])):
                store.save_event(record)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtExact")
                link = links.get_verified_link("evtExact")
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["pending_count"], 0)
        self.assertIsNotNone(after["record"]["outcome"])
        self.assertIsNotNone(link)
        self.assertEqual(link["contract_id"], "poly-2")
        self.assertEqual(link["market_name"], "Polymarket")

    def test_linked_event_not_scored_against_different_contract(self):
        """M0 identity integrity (contract-first): an event verified-linked to
        contract A is NEVER scored against a different contract B that merely
        matches by question text. Under contract-first settlement the event waits
        for A to settle rather than being marked invalid - the same fail-closed
        guarantee (no scoring against the wrong contract), but recoverable: it can
        still resolve correctly when A actually settles.
        """
        market = {"question": "Will it snow in Denver in December 2026?",
                  "actual_outcome": 100.0, "id": "contract-B"}
        record = _make_record("evtDiv", value_score=30)
        record["event_title"] = "Will it snow in Denver in December 2026?"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=db_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[market])):
                store.save_event(record)
                # Pre-existing verified link to a DIFFERENT contract (A), which is
                # NOT in the resolved set (only B settled).
                links.upsert_link("evtDiv", contract_id="contract-A",
                                  market_name="Polymarket", verified=True)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtDiv")
                resolved = store.list_resolved_events()
        self.assertEqual(result["resolved_count"], 0)        # not scored against B
        self.assertIsNone(after["record"].get("outcome"))    # waits for A, stays unresolved
        self.assertEqual([e["event_id"] for e in resolved], [])  # excluded from calibration

    def test_linked_event_settles_by_contract_id(self):
        """Contract-first PRIMARY path: an event verified-linked to contract A is
        settled the moment A appears in the resolved set, regardless of whether
        the market's question text still matches the event title."""
        market = {"question": "TOTALLY DIFFERENT WORDING NOW",
                  "actual_outcome": 100.0, "id": "contract-A"}
        record = _make_record("evtLink", value_score=30, estimated=80.0)
        record["event_title"] = "Will the Fed cut rates in July 2026?"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=db_path), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[market])):
                store.save_event(record)
                links.upsert_link("evtLink", contract_id="contract-A",
                                  market_name="Polymarket", verified=True)
                result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                after = store.get_event("evtLink")
        self.assertEqual(result["resolved_count"], 1)        # settled by contract id alone
        self.assertEqual(after["record"]["outcome"]["status"], "resolved")
        self.assertEqual(after["record"]["outcome"]["actual_outcome"], 100.0)
        self.assertEqual(after["record"]["outcome"]["confidence"], 1.0)


class Milestone1PredictionScoringTests(unittest.TestCase):
    """M1: resolving an event scores its frozen, point-in-time prediction."""

    def _market_record(self, event_id, estimated=80.0, baseline=50.0):
        return {
            "event_id": event_id,
            "probability": {"baseline": baseline, "estimated": estimated},
            "source": {"type": "prediction_market", "platform": "Polymarket",
                       "source_id": "poly-1"},
        }

    def test_resolution_scores_act_prediction(self):
        # An act prediction resolves to terminal 'scored' (enters calibration).
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtPS", value_score=30))
                _seed_open_act("evtPS", ai_probability=80.0)
                ers.resolve_with_calibration(
                    event_id="evtPS", actual_outcome=100.0, source="manual",
                )
                scored = preds.get_prediction("evtPS")
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["actual_outcome"], 100.0)
        self.assertAlmostEqual(scored["brier_score"], 0.04)  # (80-100)/100 ^2

    def test_resolution_observes_watch_prediction(self):
        # A non-act prediction resolves to 'observed': outcome recorded, but it
        # stays out of the act-only prediction calibration.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtWatch", value_score=30))
                # Dormant segment + large edge -> provisional_act (cold-start bypass).
                preds.freeze_prediction(self._market_record("evtWatch", estimated=95.0))
                ers.resolve_with_calibration(
                    event_id="evtWatch", actual_outcome=100.0, source="manual",
                )
                after = preds.get_prediction("evtWatch")
                calib_n = preds.calibration_summary()["n"]  # inside patch (loop DB)
        self.assertEqual(after["decision"], "provisional_act")
        self.assertEqual(after["status"], "observed")
        self.assertIsNotNone(after["brier_score"])
        self.assertEqual(calib_n, 0)  # excluded from act-only calibration

    def test_invalid_resolution_voids_prediction(self):
        # A non-genuine resolution (identity conflict -> invalid) closes the open
        # prediction as 'voided': no Brier, and it drops off the opportunity
        # surface (no longer shows as actionable).
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtInv", value_score=30))
                preds.freeze_prediction(self._market_record("evtInv", estimated=80.0))
                ers.resolve_with_calibration(
                    event_id="evtInv", actual_outcome=100.0,
                    source="auto_market", status="invalid",
                )
                after = preds.get_prediction("evtInv")
                open_ids = [o["event_id"] for o in preds.list_open_opportunities()]
        self.assertEqual(after["status"], "voided")      # closed, not scored
        self.assertIsNone(after["brier_score"])
        self.assertNotIn("evtInv", open_ids)             # off the opportunity surface


class ResolutionCriteriaPersistenceTests(unittest.TestCase):
    """M0 exit criteria: a resolved event's link carries a resolution-criteria
    string (the event-side criteria the analysis engine understood), not an
    empty column."""

    def test_manual_resolve_persists_resolution_criteria(self):
        record = _make_record("evtRCm", value_score=30)
        record["semantics"] = {"resolution_criteria": "YES if CPI < 3.0% in June 2026",
                               "time_horizon": "June 2026", "entities": ["CPI"]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(record)
                ers.resolve_with_calibration(
                    event_id="evtRCm", actual_outcome=100.0, source="manual",
                )
                link = links.get_verified_link("evtRCm")
        self.assertIsNotNone(link)
        self.assertEqual(link["resolution_criteria"], "YES if CPI < 3.0% in June 2026")

    def test_auto_resolve_persists_resolution_criteria(self):
        market = {"question": "Will the Fed cut rates in July 2026?",
                  "actual_outcome": 100.0, "id": "poly-rc"}
        record = _make_record("evtRCa", value_score=30)
        record["event_title"] = "Will the Fed cut rates in July 2026?"
        record["semantics"] = {"resolution_criteria": "YES if FOMC cuts the target range",
                               "time_horizon": "July 2026", "entities": ["Fed"]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(phs, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[market])):
                store.save_event(record)
                asyncio.run(ers.auto_resolve_events(resolved_limit=50))
                link = links.get_verified_link("evtRCa")
        self.assertIsNotNone(link)
        self.assertEqual(link["resolution_criteria"], "YES if FOMC cuts the target range")

    def test_missing_semantics_leaves_criteria_empty_not_error(self):
        # A record without semantics must still resolve; criteria is just "".
        record = _make_record("evtRCn", value_score=30)  # no semantics key
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(record)
                ers.resolve_with_calibration(
                    event_id="evtRCn", actual_outcome=100.0, source="manual",
                )
                link = links.get_verified_link("evtRCn")
        self.assertIsNotNone(link)
        self.assertEqual(link["resolution_criteria"], "")


class ReconcilePredictionsTests(unittest.TestCase):
    """reconcile_predictions heals orphans: event resolved but prediction open."""

    def test_heals_orphan_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                # Insert an open act prediction, then resolve ONLY the event
                # store outcome out-of-band - simulating a crash after the JSON
                # write but before scoring (a pre-fix orphan).
                rec = _make_record("evtOrphan", estimated=90.0, value_score=30)
                store.save_event(rec)
                _seed_open_act("evtOrphan", ai_probability=90.0, market_probability=50.0)
                store.resolve_event("evtOrphan", {
                    "status": "resolved", "actual_outcome": 100.0,
                    "confidence": 1.0, "resolved_at": "t", "source": "auto_market",
                })
                self.assertEqual(preds.get_prediction("evtOrphan")["status"], "open")

                healed = ers.reconcile_predictions()

                self.assertEqual(healed, 1)
                p = preds.get_prediction("evtOrphan")
                self.assertEqual(p["status"], "scored")  # act row -> scored
                self.assertEqual(p["actual_outcome"], 100.0)

    def test_no_orphan_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                self.assertEqual(ers.reconcile_predictions(), 0)


class AutoResolveScanCostTests(unittest.TestCase):
    """auto_resolve_events must not scale its query count with the event store.

    The scan walks EVERY stored event (deliberately unbounded - see the comment
    in the service). It called get_verified_link() per event in two separate
    loops, so a store of N events cost 2N connections and queries, all on the
    event loop.
    """

    def setUp(self):
        for module in (mfs, kes, phs):
            patcher = patch.object(
                module, "fetch_resolved_markets", new=AsyncMock(return_value=[])
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        direct_patcher = patch.object(
            phs, "fetch_markets_by_ids", new=AsyncMock(return_value=[])
        )
        direct_patcher.start()
        self.addCleanup(direct_patcher.stop)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        for target in (
            patch.object(store, "_store_path", return_value=str(base / "event_store.json")),
            patch.object(audit, "_audit_path", return_value=str(base / "event_audit.jsonl")),
            patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")),
        ):
            target.start()
            self.addCleanup(target.stop)

    def _seed(self, n):
        """n linked, unresolved events plus one resolved market to match on.

        Without a resolved market auto_resolve_events early-returns
        "no_resolved_markets" and never reaches the scan, so a test that
        omitted it would pass vacuously.
        """
        for i in range(n):
            record = _make_record(f"scan-{i}", value_score=30)
            store.save_event(record)
            links.upsert_link(
                f"scan-{i}",
                contract_id=f"c-{i}",
                market_name="Polymarket",
                verified=True,
            )
        return AsyncMock(return_value=[
            {"question": "unmatched market", "actual_outcome": 100.0, "id": "zzz"}
        ])

    def test_link_lookups_do_not_scale_with_the_event_store(self):
        """The N+1 regression guard.

        Counting is done at ``sqlite_db.connect`` - the chokepoint every store
        funnels through - because each module binds its own ``reading``, so
        patching one module's binding would miss the other's. Counting queries
        rather than wall-clock keeps this deterministic on a loaded machine.
        """
        market = self._seed(10)

        selects = []
        real_connect = sqlite_db.connect

        class _CountingConn:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args, **kwargs):
                if "event_market_links" in sql and sql.strip().upper().startswith("SELECT"):
                    selects.append(sql)
                return self._conn.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        with patch.object(
            sqlite_db, "connect", lambda path: _CountingConn(real_connect(path))
        ), patch.object(phs, "fetch_resolved_markets", new=market):
            result = asyncio.run(ers.auto_resolve_events(resolved_limit=50))

        self.assertNotEqual(
            result["status"], "no_resolved_markets",
            "the scan must actually run for this guard to mean anything",
        )

        # Two bulk reads at most (one per scan loop); the old code issued one
        # per event per loop.
        self.assertLessEqual(
            len(selects),
            2,
            f"auto-resolve issued {len(selects)} link SELECTs for 10 events; "
            "link lookups must not scale with the event store",
        )

    def test_scan_does_not_starve_the_event_loop(self):
        """The store scan runs off-loop.

        The scan body has no awaits at all, so every blocking store call inside
        it ran on the event loop thread - the API stopped serving requests for
        the whole job.
        """
        import time

        market = self._seed(3)

        async def _exercise():
            ticks = 0

            async def _heartbeat():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.005)
                    ticks += 1

            beat = asyncio.create_task(_heartbeat())
            real = ers.histories_by_event

            def _slow():
                time.sleep(0.25)
                return real()

            with patch.object(ers, "histories_by_event", _slow),                     patch.object(phs, "fetch_resolved_markets", new=market):
                result = await ers.auto_resolve_events(resolved_limit=50)
            beat.cancel()
            return ticks, result

        ticks, result = asyncio.run(_exercise())

        self.assertNotEqual(
            result["status"], "no_resolved_markets",
            "the scan must actually run for this guard to mean anything",
        )

        self.assertGreater(
            ticks,
            15,
            f"the auto-resolve scan blocked the event loop: the heartbeat only "
            f"got {ticks} ticks during a 0.25s store read",
        )


if __name__ == "__main__":
    unittest.main()
