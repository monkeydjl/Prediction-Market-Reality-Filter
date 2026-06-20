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
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtAuto", value_score=30))
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtAuto", actual_outcome=0.0,
                    source="auto_market", notes="matched: some market",
                ))
                after = store.get_event("evtAuto")
        self.assertEqual(after["record"]["outcome"]["source"], "auto_market")
        self.assertIn("matched", after["record"]["outcome"]["notes"])


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


class Milestone0LinkGateTests(unittest.TestCase):
    """M0: fail-closed event->market link gating in auto-resolve."""

    def setUp(self):
        for module in (mfs, kes):
            patcher = patch.object(
                module, "fetch_resolved_markets", new=AsyncMock(return_value=[])
            )
            patcher.start()
            self.addCleanup(patcher.stop)

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
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtPS", actual_outcome=100.0, source="manual",
                ))
                scored = preds.get_prediction("evtPS")
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["actual_outcome"], 100.0)
        self.assertAlmostEqual(scored["brier_score"], 0.04)  # (80-100)/100 ^2

    def test_resolution_observes_watch_prediction(self):
        # A watch prediction resolves to 'observed': outcome recorded, but it
        # stays out of the act-only prediction calibration.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(_make_record("evtWatch", value_score=30))
                # Dormant segment + liquidity -> caps at watch.
                preds.freeze_prediction(self._market_record("evtWatch", estimated=95.0))
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtWatch", actual_outcome=100.0, source="manual",
                ))
                after = preds.get_prediction("evtWatch")
                calib_n = preds.calibration_summary()["n"]  # inside patch (loop DB)
        self.assertEqual(after["decision"], "watch")
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
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtInv", actual_outcome=100.0,
                    source="auto_market", status="invalid",
                ))
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
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtRCm", actual_outcome=100.0, source="manual",
                ))
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
                asyncio.run(ers.resolve_with_calibration(
                    event_id="evtRCn", actual_outcome=100.0, source="manual",
                ))
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


if __name__ == "__main__":
    unittest.main()
