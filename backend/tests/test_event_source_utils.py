"""Tests for the shared prediction-market source helpers.

Two jobs:

1. pin the *scale* contract (a 0-1 fraction becomes 0-100 points) and the
   field-fallback order in one place;
2. pin the three adapters to these exact objects by identity, so a
   byte-identical copy cannot silently reappear -- the duplication that
   docs/reviews/system-health-audit-2026-09-26.md sections 18-20 found and that
   was converged in section 21.
"""

import unittest

from app.services import event_source_utils as utils


class NormalizeProbabilityTests(unittest.TestCase):
    def test_fractions_are_promoted_to_points(self):
        # The helper does not round (0.57 * 100 is 56.99999999999999 in IEEE-754);
        # adapters round to 2dp when they build the event payload.
        for raw, expected in ((0.41, 41.0), (0.57, 57.0), (1.0, 100.0), (0.0, 0.0), ("0.83", 83.0)):
            with self.subTest(raw=raw):
                self.assertAlmostEqual(utils.normalize_probability(raw), expected, places=9)

    def test_point_scale_values_pass_through(self):
        self.assertEqual(utils.normalize_probability(38), 38.0)
        self.assertEqual(utils.normalize_probability(62.0), 62.0)
        self.assertEqual(utils.normalize_probability(100), 100.0)

    def test_out_of_range_or_unparseable_is_rejected(self):
        for bad in (-1, 101, "abc", None, {}, "", "   "):
            with self.subTest(bad=bad):
                self.assertIsNone(utils.normalize_probability(bad))


class CleanNumberTests(unittest.TestCase):
    def test_currency_and_percent_decoration_is_stripped(self):
        self.assertEqual(utils.clean_number("$1,200.50"), "1200.50")
        self.assertEqual(utils.clean_number("42%"), "42")
        self.assertEqual(utils.clean_number("  7 "), "7")

    def test_non_strings_pass_through_untouched(self):
        for raw in (0.41, 12, None):
            with self.subTest(raw=raw):
                self.assertIs(utils.clean_number(raw), raw)


class ExtractTextTests(unittest.TestCase):
    def test_first_non_blank_field_wins(self):
        self.assertEqual(utils.extract_text({"a": "  ", "b": " hello "}, ("a", "b")), "hello")

    def test_missing_and_blank_yield_empty_string(self):
        self.assertEqual(utils.extract_text({}, ("a",)), "")
        self.assertEqual(utils.extract_text({"a": "   "}, ("a",)), "")


class ExtractNumberTests(unittest.TestCase):
    def test_first_present_field_wins_even_if_earlier_is_absent(self):
        market = {"a": None, "b": "$1,200.5", "c": 9}
        self.assertEqual(utils.extract_number(market, ("a", "b", "c")), 1200.5)

    def test_missing_fields_default_to_zero(self):
        self.assertEqual(utils.extract_number({}, ("a", "b")), 0.0)
        self.assertEqual(utils.extract_number({"a": None}, ("a",)), 0.0)

    def test_unparseable_value_falls_back_to_zero(self):
        self.assertEqual(utils.extract_number({"a": "not-a-number"}, ("a",)), 0.0)


class ExtractMarketListTests(unittest.TestCase):
    def test_bare_list_passes_through(self):
        rows = [{"id": 1}]
        self.assertIs(utils.extract_market_list(rows), rows)

    def test_data_key_is_unwrapped(self):
        self.assertEqual(utils.extract_market_list({"data": [{"id": 1}]}), [{"id": 1}])

    def test_other_shapes_yield_empty(self):
        # including Opinion's {"result": {"list": [...]}} - a different contract
        for shape in ({"result": {"list": [{"id": 1}]}}, {"data": "x"}, "nope", None, 7):
            with self.subTest(shape=shape):
                self.assertEqual(utils.extract_market_list(shape), [])


class AdaptersShareTheHelpersTests(unittest.TestCase):
    """Behaviour tests pass even with a re-localised copy; identity does not."""

    def _adapters(self):
        from app.services import (
            limitless_event_source,
            opinion_event_source,
            predict_fun_event_source,
        )

        return (limitless_event_source, opinion_event_source, predict_fun_event_source)

    def test_text_number_and_probability_helpers_are_the_shared_objects(self):
        for module in self._adapters():
            with self.subTest(module=module.__name__):
                self.assertIs(module._extract_text, utils.extract_text)
                self.assertIs(module._extract_number, utils.extract_number)
                self.assertIs(module._normalize_probability, utils.normalize_probability)

    def test_market_list_helper_is_shared_where_the_contract_matches(self):
        from app.services import limitless_event_source, predict_fun_event_source

        for module in (limitless_event_source, predict_fun_event_source):
            with self.subTest(module=module.__name__):
                self.assertIs(module._extract_market_list, utils.extract_market_list)

    def test_opinion_keeps_its_own_market_list_reader(self):
        from app.services import opinion_event_source

        self.assertIsNot(
            opinion_event_source._extract_market_list,
            utils.extract_market_list,
        )


if __name__ == "__main__":
    unittest.main()
