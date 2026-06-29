"""Tests for evidence_aggregation_service.aggregate_evidence_breakdown.

Pure-function tests (no LLM, no IO). Verifies filtering, normalization,
禁词 replacement, index join, and ordering.
"""
import unittest

from app.services.evidence_aggregation_service import aggregate_evidence_breakdown


class AggregateEvidenceBreakdownTests(unittest.TestCase):
    def test_empty_inputs_return_empty_list(self):
        self.assertEqual(aggregate_evidence_breakdown([], []), [])
        self.assertEqual(aggregate_evidence_breakdown(None, None), [])
        self.assertEqual(aggregate_evidence_breakdown([], None), [])
        self.assertEqual(aggregate_evidence_breakdown(None, []), [])

    def test_neutral_direction_filtered_out(self):
        sentiment = [{"index": 0, "evidence_direction": "neutral", "evidence_strength": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_missing_direction_filtered_out(self):
        sentiment = [{"index": 0, "evidence_strength": 0.9}]  # no direction key
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_unknown_direction_filtered_out(self):
        sentiment = [{"index": 0, "evidence_direction": "maybe", "evidence_strength": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_strength_below_threshold_filtered_out(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.19}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_strength_at_threshold_kept(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.2,
                      "source_credibility": 0.8, "rationale_zh": "原因"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["strength"], 0.2)

    def test_support_article_produces_breakdown_item(self):
        sentiment = [{
            "index": 0,
            "evidence_direction": "support",
            "evidence_strength": 0.8,
            "source_credibility": 0.9,
            "rationale_zh": "直接支持 YES 的事实。",
        }]
        original = [{"source": "Reuters", "title": "Fed signals rate cut"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 1)
        item = out[0]
        self.assertEqual(item["source"], "Reuters")
        self.assertEqual(item["title"], "Fed signals rate cut")
        self.assertEqual(item["direction"], "support")
        self.assertEqual(item["strength"], 0.8)
        self.assertEqual(item["credibility"], 0.9)
        self.assertEqual(item["rationale_zh"], "直接支持 YES 的事实。")

    def test_oppose_article_produces_breakdown_item(self):
        sentiment = [{
            "index": 0,
            "evidence_direction": "oppose",
            "evidence_strength": 0.7,
            "source_credibility": 0.6,
            "rationale_zh": "反对 YES。",
        }]
        original = [{"source": "AP", "title": "Bill stalled"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["direction"], "oppose")

    def test_index_missing_skipped(self):
        sentiment = [{"evidence_direction": "support", "evidence_strength": 0.8}]  # no index
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_non_integer_skipped(self):
        sentiment = [{"index": 1.5, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_bool_skipped(self):
        # bool is a subclass of int in Python; must be rejected explicitly.
        sentiment = [{"index": True, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_negative_skipped(self):
        sentiment = [{"index": -1, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_out_of_range_skipped(self):
        sentiment = [{"index": 5, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]  # only index 0 exists
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_original_article_missing_title_skipped(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters"}]  # no title
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_original_article_empty_title_skipped(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "   "}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_strength_clamped_to_range(self):
        sentiment = [{"index": 0, "evidence_direction": "support",
                      "evidence_strength": 1.5, "source_credibility": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["strength"], 1.0)

    def test_strength_negative_clamped_to_zero(self):
        sentiment = [{"index": 0, "evidence_direction": "support",
                      "evidence_strength": -0.5, "source_credibility": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        # -0.5 clamps to 0.0, which is below 0.2 threshold, so filtered out
        self.assertEqual(out, [])

    def test_credibility_clamped_to_range(self):
        sentiment = [{"index": 0, "evidence_direction": "support",
                      "evidence_strength": 0.8, "source_credibility": 1.7}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["credibility"], 1.0)

    def test_credibility_missing_defaults_to_half(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["credibility"], 0.5)

    def test_source_missing_defaults_to_unknown(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"title": "T"}]  # no source
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["source"], "unknown")

    def test_title_truncated_to_200_chars(self):
        long_title = "A" * 500
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": long_title}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out[0]["title"]), 200)

    def test_rationale_truncated_to_300_chars(self):
        long_rationale = "原因" * 200  # 400 chars
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": long_rationale}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out[0]["rationale_zh"]), 300)

    def test_rationale_missing_becomes_empty_string(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["rationale_zh"], "")

    def test_banned_word_long_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "这是 long 信号"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("long", out[0]["rationale_zh"].lower())
        self.assertIn("支持 YES", out[0]["rationale_zh"])

    def test_banned_word_short_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "这是 short 信号"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("short", out[0]["rationale_zh"].lower())
        self.assertIn("支持 NO", out[0]["rationale_zh"])

    def test_banned_word_case_insensitive_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "这是 LONG 信号"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("long", out[0]["rationale_zh"].lower())

    def test_banned_word_buy_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "建议 buy"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("buy", out[0]["rationale_zh"].lower())
        self.assertIn("支持 YES", out[0]["rationale_zh"])

    def test_banned_word_position_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "调整 position"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("position", out[0]["rationale_zh"].lower())
        self.assertIn("配置", out[0]["rationale_zh"])

    def test_banned_word_kelly_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "kelly 公式"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("kelly", out[0]["rationale_zh"].lower())
        self.assertIn("风险预算", out[0]["rationale_zh"])

    def test_banned_word_order_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "提交 order"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("order", out[0]["rationale_zh"].lower())
        self.assertIn("决策", out[0]["rationale_zh"])

    def test_order_preserved_from_sentiment_articles(self):
        sentiment = [
            {"index": 0, "evidence_direction": "support", "evidence_strength": 0.7,
             "source_credibility": 0.8, "rationale_zh": "第一条"},
            {"index": 1, "evidence_direction": "oppose", "evidence_strength": 0.6,
             "source_credibility": 0.7, "rationale_zh": "第二条"},
            {"index": 2, "evidence_direction": "support", "evidence_strength": 0.5,
             "source_credibility": 0.6, "rationale_zh": "第三条"},
        ]
        original = [
            {"source": "Reuters", "title": "A"},
            {"source": "AP", "title": "B"},
            {"source": "Bloomberg", "title": "C"},
        ]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["title"], "A")
        self.assertEqual(out[1]["title"], "B")
        self.assertEqual(out[2]["title"], "C")

    def test_mixed_valid_invalid_items_only_valid_returned(self):
        sentiment = [
            {"index": 0, "evidence_direction": "support", "evidence_strength": 0.8},  # valid
            {"index": 1, "evidence_direction": "neutral", "evidence_strength": 0.9},  # filtered
            {"index": 99, "evidence_direction": "support", "evidence_strength": 0.8},  # out of range
            {"index": 2, "evidence_direction": "support", "evidence_strength": 0.8},  # valid
        ]
        original = [
            {"source": "Reuters", "title": "A"},
            {"source": "AP", "title": "B"},
            {"source": "Bloomberg", "title": "C"},
        ]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "A")
        self.assertEqual(out[1]["title"], "C")

    def test_non_dict_sentiment_article_skipped(self):
        sentiment = ["not a dict", None, 42]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_rationale_non_string_coerced_to_string(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": 12345}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["rationale_zh"], "12345")


if __name__ == "__main__":
    unittest.main()
