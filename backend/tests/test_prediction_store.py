"""Tests for prediction_store (M1 frozen-prediction ledger).

Covers freezing a committed prediction (market-gated, idempotent), scoring it
against an outcome, and the global calibration summary. Plus the _persist_events
wiring (freeze hook). Each test points the loop DB at a temp SQLite file via
sqlite_db.loop_db_path, so no real v2_loop.db is touched.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import prediction_store as preds
from app.memory import event_market_link_store as links
from app.utils import sqlite_db


def _market_record(event_id="evtM", estimated=70.0, baseline=50.0, contract="c1"):
    """Minimal market-derived record (what freeze_prediction reads)."""
    return {
        "event_id": event_id,
        "probability": {"baseline": baseline, "estimated": estimated},
        "source": {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": contract,
            "liquidity": 1000.0,
            "volume": 5000.0,
        },
    }


def _seed_resolved(
    event_id,
    *,
    decision,
    status,
    brier,
    raw_edge=10.0,
    market_probability=50.0,
    actual_outcome=100.0,
    category="cpi",
    ai_probability=80.0,
):
    """Insert a fully-resolved prediction row directly, with a chosen decision /
    status / brier. Lets the act-only口径 tests assert the calibration filters
    precisely, without bootstrapping the diagnose() math to manufacture an act."""
    path = sqlite_db.loop_db_path()
    preds._ensure_schema(path)
    with sqlite_db.writing(path) as conn:
        conn.execute(
            """
            INSERT INTO predictions (
                id, event_id, base_rate_category, ai_probability,
                market_probability, raw_edge, decision, status,
                actual_outcome, brier_score, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, event_id, category, ai_probability, market_probability,
             raw_edge, decision, status, actual_outcome, brier, "t0", "t1"),
        )


def _act_record(event_id, category, estimated, baseline):
    """A market record with NO liquidity (liq factor 1.0) so a bootstrapped,
    qualified, high-skill segment yields a real act decision."""
    rec = {
        "event_id": event_id,
        "probability": {"baseline": baseline, "estimated": estimated},
        "source": {"type": "prediction_market", "platform": "Polymarket",
                   "source_id": event_id},
        "legacy_analysis": {"base_rate_category": category},
    }
    return rec


def _bootstrap_act(event_id, *, category, estimated, baseline):
    """Drive the real freeze+score path to a genuine act row, proving the loop
    can leave dormancy. Freeze + score 8 watch predictions in `category` with a
    good Brier (skill ~0.84 -> trust ~0.84, qualified at min_samples=8), then
    freeze the target with a large edge: qualified + trust*raw >= ACT_EDGE -> act."""
    for i in range(8):
        boot = _act_record(f"{event_id}_boot{i}", category, estimated=80.0, baseline=50.0)
        frozen = preds.freeze_prediction(boot)  # dormant trust 0.5, liq 1.0 -> adj 15 -> watch
        assert frozen["decision"] == "watch", frozen["decision"]
        preds.score_prediction(f"{event_id}_boot{i}", actual_outcome=100.0)  # brier 0.04 -> observed
    frozen = preds.freeze_prediction(_act_record(event_id, category, estimated, baseline))
    assert frozen["decision"] == "act", frozen["decision"]
    return frozen


class FreezePredictionTests(unittest.TestCase):
    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def test_freeze_creates_prediction(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            frozen = preds.freeze_prediction(_market_record(estimated=70.0, baseline=50.0))
            self.assertIsNotNone(frozen)
            self.assertEqual(frozen["ai_probability"], 70.0)
            self.assertEqual(frozen["market_probability"], 50.0)
            self.assertEqual(frozen["raw_edge"], 20.0)
            self.assertEqual(frozen["contract_id"], "c1")
            self.assertEqual(frozen["platform"], "Polymarket")
            self.assertEqual(frozen["liquidity"], 1000.0)
            self.assertEqual(frozen["volume"], 5000.0)
            self.assertEqual(frozen["status"], "open")

    def test_freeze_seeds_verified_link(self):
        # 补-A: freezing a market event also seeds a verified event->contract link
        # from the known source_id, so auto_resolve's contract-first PRIMARY path
        # engages on the first pass (instead of needing an exact text match).
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            rec = _market_record("evtLink", estimated=70.0, contract="cABC")
            rec["event_title"] = "Will X happen?"
            preds.freeze_prediction(rec)
            link = links.get_verified_link("evtLink")
            self.assertIsNotNone(link)
            self.assertEqual(link["contract_id"], "cABC")
            self.assertTrue(link["verified"])
            self.assertEqual(link["link_method"], "freeze")
            self.assertEqual(link["market_question"], "Will X happen?")

    def test_rescan_does_not_re_verify_link(self):
        # A re-scan freeze is a no-op (DO NOTHING); it must not rewrite the link
        # or silently re-verify one a human deliberately un-verified.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            preds.freeze_prediction(_market_record("evtRV", estimated=70.0, contract="cRV"))
            links.set_verified("evtRV", "cRV", False)  # human quarantines it
            preds.freeze_prediction(_market_record("evtRV", estimated=95.0, contract="cRV"))
            self.assertIsNone(links.get_verified_link("evtRV"))  # stays un-verified

    def test_rescan_is_noop_commitment_frozen(self):
        # One Event -> One Prediction: the first freeze is the committed estimate;
        # any later re-scan is a no-op (ON CONFLICT DO NOTHING), even when the new
        # estimate would yield a different decision. The commitment is frozen at
        # decision time and never overwritten or re-versioned.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            preds.freeze_prediction(_market_record("evtX", estimated=70.0))  # raw 20 -> skip
            # A later re-scan with a very different estimate (would be watch):
            preds.freeze_prediction(_market_record("evtX", estimated=95.0))
            frozen = preds.get_prediction("evtX")
            self.assertEqual(frozen["ai_probability"], 70.0)  # original, frozen
            self.assertEqual(frozen["status"], "open")
            self.assertEqual(len(preds.list_recent()), 1)  # exactly one row per event

    def test_news_event_is_not_frozen(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            news = _market_record("evtNews")
            news["source"]["type"] = "open_web"  # not a market
            self.assertIsNone(preds.freeze_prediction(news))
            self.assertIsNone(preds.get_prediction("evtNews"))

    def test_missing_contract_is_not_frozen(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            rec = _market_record("evtNC")
            rec["source"]["source_id"] = ""  # no contract id
            self.assertIsNone(preds.freeze_prediction(rec))

    def test_missing_probability_is_not_frozen(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            rec = _market_record("evtNP")
            rec["probability"] = {}  # no baseline/estimated
            self.assertIsNone(preds.freeze_prediction(rec))


class ScorePredictionTests(unittest.TestCase):
    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def test_act_row_scores_to_scored(self):
        # A bootstrapped act row resolves to terminal status 'scored'.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            _bootstrap_act("evtAct", category="cpi", estimated=80.0, baseline=50.0)
            scored = preds.score_prediction("evtAct", actual_outcome=100.0)
            self.assertIsNotNone(scored)
            self.assertEqual(scored["decision"], "act")
            self.assertEqual(scored["status"], "scored")
            self.assertEqual(scored["actual_outcome"], 100.0)
            # Brier((80-100)/100)^2 = 0.04
            self.assertAlmostEqual(scored["brier_score"], 0.04)

    def test_watch_row_scores_to_observed(self):
        # A dormant freeze (trust 0.5) caps at watch; resolving it records the
        # outcome but moves it to 'observed', NOT 'scored' - it never enters the
        # act-only prediction calibration.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            frozen = preds.freeze_prediction(_market_record("evtW", estimated=95.0, baseline=50.0))
            self.assertEqual(frozen["decision"], "watch")
            observed = preds.score_prediction("evtW", actual_outcome=100.0)
            self.assertEqual(observed["status"], "observed")
            self.assertIsNotNone(observed["brier_score"])  # recorded for diagnostics
            self.assertEqual(preds.calibration_summary()["n"], 0)  # excluded

    def test_score_with_no_prediction_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            self.assertIsNone(preds.score_prediction("ghost", actual_outcome=100.0))

    def test_score_is_idempotent_after_scored(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            _bootstrap_act("evtS2", category="cpi", estimated=80.0, baseline=50.0)
            first = preds.score_prediction("evtS2", actual_outcome=100.0)
            second = preds.score_prediction("evtS2", actual_outcome=0.0)  # no longer open
            self.assertIsNone(second)
            self.assertAlmostEqual(preds.get_prediction("evtS2")["brier_score"],
                                   first["brier_score"])  # unchanged


class CalibrationSummaryTests(unittest.TestCase):
    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def test_no_data_before_any_scored(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            preds.freeze_prediction(_market_record("evtOpen"))  # open, not scored
            summary = preds.calibration_summary()
            self.assertEqual(summary["n"], 0)
            self.assertEqual(summary["grade"], "no_data")

    def test_aggregates_scored_predictions(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            # Two act rows: brier 0.04 (edge 30) and 0.25 (edge 0).
            _seed_resolved("e1", decision="act", status="scored", brier=0.04, raw_edge=30.0)
            _seed_resolved("e2", decision="act", status="scored", brier=0.25, raw_edge=0.0)
            summary = preds.calibration_summary()
            self.assertEqual(summary["n"], 2)
            self.assertAlmostEqual(summary["brier_score"], 0.145)  # mean(0.04, 0.25)
            self.assertEqual(summary["grade"], "ACCEPTABLE")        # 0.145 <= 0.15
            self.assertAlmostEqual(summary["mean_raw_edge"], 15.0)  # mean(30, 0)

    def test_calibration_excludes_watch_and_skip(self):
        # The act-only invariant: watch/skip rows that resolved to 'observed'
        # never enter the headline calibration, even with a Brier recorded.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            _seed_resolved("act1", decision="act", status="scored", brier=0.04, raw_edge=20.0)
            _seed_resolved("watch1", decision="watch", status="observed", brier=0.01, raw_edge=15.0)
            _seed_resolved("skip1", decision="skip", status="observed", brier=0.02, raw_edge=1.0)
            summary = preds.calibration_summary()
            self.assertEqual(summary["n"], 1)                       # only the act row
            self.assertAlmostEqual(summary["brier_score"], 0.04)
            self.assertAlmostEqual(summary["mean_raw_edge"], 20.0)
            self.assertEqual(set(summary["by_category"]), {"cpi"})  # one act category
            self.assertEqual(summary["by_category"]["cpi"]["n"], 1)


class PersistEventsFreezeTests(unittest.TestCase):
    """The freeze hook: _persist_events freezes exactly one prediction per
    market event and does not duplicate on re-persist."""

    def test_persist_freezes_one_prediction(self):
        from app.memory import event_store as store
        from app.services import event_audit_service as audit
        from app.services import event_intelligence_service as eis
        # Reuse the canonical full EventRecord builder, with a market source.
        from tests.test_event_store import _make_record

        rec = _make_record("evtPersist", value_score=30)
        rec["source"] = {
            "type": "prediction_market", "platform": "Polymarket",
            "source_id": "poly-xyz", "liquidity": 100.0, "volume": 200.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                eis._persist_events([rec])
                eis._persist_events([rec])  # re-scan
                frozen = preds.get_prediction("evtPersist")
                self.assertIsNotNone(frozen)
                self.assertEqual(frozen["contract_id"], "poly-xyz")
                self.assertEqual(len(preds.list_recent()), 1)  # not duplicated

    def test_freeze_failure_does_not_lose_saved_event(self):
        # A freeze error must be isolated: the event is still saved + audited,
        # not swallowed alongside the store write (separate error boundaries).
        from app.memory import event_store as store
        from app.services import event_audit_service as audit
        from app.services import event_intelligence_service as eis
        from tests.test_event_store import _make_record
        from unittest.mock import patch as _patch

        rec = _make_record("evtFreezeErr", value_score=30)
        rec["source"] = {
            "type": "prediction_market", "platform": "Polymarket",
            "source_id": "poly-err", "liquidity": 100.0, "volume": 200.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(Path(tmp) / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    _patch("app.memory.prediction_store.freeze_prediction",
                           side_effect=RuntimeError("freeze boom")):
                eis._persist_events([rec])  # must not raise
                saved = store.get_event("evtFreezeErr")
        self.assertIsNotNone(saved)                          # event survived
        self.assertEqual(saved["record"]["source"]["source_id"], "poly-err")


class Milestone2DiagnosisTests(unittest.TestCase):
    """M2: freeze stores category + diagnosis; segment calibration + by_category."""

    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def _rec(self, event_id, category="fed_hike", estimated=80.0, baseline=50.0, contract=None):
        rec = _market_record(event_id, estimated=estimated, baseline=baseline,
                             contract=contract or event_id)
        rec["legacy_analysis"] = {"base_rate_category": category}
        return rec

    def test_freeze_captures_category_and_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            frozen = preds.freeze_prediction(self._rec("e1", category="fed_hike"))
            self.assertEqual(frozen["base_rate_category"], "fed_hike")
            self.assertIsNotNone(frozen["trust"])
            self.assertTrue(0.0 <= frozen["trust"] <= 1.0)
            self.assertIsNotNone(frozen["adjusted_edge"])
            self.assertIn(frozen["decision"], {"act", "watch", "skip"})

    def test_dormant_segment_never_acts(self):
        # No scored history -> dormant -> decision caps at watch/skip, never act.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            frozen = preds.freeze_prediction(
                self._rec("eBig", category="rare_cat", estimated=99.0, baseline=1.0)
            )
            self.assertNotEqual(frozen["decision"], "act")

    def test_freeze_captures_diagnosis_explanation_fields(self):
        # The diagnosis inputs behind the verdict are frozen on the row (so a
        # decision report explains WHY without recomputing).
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            frozen = preds.freeze_prediction(
                self._rec("eDx", category="cpi", estimated=90.0, baseline=50.0)
            )
            self.assertEqual(frozen["qualified"], 0)        # dormant -> not qualified
            self.assertEqual(frozen["segment_n"], 0)
            self.assertIsNone(frozen["segment_skill"])      # no history yet
            self.assertIsNotNone(frozen["liquidity_factor"])

    def test_segment_skill_aggregates_scored_in_category(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            # Freeze + score several predictions in one category.
            for i in range(3):
                preds.freeze_prediction(self._rec(f"f{i}", category="cpi", estimated=80.0))
                preds.score_prediction(f"f{i}", actual_outcome=100.0)  # brier 0.04 each
            # A different category, unscored, must not bleed in.
            preds.freeze_prediction(self._rec("other", category="elections"))
            seg = preds.segment_skill("cpi")
            self.assertEqual(seg["n"], 3)
            self.assertAlmostEqual(seg["mean_brier"], 0.04)
            self.assertAlmostEqual(seg["skill"], 0.84)
            self.assertEqual(preds.segment_skill("elections")["n"], 0)  # unscored

    def test_calibration_summary_has_by_category(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            _seed_resolved("a", decision="act", status="scored", brier=0.04, category="cpi")
            _seed_resolved("b", decision="act", status="scored", brier=0.25, category="fed_hike")
            summary = preds.calibration_summary()
            self.assertEqual(summary["n"], 2)
            self.assertIn("cpi", summary["by_category"])
            self.assertIn("fed_hike", summary["by_category"])
            self.assertEqual(summary["by_category"]["cpi"]["n"], 1)

    def test_segment_skill_counts_watch_excludes_skip(self):
        # The trust gate counts act+watch (so a fresh category can bootstrap out
        # of dormancy) but excludes skip (an easy agree-with-market forecast
        # whose low Brier would inflate trust).
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            _seed_resolved("s_act", decision="act", status="scored", brier=0.04, category="cpi")
            _seed_resolved("s_watch", decision="watch", status="observed", brier=0.04, category="cpi")
            _seed_resolved("s_skip", decision="skip", status="observed", brier=0.0, category="cpi")
            seg = preds.segment_skill("cpi")
            self.assertEqual(seg["n"], 2)               # act + watch, skip excluded
            self.assertAlmostEqual(seg["mean_brier"], 0.04)

    def test_migrate_adds_columns_to_m1_table(self):
        import sqlite3
        m1_schema = """
        CREATE TABLE predictions (
            id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
            contract_id TEXT, platform TEXT, ai_probability REAL,
            market_probability REAL, raw_edge REAL, liquidity REAL, volume REAL,
            decision TEXT, created_at TEXT, status TEXT, actual_outcome REAL,
            brier_score REAL, resolved_at TEXT
        );
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(m1_schema)
            conn.close()
            with patch.object(sqlite_db, "loop_db_path", return_value=db_path):
                preds.list_recent()  # triggers _ensure_schema -> _migrate
            conn = sqlite3.connect(db_path)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
            conn.close()
        self.assertIn("base_rate_category", cols)
        self.assertIn("trust", cols)
        self.assertIn("adjusted_edge", cols)


class Milestone5OpportunityTests(unittest.TestCase):
    """M5: open-opportunity ranking + realized-vs-predicted edge."""

    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def test_list_open_opportunities_filters_and_ranks(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            # dormant segment (trust 0.5), liquidity 1000 -> liq factor 0.2,
            # so adjusted_edge = raw * 0.5 * 0.2 = raw * 0.1.
            preds.freeze_prediction(_market_record("e1", estimated=95.0, contract="e1"))  # raw 45 -> adj 4.5 watch
            preds.freeze_prediction(_market_record("e2", estimated=85.0, contract="e2"))  # raw 35 -> adj 3.5 watch
            preds.freeze_prediction(_market_record("e3", estimated=55.0, contract="e3"))  # raw 5  -> adj 0.5 skip
            opps = preds.list_open_opportunities(decisions=("act", "watch"))
            ids = [o["event_id"] for o in opps]
        self.assertEqual(ids, ["e1", "e2"])           # skip (e3) excluded, ranked by |adjusted_edge|
        self.assertTrue(all(o["decision"] == "watch" for o in opps))

    def test_list_open_opportunities_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            for i in range(4):
                preds.freeze_prediction(_market_record(f"o{i}", estimated=95.0, contract=f"o{i}"))
            self.assertEqual(len(preds.list_open_opportunities(limit=2)), 2)

    def test_scored_predictions_excluded_from_opportunities(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            preds.freeze_prediction(_market_record("done", estimated=95.0, contract="done"))
            preds.score_prediction("done", actual_outcome=100.0)  # now status=scored
            self.assertEqual(preds.list_open_opportunities(), [])

    def test_realized_edge_and_hit_rate(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            # No scored act predictions yet -> realized_edge None.
            _seed_resolved("w", decision="watch", status="observed", brier=0.04,
                           raw_edge=30.0, market_probability=50.0, actual_outcome=100.0)
            self.assertIsNone(preds.calibration_summary()["realized_edge"])
            # r1: act, raw_edge +30, outcome 100 -> realized sign(+)*(100-50) = +50
            _seed_resolved("r1", decision="act", status="scored", brier=0.04,
                           raw_edge=30.0, market_probability=50.0, actual_outcome=100.0)
            # r2: act, raw_edge -30, outcome 0 -> realized sign(-)*(0-50) = +50
            _seed_resolved("r2", decision="act", status="scored", brier=0.04,
                           raw_edge=-30.0, market_probability=50.0, actual_outcome=0.0)
            summary = preds.calibration_summary()
        self.assertEqual(summary["n"], 2)              # the watch row is excluded
        self.assertEqual(summary["realized_edge"], 50.0)
        self.assertEqual(summary["directional_hit_rate"], 1.0)


class CommitmentMigrationTests(unittest.TestCase):
    """Revert to One Event -> One Prediction: a DB that went through the
    short-lived multi-row experiment (no UNIQUE(event_id), possibly several rows
    per event) is collapsed to one row per event and rebuilt WITH UNIQUE."""

    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def test_multirow_collapses_to_one_and_readds_unique(self):
        import sqlite3
        # A multi-row (no-UNIQUE) table with TWO rows for the same event: an old
        # superseded skip and the current open watch. Collapse must keep the open.
        multirow_schema = """
        CREATE TABLE predictions (
            id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
            contract_id TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '',
            base_rate_category TEXT NOT NULL DEFAULT 'unknown',
            ai_probability REAL, market_probability REAL, raw_edge REAL,
            trust REAL, adjusted_edge REAL,
            liquidity REAL NOT NULL DEFAULT 0.0, volume REAL NOT NULL DEFAULT 0.0,
            decision TEXT NOT NULL DEFAULT 'tracked',
            liquidity_factor REAL, qualified INTEGER, segment_n INTEGER, segment_skill REAL,
            created_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
            actual_outcome REAL, brier_score REAL, resolved_at TEXT
        );
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(multirow_schema)
            conn.execute(
                "INSERT INTO predictions (id, event_id, contract_id, ai_probability, "
                "market_probability, raw_edge, decision, created_at, status) "
                "VALUES ('old', 'evtMig', 'c1', 70.0, 50.0, 20.0, 'skip', 't0', 'superseded')"
            )
            conn.execute(
                "INSERT INTO predictions (id, event_id, contract_id, ai_probability, "
                "market_probability, raw_edge, decision, created_at, status) "
                "VALUES ('cur', 'evtMig', 'c1', 95.0, 50.0, 45.0, 'watch', 't1', 'open')"
            )
            conn.commit()
            conn.close()
            with patch.object(sqlite_db, "loop_db_path", return_value=db_path):
                preds.list_recent()  # triggers _ensure_schema -> _migrate (collapse)
                kept = preds.get_prediction("evtMig")
                # Collapsed to exactly one row, the open commitment was kept.
                self.assertEqual(kept["ai_probability"], 95.0)
                self.assertEqual(kept["status"], "open")
            conn = sqlite3.connect(db_path)
            total = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE event_id='evtMig'"
            ).fetchone()[0]
            # UNIQUE(event_id) is back: a duplicate insert must now fail.
            dup_failed = False
            try:
                conn.execute(
                    "INSERT INTO predictions (id, event_id, contract_id, ai_probability, "
                    "market_probability, raw_edge, decision, created_at, status) "
                    "VALUES ('dup', 'evtMig', 'c1', 10.0, 50.0, -40.0, 'skip', 't2', 'open')"
                )
                conn.commit()
            except sqlite3.IntegrityError:
                dup_failed = True
            conn.close()
        self.assertEqual(total, 1)        # collapsed to one row
        self.assertTrue(dup_failed)       # UNIQUE(event_id) re-enforced

    def test_segment_skill_counts_resolved_act_watch(self):
        # Only resolved act/watch rows feed the trust signal; skip excluded.
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            _seed_resolved("s_act", decision="act", status="scored", brier=0.04, category="cpi")
            _seed_resolved("s_watch", decision="watch", status="observed", brier=0.04, category="cpi")
            _seed_resolved("s_skip", decision="skip", status="observed", brier=0.0, category="cpi")
            seg = preds.segment_skill("cpi")
            self.assertEqual(seg["n"], 2)               # act + watch, skip excluded
            self.assertAlmostEqual(seg["mean_brier"], 0.04)


if __name__ == "__main__":
    unittest.main()

