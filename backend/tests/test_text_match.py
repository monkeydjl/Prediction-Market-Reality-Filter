"""Tests for the shared text matching utilities (app/utils/text_match).

These lock the behavior extracted verbatim from auto_resolve_service. The
market-layer auto_resolve_service now imports these helpers, and the event
layer's auto-resolve uses them too, so this is the regression net for both.
"""

import unittest

from app.utils.text_match import (
    FUZZY_THRESHOLD,
    build_index,
    find_match,
    normalize,
    token_overlap,
    tokenize,
    word_in_text,
)


class NormalizeTests(unittest.TestCase):
    def test_lowercases_and_collapses_whitespace(self):
        self.assertEqual(normalize("  Will   BTC Reach  "), "will btc reach")

    def test_empty_and_none_are_safe(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(None), "")


class TokenizeTests(unittest.TestCase):
    def test_min_two_chars_and_stopwords_dropped(self):
        # "be" is a stopword; "a" is 1 char; "x" is 1 char.
        self.assertEqual(tokenize("Will a be x bit"), ["bit"])

    def test_alphanumeric_only(self):
        self.assertEqual(tokenize("BTC-100k by 2030?!"), ["btc", "100k", "2030"])

    def test_empty(self):
        self.assertEqual(tokenize(""), [])

    def test_ascii_input_unchanged_by_unicode_support(self):
        """Unicode tokenization must not change ASCII-only behavior (the
        locked matching values depend on this). Stopwords (will/the/in) drop."""
        self.assertEqual(
            tokenize("Will the Fed raise rates in June?"),
            ["fed", "raise", "rates", "june"],
        )

    def test_cjk_split_into_single_chars(self):
        # Chinese has no spaces; each CJK char is one token. Previously this
        # returned [] (ASCII-only regex), so Chinese events never matched.
        tokens = tokenize("比特币年底破十万美元")
        self.assertIn("比", tokens)
        self.assertIn("特", tokens)
        self.assertIn("币", tokens)
        self.assertTrue(len(tokens) >= 5)  # most chars survive

    def test_mixed_ascii_and_cjk(self):
        tokens = tokenize("Will Bitcoin 比特币 reach $100k")
        # ASCII words whole (min 2), CJK as single chars.
        self.assertNotIn("will", tokens)
        self.assertIn("bitcoin", tokens)
        self.assertIn("100k", tokens)
        self.assertIn("比", tokens)
        self.assertIn("币", tokens)


class TokenOverlapTests(unittest.TestCase):
    def test_identical_sets_are_one(self):
        self.assertEqual(token_overlap({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint_sets_are_zero(self):
        self.assertEqual(token_overlap({"a"}, {"b"}), 0.0)

    def test_empty_set_is_zero(self):
        self.assertEqual(token_overlap(set(), {"a"}), 0.0)
        self.assertEqual(token_overlap({"a"}, set()), 0.0)

    def test_known_jaccard(self):
        # {a,b,c} vs {a,b,d}: intersection 2, union 4 -> 0.5
        self.assertEqual(token_overlap({"a", "b", "c"}, {"a", "b", "d"}), 0.5)


class BuildIndexTests(unittest.TestCase):
    def test_indexes_by_normalized_question(self):
        items = [
            {"question": "Will BTC reach 100k?", "actual_outcome": 100.0},
            {"question": "  will eth pass 5k  ", "actual_outcome": 0.0},
        ]
        index = build_index(items)
        self.assertIn("will btc reach 100k?", index)
        self.assertIn("will eth pass 5k", index)
        self.assertEqual(index["will eth pass 5k"][1], 0.0)

    def test_skips_empty_question_and_bad_value(self):
        items = [
            {"question": "", "actual_outcome": 100.0},
            {"question": "ok", "actual_outcome": "not-a-number"},
            {"question": "good", "actual_outcome": 50.0},
        ]
        index = build_index(items)
        self.assertEqual(len(index), 1)
        self.assertIn("good", index)


class FindMatchTests(unittest.TestCase):
    def setUp(self):
        self.index = build_index([
            {"question": "Will Bitcoin reach $100,000 by end of 2026?",
             "actual_outcome": 100.0},
            {"question": "Will the Fed raise rates in June?",
             "actual_outcome": 0.0},
        ])

    def test_exact_normalized_match_returns_score_one(self):
        # Same question, different casing/whitespace -> exact key hit.
        match = find_match("  will bitcoin reach $100,000 by end of 2026?  ", self.index)
        self.assertIsNotNone(match)
        original, value, score = match
        self.assertEqual(value, 100.0)
        self.assertEqual(score, 1.0)

    def test_fuzzy_match_above_threshold(self):
        # 12 shared meaningful tokens + 1 differing token on the query side:
        # intersection 12 / union 13 ~ 0.923 >= 0.82. Normalized keys differ
        # (july vs june) so this exercises the fuzzy branch, not exact-key.
        shared = (
            "fed reserve raise rates june meeting decision chair "
            "powell vote committee policy"
        )
        index = build_index([{"question": shared, "actual_outcome": 0.0}])
        query = shared.replace("june", "july")
        match = find_match(query, index)
        self.assertIsNotNone(match)
        _, value, score = match
        self.assertEqual(value, 0.0)
        self.assertGreaterEqual(score, FUZZY_THRESHOLD)
        # A fuzzy (non-exact) match scores below 1.0.
        self.assertLess(score, 1.0)

    def test_below_threshold_returns_none(self):
        # Unrelated question -> overlap below threshold.
        match = find_match("Will aliens land on the White House?", self.index)
        self.assertIsNone(match)

    def test_empty_question_returns_none(self):
        self.assertIsNone(find_match("", self.index))
        self.assertIsNone(find_match("   ", self.index))


class WordInTextTests(unittest.TestCase):
    """word_in_text uses \\b word-boundary regex to prevent substring matches."""

    def test_word_boundary_match(self):
        self.assertTrue(word_in_text("eth", "ETH breaks out"))

    def test_word_boundary_no_match_inside_longer_word(self):
        """eth must NOT match inside hegseth."""
        self.assertFalse(word_in_text("eth", "Hegseth speaks today"))

    def test_word_boundary_no_match_partial(self):
        """rate must NOT match inside rates."""
        self.assertFalse(word_in_text("rate", "interest rates rising"))

    def test_word_boundary_match_standalone(self):
        """rate DOES match when it is a standalone word."""
        self.assertTrue(word_in_text("rate", "the rate is rising"))

    def test_multi_word_phrase(self):
        self.assertTrue(word_in_text("bitcoin etf", "Bitcoin ETF approved"))

    def test_multi_word_phrase_no_match(self):
        """bitcoin must NOT match as substring in compound context."""
        self.assertFalse(word_in_text("bitcoin", "bitcoinetf is a scam"))

    def test_non_alnum_token_fallback_to_substring(self):
        """Tokens starting/ending with non-word chars fall back to substring."""
        self.assertTrue(word_in_text("$100k", "BTC hits $100k mark"))

    def test_empty_token_returns_false(self):
        self.assertFalse(word_in_text("", "anything"))

    def test_case_insensitive(self):
        self.assertTrue(word_in_text("BITCOIN", "bitcoin rises"))
        self.assertTrue(word_in_text("btc", "BTC rally"))


if __name__ == "__main__":
    unittest.main()
