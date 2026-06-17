import unittest

from app.services.market_semantics_service import parse_market_semantics
from app.services.news_filter_service import (
    filter_news_for_market,
    relevance_score,
    score_article,
)


class NewsFilterServiceContractTests(unittest.TestCase):
    """Characterization tests that lock the filter_news_for_market contract.

    This is the public interface consumed by discover_events / analyze_event
    (context, summary.selected_count) and several legacy routes. The Phase 3
    split of news_filter_service must preserve this exactly.
    """

    def _articles(self):
        return [
            {
                "title": "Bitcoin surges toward $100k as ETF inflows hit record",
                "description": "Bitcoin rallied as spot ETF demand reached a "
                               "record, pushing price near 100k.",
                "source": "Reuters",
                "published": "",
            },
            {
                "title": "Hoax",
                "description": "",
                "source": "Random Blog",
                "published": "",
            },
        ]

    def test_contract_shape_and_filtering(self):
        result = filter_news_for_market(
            "Will Bitcoin reach $100k in 2026?", self._articles()
        )
        # Top-level contract consumed downstream.
        self.assertEqual(
            set(result.keys()),
            {"articles", "context", "evidence_profile", "market_semantics", "summary"},
        )
        self.assertEqual(
            set(result["summary"].keys()),
            {
                "input_count", "selected_count", "rejected_count",
                "average_quality", "evidence_strength", "evidence_direction",
                "conflict_score", "freshness_score", "rejected",
            },
        )
        self.assertEqual(
            set(result["evidence_profile"].keys()),
            {
                "evidence_direction", "evidence_strength", "support_score",
                "oppose_score", "neutral_score", "conflict_score",
                "freshness_score", "resolution_relevance_score",
                "source_count", "sources", "items",
            },
        )
        # Deterministic filtering: trusted + relevant kept, low-quality short
        # item rejected. (published="" -> fixed age score, so not time-sensitive.)
        self.assertEqual(result["summary"]["input_count"], 2)
        self.assertEqual(result["summary"]["selected_count"], 1)
        self.assertEqual(result["summary"]["rejected_count"], 1)
        self.assertEqual(
            result["articles"][0]["title"],
            "Bitcoin surges toward $100k as ETF inflows hit record",
        )
        # Context string carries the evidence + news headers for the LLM.
        self.assertIsInstance(result["context"], str)
        self.assertIn("EVIDENCE PROFILE", result["context"])
        self.assertIn("NEWS ITEM", result["context"])

    def test_empty_articles(self):
        result = filter_news_for_market("Will Bitcoin reach $100k in 2026?", [])
        self.assertEqual(result["summary"]["input_count"], 0)
        self.assertEqual(result["summary"]["selected_count"], 0)
        self.assertEqual(result["evidence_profile"]["evidence_direction"], "neutral")
        self.assertEqual(result["evidence_profile"]["evidence_strength"], 0.0)


def _blend_article(title, description, semantic=None):
    article = {
        "title": title,
        "description": description,
        "source_quality": 0.5,
        "age_score": 0.5,
    }
    if semantic is not None:
        article["semantic_relevance"] = semantic
    return article


class RelevanceBlendTests(unittest.TestCase):
    """score_article blends semantic_relevance (when present) with keyword
    relevance via max-merge; keyword-only when it is absent (unchanged)."""

    QUESTION = "Will the Federal Reserve cut interest rates?"

    def test_semantic_rescues_low_keyword_score(self):
        # No shared vocabulary -> keyword relevance ~0; semantic 0.9 wins.
        article = _blend_article(
            "Bananas ripen faster in tropical summers",
            "Logistics of fruit shipping",
            semantic=0.9,
        )
        score_article(self.QUESTION, article)
        self.assertEqual(article["relevance_score"], 0.9)

    def test_keyword_only_when_semantic_absent(self):
        article = _blend_article(
            "Federal Reserve signals an interest rate decision", "rates"
        )
        score_article(self.QUESTION, article)
        self.assertGreater(article["relevance_score"], 0.0)


class RelevanceSemanticsTokenTests(unittest.TestCase):
    """relevance_score must use only the event's concrete entities from
    semantics, not the yes/no condition templates. Those templates are generic
    boilerplate ("the referenced metric reaches or exceeds ... does not ...")
    whose filler words match unrelated news and inflate relevance. Regression
    for the template-pollution fix.
    """

    QUESTION = "Will Bitcoin reach $100,000 by end of 2026?"

    def test_template_boilerplate_words_do_not_match(self):
        semantics = parse_market_semantics(self.QUESTION)
        # Made only of yes/no-condition boilerplate words that are NOT in the
        # question. With the templates excluded these are not matching tokens.
        boilerplate = "the referenced metric exceeds its target and the event occurs"
        self.assertEqual(relevance_score(self.QUESTION, boilerplate, semantics), 0.0)

    def test_entity_article_still_matches(self):
        semantics = parse_market_semantics(self.QUESTION)
        self.assertGreater(
            relevance_score(self.QUESTION, "bitcoin rallies toward a new high", semantics),
            0.0,
        )

    def test_real_crypto_news_outranks_unrelated_politics(self):
        """The motivating regression: an unrelated politics headline must not
        score as high as real Bitcoin news on a Bitcoin question."""
        semantics = parse_market_semantics(self.QUESTION)
        politics = relevance_score(
            self.QUESTION, "federal judge blocks state ban enforcement", semantics
        )
        crypto = relevance_score(
            self.QUESTION, "morgan stanley says bitcoin reaching new highs", semantics
        )
        self.assertGreater(crypto, politics)


class RelevanceWordBoundaryTests(unittest.TestCase):
    """relevance_score must match question tokens on word boundaries, not as
    bare substrings. Regression for the substring false-positive that let a
    short token like "eth" match inside "hegseth" / "whether" (and "end"
    inside "transgender"), inflating relevance on unrelated news.
    """

    def test_short_token_does_not_match_inside_a_longer_word(self):
        # "eth" must NOT match inside "hegseth". No other crypto token present
        # -> relevance is 0. (Question chosen so its only matching token is eth.)
        self.assertEqual(
            relevance_score("Will ETH rally?", "hegseth may leave the cabinet", None),
            0.0,
        )
        self.assertEqual(
            relevance_score("Will ETH rally?", "whether the bill passes", None),
            0.0,
        )

    def test_token_still_matches_as_a_whole_word(self):
        # Word-boundary does not break legitimate matches: "eth" matches " eth ".
        self.assertGreater(
            relevance_score("Will ETH rally?", "eth breaks out to a new high", None),
            0.0,
        )
        self.assertGreater(
            relevance_score("Will Bitcoin reach $100k?", "bitcoin nears all-time high", None),
            0.0,
        )

    def test_non_alphanumeric_token_falls_back_to_substring(self):
        # A token like "$100k" starts with a non-word char, so there is no word
        # boundary before it; it falls back to a substring match and still hits.
        self.assertGreater(
            relevance_score("Will Bitcoin reach $100k?", "price eyes $100k milestone", None),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
