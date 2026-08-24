"""Q3: the per-domain prior fed into source reliability must be Brier-aware.

Before this, ``domain_reliability.reliability_score`` was ``correct_count /
sample_count`` where ``correct`` meant only "the recommendation direction
matched the outcome". That 0/1 label cannot tell a domain that keeps appearing
on 95%-and-right events from one that appears on 51%-and-lucky ones, yet it was
already feeding ``build_source_reliability`` as a prior weight -- 40% of
``overall_score``, which gates the WAIT downgrade.

These tests pin four things the change turns on:

1. The Brier is taken from the FROZEN estimate, never from the record's latest.
2. It is stance-adjusted: a refuting domain is graded on the complement.
3. ``brier_count`` stays separate from ``sample_count``, so an ungradeable
   attribution is reported as ungradeable rather than as a perfect 0.0.
4. The metric swap is opt-in and actually changes the prior (otherwise the whole
   feature is decorative).
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services.calibration_service_event import brier_score
from app.services.domain_reliability_service import (
    attribute_evidence,
    compute_brier_skill,
    compute_reliability_stats,
)
from app.services.source_reliability_service import (
    _shrunk_brier_skill,
    _shrunk_reliability,
    build_source_reliability,
)
from app.utils import sqlite_db


def _record(
    event_id: str = "e1",
    *,
    direction: str = "YES",
    actual_outcome: float = 100.0,
    estimated: float = 80.0,
    baseline: float = 50.0,
    stance: str = "support",
    domain_url: str = "https://www.reuters.com/article/1",
    resolved: bool = True,
) -> dict:
    """A record that both freeze_prediction and attribute_evidence accept."""
    record: dict = {
        "event_id": event_id,
        "event_title": "t",
        "source": {
            "type": "prediction_market",
            "source_id": f"contract-{event_id}",
            "platform": "polymarket",
            "liquidity": 50000.0,
            "volume": 10000.0,
        },
        "probability": {"estimated": estimated, "baseline": baseline},
        "legacy_analysis": {"base_rate_category": "crypto"},
        "actionable_recommendation": {"direction": direction},
        "evidence_breakdown": [
            {"source": "Reuters", "direction": stance, "credibility": 0.8},
        ],
        "evidence_items": [{"source": "Reuters", "url": domain_url}],
    }
    if resolved:
        record["outcome"] = {"status": "resolved", "actual_outcome": actual_outcome}
    return record


class TestAttributionBrier(unittest.TestCase):
    """attribute_evidence: the committed probability, stance-adjusted."""

    def test_supporting_domain_is_graded_on_the_committed_estimate(self):
        attrs = attribute_evidence(
            _record(actual_outcome=100.0), committed_probability=80.0
        )
        self.assertEqual(len(attrs), 1)
        self.assertAlmostEqual(attrs[0]["brier"], 0.04, places=6)

    def test_refuting_domain_is_graded_on_the_complement(self):
        """A refuter argued for 100-p, so it must not share the supporter's loss.

        p=80 against a YES outcome is a good call (0.04); the domain that
        argued against it made a bad one (0.64). A single event-level Brier
        copied to every domain would credit the refuter with 0.04.
        """
        attrs = attribute_evidence(
            _record(stance="oppose", actual_outcome=100.0),
            committed_probability=80.0,
        )
        self.assertEqual(len(attrs), 1)
        self.assertEqual(attrs[0]["stance"], "refutes")
        self.assertAlmostEqual(attrs[0]["brier"], 0.64, places=6)

    def test_brier_matches_the_score_prediction_convention(self):
        """The support-side value must equal what the ledger itself would store.

        score_prediction writes brier_score(frozen ai_probability, actual) on the
        prediction row. If this module used a different convention -- 0-1 instead
        of 0-100, or the complement -- the two numbers would silently disagree
        and no test above would notice.
        """
        for probability, actual in ((80.0, 100.0), (30.0, 0.0), (55.0, 100.0)):
            with self.subTest(probability=probability, actual=actual):
                attrs = attribute_evidence(
                    _record(actual_outcome=actual),
                    committed_probability=probability,
                )
                self.assertAlmostEqual(
                    attrs[0]["brier"], brier_score(probability, actual), places=9
                )

    def test_no_committed_probability_leaves_brier_unset(self):
        attrs = attribute_evidence(_record())
        self.assertEqual(len(attrs), 1)
        self.assertIsNone(attrs[0]["brier"])
        # The 0/1 label still lands -- the event is gradeable for direction.
        self.assertTrue(attrs[0]["correct"])

    def test_never_falls_back_to_the_records_latest_estimate(self):
        """The record's ai_probability is rewritten by every re-scan.

        A scan that ran after the outcome leaked would grade the model on an
        estimate it never committed to, so the fallback must not exist: with no
        committed probability the Brier is absent, even though the record
        carries a perfectly parseable number.
        """
        record = _record(estimated=99.0, actual_outcome=100.0)
        record["ai_probability"] = 99.0

        attrs = attribute_evidence(record)
        self.assertIsNone(attrs[0]["brier"])

        # And when the committed value IS supplied, that is what gets graded --
        # not the 99 sitting on the record.
        attrs = attribute_evidence(record, committed_probability=55.0)
        self.assertAlmostEqual(attrs[0]["brier"], brier_score(55.0, 100.0), places=9)
        self.assertNotAlmostEqual(attrs[0]["brier"], brier_score(99.0, 100.0), places=4)

    def test_unusable_probabilities_yield_no_brier(self):
        for value in (None, "80", True, [80.0]):
            with self.subTest(value=value):
                attrs = attribute_evidence(_record(), committed_probability=value)
                self.assertIsNone(attrs[0]["brier"])

    def test_non_finite_probability_is_rejected_not_clamped(self):
        """NaN must not become a perfect prediction.

        ``min(100.0, nan)`` returns 100.0 -- every NaN comparison is False, so
        the clamp falls through and a nonsense value would grade as a maximally
        confident call and, on a YES outcome, as a Brier of 0.0.
        """
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                attrs = attribute_evidence(
                    _record(actual_outcome=100.0), committed_probability=value
                )
                self.assertIsNone(attrs[0]["brier"])

    def test_out_of_range_probability_is_clamped(self):
        """Clamped, not rejected: a stored 150 grades as a confident 100."""
        attrs = attribute_evidence(
            _record(actual_outcome=100.0), committed_probability=150.0
        )
        self.assertAlmostEqual(attrs[0]["brier"], 0.0, places=9)
        attrs = attribute_evidence(
            _record(actual_outcome=100.0), committed_probability=-20.0
        )
        self.assertAlmostEqual(attrs[0]["brier"], 1.0, places=9)


class TestAggregation(unittest.TestCase):
    def test_brier_count_only_counts_gradeable_attributions(self):
        attributions = [
            {"domain": "a.com", "category": "_all", "correct": True, "brier": 0.04},
            {"domain": "a.com", "category": "_all", "correct": True, "brier": None},
            {"domain": "a.com", "category": "_all", "correct": False, "brier": 0.36},
        ]
        stats = compute_reliability_stats(attributions)[("a.com", "_all")]
        self.assertEqual(stats["sample_count"], 3)
        self.assertEqual(stats["brier_count"], 2)
        self.assertAlmostEqual(stats["brier_sum"], 0.40, places=6)
        self.assertAlmostEqual(compute_brier_skill(stats), 1 - 0.20, places=6)

    def test_brier_skill_is_none_when_nothing_was_gradeable(self):
        stats = compute_reliability_stats([
            {"domain": "a.com", "category": "_all", "correct": True, "brier": None},
        ])[("a.com", "_all")]
        self.assertEqual(stats["sample_count"], 1)
        self.assertEqual(stats["brier_count"], 0)
        self.assertIsNone(compute_brier_skill(stats))

    def test_hit_rate_and_brier_skill_disagree_on_the_same_sample(self):
        """If the two always agreed the metric switch would be decorative.

        Both attributions were directionally right, so the hit rate is 1.0 for
        each. One was called at 95%, the other at 51% -- Brier separates them.
        """
        confident = compute_reliability_stats([
            {"domain": "a.com", "category": "_all", "correct": True,
             "brier": brier_score(95.0, 100.0)},
        ])[("a.com", "_all")]
        lucky = compute_reliability_stats([
            {"domain": "b.com", "category": "_all", "correct": True,
             "brier": brier_score(51.0, 100.0)},
        ])[("b.com", "_all")]

        self.assertEqual(confident["correct_count"], lucky["correct_count"])
        skill_confident = compute_brier_skill(confident)
        skill_lucky = compute_brier_skill(lucky)
        assert skill_confident is not None and skill_lucky is not None
        self.assertGreater(skill_confident - skill_lucky, 0.2)


class TestShrunkBrierSkill(unittest.TestCase):
    def test_shrinks_toward_a_half_on_a_thin_sample(self):
        # One perfect sample with K=5: (1 - 0 + 2.5) / (1 + 5) = 0.5833
        self.assertAlmostEqual(
            _shrunk_brier_skill(brier_sum=0.0, brier_count=1, K=5), 3.5 / 6, places=6
        )
        # Twenty perfect samples barely shrink: (20 + 2.5) / 25 = 0.9
        self.assertAlmostEqual(
            _shrunk_brier_skill(brier_sum=0.0, brier_count=20, K=5), 0.9, places=6
        )

    def test_unusable_inputs_return_none_rather_than_a_default(self):
        for kwargs in (
            {"brier_sum": 0.0, "brier_count": 0, "K": 5},
            {"brier_sum": 0.0, "brier_count": None, "K": 5},
            {"brier_sum": 0.0, "brier_count": True, "K": 5},
            {"brier_sum": None, "brier_count": 4, "K": 5},
            {"brier_sum": "0.4", "brier_count": 4, "K": 5},
            {"brier_sum": 0.0, "brier_count": 4, "K": 0},
        ):
            with self.subTest(**kwargs):
                self.assertIsNone(_shrunk_brier_skill(**kwargs))

    def test_output_stays_inside_the_tier_score_band(self):
        """A corrupt sum must not push the prior outside 0-1.

        The prior is averaged against _TIER_SCORES (0.20-0.95); a negative or
        >1 value would not just be wrong, it would move overall_score outside
        the range every threshold is calibrated against.
        """
        for brier_sum in (-5.0, 0.0, 2.0, 99.0):
            with self.subTest(brier_sum=brier_sum):
                value = _shrunk_brier_skill(brier_sum=brier_sum, brier_count=2, K=5)
                assert value is not None
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_non_finite_sum_drops_the_prior_instead_of_clamping_to_perfect(self):
        """The clamp cannot handle NaN: min/max both fall through.

        max(0.0, min(nan, 2.0)) is 0.0, which would report a stored NaN as a
        flawless Brier of zero across the whole sample.
        """
        for brier_sum in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(brier_sum=brier_sum):
                self.assertIsNone(
                    _shrunk_brier_skill(brier_sum=brier_sum, brier_count=2, K=5)
                )

    def test_a_coin_flip_scores_higher_under_brier_than_under_hit_rate(self):
        """Documents why the switch cannot be silent.

        Five 50/50 calls that split 2-3: the hit rate says 0.4, Brier skill says
        0.75. Flipping the metric raises every stats-backed prior, so the flag
        defaults to hit_rate.
        """
        hit_rate = _shrunk_reliability(correct=2, sample=5, K=5)
        skill = _shrunk_brier_skill(brier_sum=5 * 0.25, brier_count=5, K=5)
        assert hit_rate is not None and skill is not None
        self.assertGreater(skill, hit_rate)


def _evidence(*domains: str):
    breakdown = [
        {"source": f"Src{i}", "direction": "support", "credibility": 0.5}
        for i, _ in enumerate(domains)
    ]
    items = [
        {"source": f"Src{i}", "url": f"https://{d}/a"}
        for i, d in enumerate(domains)
    ]
    return breakdown, items


class TestBuildSourceReliabilityMetric(unittest.TestCase):
    """The prior actually reaches overall_score, and the default is unchanged."""

    def _build(self, overrides, **kwargs):
        breakdown, items = _evidence("obscure-blog.example", "other-blog.example")
        return build_source_reliability(
            evidence_breakdown=breakdown,
            evidence_items=items,
            raw_direction="YES",
            enabled=True,
            score_threshold=0.5,
            min_trusted_ratio=0.0,
            min_domain_diversity=2,
            min_sources=1,
            domain_stats_overrides=overrides,
            domain_reliability_shrinkage_pseudocount=5,
            **kwargs,
        )

    @staticmethod
    def _row(domain: str):
        """A domain that went 2-of-5 on coin flips: bad hit rate, average Brier."""
        return {
            "domain": domain,
            "sample_count": 5, "correct_count": 2,
            "brier_sum": 5 * 0.25, "brier_count": 5,
        }

    def test_default_metric_is_byte_identical_to_omitting_the_argument(self):
        overrides = [self._row("obscure-blog.example")]
        implicit = self._build(overrides)
        explicit = self._build(overrides, domain_stats_prior_metric="hit_rate")
        self.assertEqual(implicit, explicit)

    def test_unrecognized_metric_reads_as_hit_rate(self):
        overrides = [self._row("obscure-blog.example")]
        self.assertEqual(
            self._build(overrides, domain_stats_prior_metric="brier-ish"),
            self._build(overrides, domain_stats_prior_metric="hit_rate"),
        )

    def test_brier_metric_moves_overall_score(self):
        """Not just a different label -- a different number reaching the score."""
        overrides = [self._row("obscure-blog.example")]
        hit = self._build(overrides, domain_stats_prior_metric="hit_rate")
        brier = self._build(overrides, domain_stats_prior_metric="brier")
        assert hit is not None and brier is not None
        self.assertTrue(hit["domain_stats_prior_affected"])
        self.assertTrue(brier["domain_stats_prior_affected"])
        self.assertGreater(brier["overall_score"], hit["overall_score"])

    def test_the_metric_used_is_recorded_on_the_block(self):
        overrides = [self._row("obscure-blog.example")]
        hit = self._build(overrides, domain_stats_prior_metric="hit_rate")
        brier = self._build(overrides, domain_stats_prior_metric="brier")
        assert hit is not None and brier is not None
        self.assertEqual(hit["domain_stats_prior_metric"], "hit_rate")
        self.assertEqual(brier["domain_stats_prior_metric"], "brier")

    def test_metric_key_absent_when_the_stats_prior_is_off(self):
        result = self._build(None, domain_stats_prior_metric="brier")
        assert result is not None
        self.assertNotIn("domain_stats_prior_metric", result)
        self.assertNotIn("domain_stats_prior_affected", result)

    def test_ungradeable_domain_gets_no_prior_under_brier(self):
        """Falling back to the hit rate here would make the recorded label a lie.

        The domain has 5 direction samples but nothing was ever frozen, so under
        the Brier metric it has no prior at all and drops to its tier default.
        """
        row = {"domain": "obscure-blog.example", "sample_count": 5,
               "correct_count": 5, "brier_sum": 0.0, "brier_count": 0}
        hit = self._build([row], domain_stats_prior_metric="hit_rate")
        brier = self._build([row], domain_stats_prior_metric="brier")
        assert hit is not None and brier is not None
        self.assertTrue(hit["domain_stats_prior_affected"])
        self.assertFalse(brier["domain_stats_prior_affected"])
        # 5-of-5 shrinks to 0.75, well above the 0.20 "unknown" tier score, so
        # dropping the prior must visibly lower the score.
        self.assertLess(brier["overall_score"], hit["overall_score"])

    def test_missing_brier_keys_are_treated_as_ungradeable(self):
        """A pre-Q3 override dict must not crash or invent a prior."""
        row = {"domain": "obscure-blog.example", "sample_count": 5,
               "correct_count": 3}
        result = self._build([row], domain_stats_prior_metric="brier")
        assert result is not None
        self.assertFalse(result["domain_stats_prior_affected"])


class _StoreMixin:
    """Isolate both databases: this store's file and the loop DB it reads."""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._tmpdir.name, "domain_reliability.db")
        self._loop_path = os.path.join(self._tmpdir.name, "v2_loop.db")
        self._patches = [
            patch.object(settings, "DOMAIN_RELIABILITY_DB_PATH", self._db_path),
            patch.object(sqlite_db, "loop_db_path", return_value=self._loop_path),
        ]
        for p in self._patches:
            p.start()
        from app.memory import domain_reliability_store as drs
        drs._INITIALIZED.discard(self._db_path)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()
        super().tearDown()

    @staticmethod
    def _freeze(record):
        from app.memory.prediction_store import freeze_prediction
        return freeze_prediction(record)


class TestStoreBrierColumns(_StoreMixin, unittest.TestCase):
    def test_frozen_estimate_is_scored_not_the_records_latest(self):
        """End-to-end: freeze at 80, rewrite the record to 99, then resolve.

        The stored Brier must be the one for 80. This is the store-level half of
        the "no fallback to latest" rule -- the pure function can be called
        correctly and the store still look up the wrong number.
        """
        from app.memory.domain_reliability_store import apply_resolution, get_stats

        self._freeze(_record("e1", estimated=80.0, resolved=False))

        resolved = _record("e1", estimated=99.0, actual_outcome=100.0)
        apply_resolution(resolved)

        row = next(s for s in get_stats(domain="reuters.com", category="_all"))
        self.assertEqual(row["brier_count"], 1)
        self.assertAlmostEqual(row["brier_sum"], brier_score(80.0, 100.0), places=6)
        self.assertAlmostEqual(row["brier_avg"], 0.04, places=6)
        self.assertAlmostEqual(row["brier_skill_score"], 0.96, places=6)

    def test_unfrozen_event_counts_for_direction_but_not_for_brier(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats

        apply_resolution(_record("never-frozen", actual_outcome=100.0))

        row = get_stats(domain="reuters.com", category="_all")[0]
        self.assertEqual(row["sample_count"], 1)
        self.assertEqual(row["correct_count"], 1)
        self.assertEqual(row["brier_count"], 0)
        self.assertIsNone(row["brier_avg"])
        self.assertIsNone(row["brier_skill_score"])

    def test_reapplying_the_same_event_does_not_double_count_the_brier(self):
        """The ledger guards sample_count; it must guard brier_sum too.

        A resolution hook that fires twice (re-resolve, backfill, retry) would
        otherwise report a domain's loss as twice what it was.
        """
        from app.memory.domain_reliability_store import apply_resolution, get_stats

        self._freeze(_record("e1", estimated=80.0, resolved=False))
        resolved = _record("e1", actual_outcome=100.0)

        apply_resolution(resolved)
        apply_resolution(resolved)
        apply_resolution(resolved)

        row = get_stats(domain="reuters.com", category="_all")[0]
        self.assertEqual(row["sample_count"], 1)
        self.assertEqual(row["brier_count"], 1)
        self.assertAlmostEqual(row["brier_sum"], 0.04, places=6)

    def test_committed_probability_is_read_once_per_record(self):
        """Three domains on one event must not mean three prediction lookups.

        attribute_evidence returns one attribution per (domain, category), so a
        lookup inside that loop would re-read the identical row per domain.
        """
        from app.memory import domain_reliability_store as drs

        record = _record("e1", actual_outcome=100.0)
        breakdown, items = _evidence("a.example", "b.example", "c.example")
        record["evidence_breakdown"] = breakdown
        record["evidence_items"] = items

        with patch.object(
            drs, "_committed_probability", wraps=drs._committed_probability
        ) as spy:
            drs.apply_resolution(record)

        self.assertEqual(spy.call_count, 1)
        # Guard against the assertion passing because nothing was written.
        self.assertEqual(len(drs.get_stats(category="_all")), 3)

    def test_rebuild_scores_the_frozen_estimates_too(self):
        from app.memory.domain_reliability_store import get_stats, rebuild_from_records

        self._freeze(_record("e1", estimated=80.0, resolved=False))
        self._freeze(_record("e2", estimated=30.0, resolved=False))

        rebuild_from_records([
            _record("e1", actual_outcome=100.0),
            _record("e2", actual_outcome=0.0),
        ])

        row = get_stats(domain="reuters.com", category="_all")[0]
        self.assertEqual(row["sample_count"], 2)
        self.assertEqual(row["brier_count"], 2)
        expected = brier_score(80.0, 100.0) + brier_score(30.0, 0.0)
        self.assertAlmostEqual(row["brier_sum"], expected, places=6)

    def test_lookup_failure_degrades_to_no_brier_instead_of_raising(self):
        """The loop DB is a different file; losing it must not lose the write."""
        from app.memory import domain_reliability_store as drs

        with patch(
            "app.memory.prediction_store.get_prediction",
            side_effect=OSError("loop db unavailable"),
        ):
            drs.apply_resolution(_record("e1", actual_outcome=100.0))

        row = drs.get_stats(domain="reuters.com", category="_all")[0]
        self.assertEqual(row["sample_count"], 1)
        self.assertEqual(row["brier_count"], 0)


class TestSchemaMigration(_StoreMixin, unittest.TestCase):
    def test_a_v1_database_gains_the_columns_and_reports_zero_graded(self):
        """An existing v1 row must read as "nothing gradeable yet", not as 0.0.

        A DEFAULT of 0.0 on brier_sum with brier_avg dividing by sample_count
        would have graded every pre-migration row as a perfect prediction.
        """
        from app.memory import domain_reliability_store as drs

        v1_schema = """
        CREATE TABLE domain_reliability (
            domain TEXT NOT NULL, category TEXT NOT NULL DEFAULT '_all',
            sample_count INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            wrong_count INTEGER NOT NULL DEFAULT 0,
            credibility_sum REAL NOT NULL DEFAULT 0.0,
            first_seen TEXT NOT NULL, last_updated TEXT NOT NULL,
            PRIMARY KEY (domain, category)
        );
        """
        with sqlite_db.writing(self._db_path) as conn:
            conn.executescript(v1_schema)
            conn.execute(
                "INSERT INTO domain_reliability (domain, category, sample_count, "
                "correct_count, wrong_count, credibility_sum, first_seen, "
                "last_updated) VALUES ('old.com', '_all', 8, 6, 2, 4.0, 'x', 'y')"
            )
        drs._INITIALIZED.discard(self._db_path)

        row = drs.get_stats(domain="old.com", category="_all")[0]
        self.assertEqual(row["sample_count"], 8)
        self.assertEqual(row["correct_count"], 6)
        self.assertEqual(row["brier_count"], 0)
        self.assertIsNone(row["brier_avg"])
        self.assertIsNone(row["brier_skill_score"])

    def test_a_migrated_database_accumulates_new_brier_samples(self):
        """Migration must leave the columns writable, not just present."""
        from app.memory import domain_reliability_store as drs

        self._freeze(_record("e1", estimated=80.0, resolved=False))
        drs.apply_resolution(_record("e1", actual_outcome=100.0))
        drs._INITIALIZED.discard(self._db_path)

        row = drs.get_stats(domain="reuters.com", category="_all")[0]
        self.assertEqual(row["brier_count"], 1)
        self.assertAlmostEqual(row["brier_avg"], 0.04, places=6)


if __name__ == "__main__":
    unittest.main()
