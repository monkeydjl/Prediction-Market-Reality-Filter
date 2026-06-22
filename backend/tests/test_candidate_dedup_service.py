"""Tests for cross-source candidate dedup (candidate_dedup_service).

The dedup drops candidates whose question is similar to an already-accepted
candidate's, keeping the higher-priority source (market platforms before curated
sports before Open Web). Similarity is token-set Jaccard with a tiered threshold:
0.82 for structured-vs-structured, 0.6 when either side is Open Web.

To make the Jaccard values deterministic and readable, most cases use
controlled token-set questions (e.g. "alpha beta gamma delta epsilon zeta") so
the overlap is exact, rather than relying on natural-language phrasing whose
token overlap is hard to predict.
"""

import unittest

from app.services.candidate_dedup_service import (
    CROSS_THRESHOLD,
    MARKET_THRESHOLD,
    dedupe_candidates,
)


def _candidate(question, platform, source_type="prediction_market",
               baseline=50.0):
    """Build a minimal candidate dict with the shape dedupe reads."""
    return {
        "question": question,
        "baseline_probability": baseline,
        "source": {"type": source_type, "platform": platform},
    }


# Token sets chosen so overlaps are exact and straddle the thresholds.
# 6 tokens, 5 shared, 1 unique each side -> 5/7 ~ 0.714 (>= 0.6, < 0.82).
_NEAR_DUPE = "alpha beta gamma delta epsilon 2026"
_NEAR_DUPE_OTHER_YEAR = "alpha beta gamma delta epsilon 2027"


class InputOrderTests(unittest.TestCase):
    """Dedup preserves the caller's input order (round-robin) for non-duplicate
    candidates; it only swaps in a higher-priority source when a duplicate is
    found. It does NOT reorder unique candidates by priority."""

    def test_unique_candidates_keep_input_order(self):
        candidates = [
            _candidate("q openweb", "Open Web", source_type="open_web"),
            _candidate("q kalshi", "Kalshi"),
            _candidate("q manifold", "Manifold"),
            _candidate("q polymarket", "Polymarket"),
        ]
        result = dedupe_candidates(candidates)
        platforms = [c["source"]["platform"] for c in result]
        # Input order preserved - no priority reordering of unique candidates.
        self.assertEqual(platforms, ["Open Web", "Kalshi", "Manifold", "Polymarket"])

    def test_replacement_keeps_round_robin_position(self):
        # Open Web arrives first (round-robin slot 0), Polymarket later with a
        # duplicate question. Polymarket wins the duplicate AND keeps Open Web's
        # slot position (does not jump to the front).
        candidates = [
            _candidate(_NEAR_DUPE_OTHER_YEAR, "Open Web", source_type="open_web"),
            _candidate("a totally different question", "Kalshi"),
            _candidate(_NEAR_DUPE, "Polymarket"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 2)
        # Polymarket replaced Open Web in slot 0; Kalshi stayed in slot 1.
        self.assertEqual(result[0]["source"]["platform"], "Polymarket")
        self.assertEqual(result[1]["source"]["platform"], "Kalshi")


class ExactDuplicateTests(unittest.TestCase):
    def test_same_question_same_source_kept_once(self):
        candidates = [
            _candidate("Will Bitcoin reach 100k?", "Polymarket"),
            _candidate("Will Bitcoin reach 100k?", "Polymarket"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["platform"], "Polymarket")

    def test_same_question_cross_source_keeps_higher_priority(self):
        # Same question from Polymarket and Manifold -> keep Polymarket.
        candidates = [
            _candidate("Will Bitcoin reach 100k?", "Manifold"),
            _candidate("Will Bitcoin reach 100k?", "Polymarket"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["platform"], "Polymarket")


class MarketVsMarketThresholdTests(unittest.TestCase):
    def test_high_overlap_market_questions_deduped(self):
        # 5/7 ~ 0.714 < 0.82, so these are NOT duplicates at market threshold.
        candidates = [
            _candidate(_NEAR_DUPE, "Polymarket"),
            _candidate(_NEAR_DUPE_OTHER_YEAR, "Manifold"),
        ]
        result = dedupe_candidates(candidates)
        # Below market threshold -> both kept.
        self.assertEqual(len(result), 2)

    def test_above_market_threshold_deduped(self):
        # 6 shared tokens, 1 unique on one side -> 6/7 ~ 0.857 >= 0.82.
        base = "alpha beta gamma delta epsilon zeta"
        near = "alpha beta gamma delta epsilon zeta extra"  # adds one token
        # Actually: base has 6, near has 7, intersection 6, union 7 -> 0.857.
        candidates = [
            _candidate(base, "Polymarket"),
            _candidate(near, "Manifold"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["platform"], "Polymarket")


class OpenWebThresholdTests(unittest.TestCase):
    def test_openweb_duplicate_of_market_deduped_at_lower_threshold(self):
        # Same 5/7 ~ 0.714 overlap, but one side is Open Web -> 0.6 threshold
        # applies, so 0.714 >= 0.6 -> duplicate. Market kept.
        candidates = [
            _candidate(_NEAR_DUPE, "Polymarket"),
            _candidate(_NEAR_DUPE_OTHER_YEAR, "Open Web", source_type="open_web"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["platform"], "Polymarket")

    def test_openweb_below_cross_threshold_kept(self):
        # 6 tokens, 3 shared, 3 unique each -> 3/9 ~ 0.33 < 0.6 -> not dup.
        market_q = "alpha beta gamma one two three"
        openweb_q = "alpha beta gamma four five six"
        candidates = [
            _candidate(market_q, "Polymarket"),
            _candidate(openweb_q, "Open Web", source_type="open_web"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 2)

    def test_openweb_arriving_first_still_loses_to_market(self):
        # Open Web is first in input but lower priority; Polymarket still kept.
        candidates = [
            _candidate(_NEAR_DUPE_OTHER_YEAR, "Open Web", source_type="open_web"),
            _candidate(_NEAR_DUPE, "Polymarket"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["platform"], "Polymarket")


class SportsEventThresholdTests(unittest.TestCase):
    def test_sports_events_use_strict_structured_threshold(self):
        # Same 5/7 ~ 0.714 overlap as the Open Web test. Because neither side is
        # Open Web, the stricter 0.82 structured threshold applies and both
        # curated sports questions are kept.
        candidates = [
            _candidate(_NEAR_DUPE, "World Cup", source_type="sports_event"),
            _candidate(_NEAR_DUPE_OTHER_YEAR, "World Cup", source_type="sports_event"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 2)

    def test_sports_event_outranks_openweb_duplicate(self):
        candidates = [
            _candidate(_NEAR_DUPE_OTHER_YEAR, "Open Web", source_type="open_web"),
            _candidate(_NEAR_DUPE, "World Cup", source_type="sports_event"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["type"], "sports_event")


class BoundaryTests(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(dedupe_candidates([]), [])

    def test_all_unique_kept_all(self):
        candidates = [
            _candidate("Will Bitcoin reach 100k?", "Polymarket"),
            _candidate("Will the Fed raise rates?", "Manifold"),
            _candidate("Will the court ruling pass?", "Open Web", source_type="open_web"),
        ]
        result = dedupe_candidates(candidates)
        self.assertEqual(len(result), 3)


class ThresholdConstantsTests(unittest.TestCase):
    """Lock the tiered threshold constants so the behavior is self-documenting."""

    def test_market_threshold_stricter_than_cross(self):
        self.assertGreater(MARKET_THRESHOLD, CROSS_THRESHOLD)
        self.assertEqual(MARKET_THRESHOLD, 0.82)
        self.assertEqual(CROSS_THRESHOLD, 0.6)


if __name__ == "__main__":
    unittest.main()
