"""
Evidence scoring / extraction edge cases (Phase 4 item 5).

evidence_scoring_service.build_evidence_profile and the
evidence_extraction_service primitives it builds on had only indirect coverage
(through the news_filter_service characterization test). These lock their
edge-case behavior directly: empty input, a clean one-directional set, a
conflicting set, direction inference (including question negation), the
resolution-relevance scorer (base / entity bonus / clamp), and the averaging
helpers. All deterministic - no network or LLM.
"""

import unittest

from app.services.evidence_extraction_service import (
    infer_direction,
    score_resolution_relevance,
)
from app.services.evidence_scoring_service import (
    average_evidence_field,
    average_field,
    build_evidence_profile,
)

QUESTION = "Will the company get approval this year?"


def _article(title, description, source, *, quality=0.8, relevance=0.7,
             source_quality=0.9, age=0.8):
    return {
        "title": title,
        "description": description,
        "source": source,
        "quality_score": quality,
        "relevance_score": relevance,
        "source_quality": source_quality,
        "age_score": age,
    }


SUPPORT_ARTICLES = [
    _article("Company wins approval", "regulator approved and confirms launch",
             "reuters", quality=0.8, relevance=0.7, source_quality=0.9, age=0.9),
    _article("Stock surges to record high", "shares rise after the news",
             "bloomberg", quality=0.7, relevance=0.6, source_quality=0.8, age=0.8),
]
OPPOSE_ARTICLES = [
    _article("Plan fails after rejection", "regulator rejected and denied the request",
             "ap news", quality=0.8, relevance=0.7, source_quality=0.9, age=0.7),
    _article("Shares drop as deal delayed", "company misses target, deal delayed",
             "cnbc", quality=0.7, relevance=0.6, source_quality=0.8, age=0.6),
]


class BuildEvidenceProfileTests(unittest.TestCase):
    def test_empty_articles_returns_neutral_default_profile(self):
        self.assertEqual(
            build_evidence_profile(QUESTION, []),
            {
                "evidence_direction": "neutral",
                "evidence_strength": 0.0,
                "support_score": 0,
                "oppose_score": 0,
                "neutral_score": 0,
                "conflict_score": 0.0,
                "freshness_score": 0.0,
                "resolution_relevance_score": 0.0,
                "source_count": 0,
                "sources": [],
                "items": [],
            },
        )

    def test_one_directional_set_has_no_conflict_and_full_strength(self):
        """2 support articles, 0 oppose -> two-stage formula.

        direction_signal = 1.0 (all directional evidence agrees)
        evidence_volume  = min(1.0, 2/5.0) = 0.4 (2 of 5 max)
        strength         = 1.0 * 0.4 = 0.4

        Golden: 2 directional articles alone cannot reach strength=1.0;
        the volume cap reflects that 2 sources are less certain than 5.
        """
        profile = build_evidence_profile(QUESTION, SUPPORT_ARTICLES)
        self.assertEqual(profile["evidence_direction"], "support")
        self.assertEqual(profile["evidence_strength"], 0.4)
        self.assertEqual(profile["conflict_score"], 0.0)
        self.assertEqual(profile["oppose_score"], 0)
        self.assertEqual(profile["source_count"], 2)
        self.assertEqual(profile["sources"], ["bloomberg", "reuters"])

    def test_balanced_set_flags_conflict_and_neutral_direction(self):
        """2 support + 2 oppose -> two-stage formula.

        direction_signal = (0.441 - 0.341) / 0.782 ~ 0.128
        evidence_volume  = min(1.0, 4/5.0) = 0.8
        strength         = 0.128 * 0.8 ~ 0.102

        strength (0.102) < 0.15 threshold -> direction is neutral.
        Conflict is present but direction_signal too weak to commit.
        """
        profile = build_evidence_profile(QUESTION, SUPPORT_ARTICLES + OPPOSE_ARTICLES)
        self.assertEqual(profile["evidence_direction"], "neutral")
        self.assertEqual(profile["support_score"], 0.441)
        self.assertEqual(profile["oppose_score"], 0.341)
        self.assertEqual(profile["conflict_score"], 0.773)
        self.assertEqual(profile["evidence_strength"], 0.102)
        self.assertEqual(profile["source_count"], 4)

    def test_neutral_articles_do_not_dilute_direction_signal(self):
        """Neutral articles increase total but not direction_signal.

        Old formula: strength = |net| / total  (neutral dilutes)
        New formula: strength = |signal| * volume  (neutral excluded from both)

        2 support + 3 neutral:
          direction_signal = 0.441 / 0.441 = 1.0  (no oppose to dilute)
          evidence_volume  = min(1.0, 2/5.0) = 0.4  (only 2 directional)
          strength         = 1.0 * 0.4 = 0.4

        With the old formula: strength = 0.441 / (0.441 + neutral) < 0.441
        """
        neutral_articles = SUPPORT_ARTICLES + [
            _article("Market update", "trading continues normally",
                     "reuters", quality=0.5, relevance=0.3),
            _article("Daily report", "standard weekly summary released",
                     "bloomberg", quality=0.5, relevance=0.3),
            _article("Sector overview", "broad industry analysis piece",
                     "cnbc", quality=0.5, relevance=0.3),
        ]
        profile = build_evidence_profile(QUESTION, neutral_articles)
        self.assertEqual(profile["evidence_direction"], "support")
        # Strength is 0.4 despite 3 neutral articles: they don't dilute.
        self.assertEqual(profile["evidence_strength"], 0.4)


class InferDirectionTests(unittest.TestCase):
    def test_positive_text_supports_a_positive_question(self):
        self.assertEqual(
            infer_direction("will the company win?", "the company wins and confirms approval"),
            "support",
        )

    def test_negative_text_opposes_a_positive_question(self):
        self.assertEqual(
            infer_direction("will the company win?", "the company loses and fails"),
            "oppose",
        )

    def test_equal_hits_is_neutral(self):
        self.assertEqual(
            infer_direction("will the company win?", "the weather is mild today"),
            "neutral",
        )

    def test_question_negation_flips_direction(self):
        # A "not win" question inverts: positive text now opposes the YES outcome.
        self.assertEqual(
            infer_direction("will the company not win?", "the company wins and surges"),
            "oppose",
        )


class ResolutionRelevanceTests(unittest.TestCase):
    def test_base_score_with_empty_semantics(self):
        self.assertEqual(score_resolution_relevance("", {}), 0.25)

    def test_entity_hits_add_capped_bonus(self):
        self.assertEqual(
            score_resolution_relevance(
                "acme and the fed met", {"entities": ["acme", "fed"]}
            ),
            0.49,
        )

    def test_score_is_clamped_to_one(self):
        self.assertEqual(
            score_resolution_relevance(
                "acme fed 100 june hit record high price",
                {
                    "entities": ["acme", "fed"],
                    "threshold": "100",
                    "deadline": "june",
                    "condition_type": "threshold",
                },
            ),
            1.0,
        )


class AveragingHelperTests(unittest.TestCase):
    def test_average_field_empty_is_zero(self):
        self.assertEqual(average_field([], "age_score"), 0.0)

    def test_average_field_rounds_to_three(self):
        self.assertEqual(
            average_field([{"age_score": 0.2}, {"age_score": 0.4}], "age_score"), 0.3
        )

    def test_average_evidence_field_empty_is_zero(self):
        self.assertEqual(average_evidence_field([], "resolution_relevance_score"), 0.0)

    def test_average_evidence_field_rounds_to_three(self):
        self.assertEqual(
            average_evidence_field([{"s": 0.1}, {"s": 0.2}], "s"), 0.15
        )


if __name__ == "__main__":
    unittest.main()
