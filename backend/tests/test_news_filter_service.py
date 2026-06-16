import unittest

from app.services.news_filter_service import filter_news_for_market


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


if __name__ == "__main__":
    unittest.main()
