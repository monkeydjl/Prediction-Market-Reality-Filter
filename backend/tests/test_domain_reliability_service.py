"""Unit tests for domain_reliability_service (LATER #2)."""
import unittest
from typing import Any

from app.services.domain_reliability_service import (
    attribute_evidence,
    compute_reliability_score,
    compute_reliability_stats,
    extract_domain,
)


class TestExtractDomain(unittest.TestCase):
    def test_normal_url(self):
        self.assertEqual(extract_domain("https://www.reuters.com/article/123"), "reuters.com")

    def test_no_www(self):
        self.assertEqual(extract_domain("https://reuters.com/path?q=1"), "reuters.com")

    def test_uppercase(self):
        self.assertEqual(extract_domain("https://WWW.Reuters.COM/"), "reuters.com")

    def test_invalid_url(self):
        self.assertIsNone(extract_domain("not a url"))

    def test_missing_url(self):
        self.assertIsNone(extract_domain(""))

    def test_no_scheme(self):
        self.assertEqual(extract_domain("reuters.com/path"), "reuters.com")


def _record(
    event_id: str = "e1",
    direction: str = "YES",
    actual_outcome: float = 100.0,
    outcome_status: str = "resolved",
    source_type: str = "prediction_market",
    evidence_breakdown: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        "outcome": {"status": outcome_status, "actual_outcome": actual_outcome},
        "evidence_breakdown": evidence_breakdown,
        "evidence_items": evidence_items,
    }


class TestAttributeEvidence(unittest.TestCase):
    def test_yes_direction_correct_support(self):
        result = attribute_evidence(_record(direction="YES", actual_outcome=100.0))
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["correct"])
        self.assertEqual(result[0]["stance"], "supports")

    def test_yes_direction_wrong_support(self):
        result = attribute_evidence(_record(direction="YES", actual_outcome=0.0))
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["correct"])

    def test_no_direction_correct_support(self):
        result = attribute_evidence(_record(direction="NO", actual_outcome=0.0))
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["correct"])

    def test_no_direction_wrong_support(self):
        result = attribute_evidence(_record(direction="NO", actual_outcome=100.0))
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["correct"])

    def test_oppose_flips_correctness(self):
        """oppose + recommendation correct -> source wrong."""
        rec = _record(
            direction="YES", actual_outcome=100.0,
            evidence_breakdown=[{"source": "Reuters", "direction": "oppose", "credibility": 0.8}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["correct"])

    def test_supports_alias_uses_breakdown_url(self):
        """Spec input shape: evidence_breakdown carries url + supports."""
        rec = _record(
            direction="YES",
            actual_outcome=100.0,
            evidence_breakdown=[
                {
                    "source": "Reuters",
                    "url": "https://www.reuters.com/a",
                    "direction": "supports",
                    "credibility": 0.8,
                },
            ],
            evidence_items=[],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["domain"], "reuters.com")
        self.assertEqual(result[0]["stance"], "supports")
        self.assertTrue(result[0]["correct"])

    def test_refutes_alias_flips_correctness(self):
        """Spec input shape: refutes means source supports the opposite direction."""
        rec = _record(
            direction="YES",
            actual_outcome=100.0,
            evidence_breakdown=[
                {
                    "source": "Reuters",
                    "url": "https://www.reuters.com/a",
                    "direction": "refutes",
                    "credibility": 0.8,
                },
            ],
            evidence_items=[],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["stance"], "refutes")
        self.assertFalse(result[0]["correct"])

    def test_wait_direction_skipped(self):
        result = attribute_evidence(_record(direction="WAIT"))
        self.assertEqual(result, [])

    def test_avoid_direction_skipped(self):
        result = attribute_evidence(_record(direction="AVOID"))
        self.assertEqual(result, [])

    def test_unresolved_skipped(self):
        result = attribute_evidence(_record(outcome_status="pending"))
        self.assertEqual(result, [])

    def test_none_actual_outcome_skipped(self):
        rec = _record(actual_outcome=None)  # type: ignore
        # Build a record where actual_outcome is None
        rec["outcome"]["actual_outcome"] = None
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_negative_outcome_skipped(self):
        rec = _record(actual_outcome=-1)
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_neutral_evidence_skipped(self):
        rec = _record(
            evidence_breakdown=[{"source": "Reuters", "direction": "neutral", "credibility": 0.8}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_missing_url_skipped(self):
        rec = _record(
            evidence_items=[{"source": "Reuters", "url": ""}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_mixed_support_oppose_skipped(self):
        """Same domain has both support and oppose -> mixed, skip."""
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8},
                {"source": "Reuters", "direction": "oppose", "credibility": 0.6},
            ],
            evidence_items=[
                {"source": "Reuters", "url": "https://www.reuters.com/a"},
                {"source": "Reuters", "url": "https://www.reuters.com/b"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_same_domain_multiple_support(self):
        """Same domain, two support items -> merged into one attribution."""
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8},
                {"source": "Reuters", "direction": "support", "credibility": 0.6},
            ],
            evidence_items=[
                {"source": "Reuters", "url": "https://www.reuters.com/a"},
                {"source": "Reuters", "url": "https://www.reuters.com/b"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["credibility"], 0.7)

    def test_credibility_clipped(self):
        rec = _record(
            evidence_breakdown=[{"source": "Reuters", "direction": "support", "credibility": 1.5}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["credibility"], 1.0)

    def test_credibility_missing(self):
        rec = _record(
            evidence_breakdown=[{"source": "Reuters", "direction": "support"}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["credibility"])

    def test_category_from_evidence_source_type(self):
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8,
                 "source_type": "open_web"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "open_web")

    def test_category_from_record_source_type(self):
        rec = _record(source_type="prediction_question")
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "prediction_question")

    def test_category_from_top_level_record_source_type(self):
        rec = _record(source_type="")
        rec["source"] = {}
        rec["source_type"] = "prediction_market"
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "prediction_market")

    def test_category_unknown_fallback(self):
        rec = _record(
            source_type="",
            evidence_breakdown=[{"source": "X", "direction": "support", "credibility": 0.5}],
            evidence_items=[{"source": "X", "url": "https://x.com/a"}],
        )
        rec["source"] = {}
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "_unknown")

    def test_unknown_is_not_all(self):
        rec = _record(
            source_type="",
            evidence_breakdown=[{"source": "X", "direction": "support", "credibility": 0.5}],
            evidence_items=[{"source": "X", "url": "https://x.com/a"}],
        )
        rec["source"] = {}
        result = attribute_evidence(rec)
        self.assertNotEqual(result[0]["category"], "_all")

    def test_all_category_is_reserved_for_synthetic_rollup(self):
        rec = _record(
            evidence_breakdown=[
                {
                    "source": "X",
                    "direction": "support",
                    "credibility": 0.5,
                    "source_type": "_all",
                    "url": "https://x.com/a",
                },
            ],
            evidence_items=[],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "_unknown")

    def test_two_domains_two_attributions(self):
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8},
                {"source": "BBC", "direction": "support", "credibility": 0.7},
            ],
            evidence_items=[
                {"source": "Reuters", "url": "https://www.reuters.com/a"},
                {"source": "BBC", "url": "https://www.bbc.co.uk/b"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 2)
        domains = {r["domain"] for r in result}
        self.assertIn("reuters.com", domains)
        self.assertIn("bbc.co.uk", domains)


class TestComputeReliabilityStats(unittest.TestCase):
    def test_basic_aggregation(self):
        attributions = [
            {"event_id": "e1", "domain": "reuters.com", "category": "prediction_market",
             "stance": "support", "credibility": 0.8, "correct": True},
            {"event_id": "e2", "domain": "reuters.com", "category": "prediction_market",
             "stance": "support", "credibility": 0.6, "correct": False},
            {"event_id": "e3", "domain": "bbc.co.uk", "category": "prediction_market",
             "stance": "support", "credibility": 0.9, "correct": True},
        ]
        stats = compute_reliability_stats(attributions)
        self.assertEqual(len(stats), 2)
        self.assertIn(("reuters.com", "prediction_market"), stats)
        self.assertIn(("bbc.co.uk", "prediction_market"), stats)

    def test_correct_plus_wrong_equals_sample(self):
        attributions = [
            {"event_id": "e1", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.8, "correct": True},
            {"event_id": "e2", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.6, "correct": False},
            {"event_id": "e3", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.7, "correct": True},
        ]
        stats = compute_reliability_stats(attributions)
        s = stats[("a.com", "pm")]
        self.assertEqual(s["sample_count"], 3)
        self.assertEqual(s["correct_count"], 2)
        self.assertEqual(s["wrong_count"], 1)
        self.assertEqual(s["correct_count"] + s["wrong_count"], s["sample_count"])

    def test_credibility_sum(self):
        attributions = [
            {"event_id": "e1", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.8, "correct": True},
            {"event_id": "e2", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.6, "correct": False},
        ]
        stats = compute_reliability_stats(attributions)
        self.assertAlmostEqual(stats[("a.com", "pm")]["credibility_sum"], 1.4)

    def test_empty_input(self):
        self.assertEqual(compute_reliability_stats([]), {})


class TestComputeReliabilityScore(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(
            compute_reliability_score({"correct_count": 12, "sample_count": 18}),
            0.667, places=2,
        )

    def test_zero_sample(self):
        self.assertIsNone(compute_reliability_score({"correct_count": 0, "sample_count": 0}))

    def test_all_correct(self):
        self.assertEqual(
            compute_reliability_score({"correct_count": 5, "sample_count": 5}),
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
