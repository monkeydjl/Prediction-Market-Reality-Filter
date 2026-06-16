import unittest

from app.utils.market_utils import clamp, safe_float, safe_int


class MarketUtilsTests(unittest.TestCase):
    def test_safe_float_preserves_finite_values(self):
        self.assertEqual(safe_float("12.5", 1.0), 12.5)

    def test_safe_float_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf", float("nan"), float("inf")):
            self.assertEqual(safe_float(value, 7.0), 7.0)

    def test_safe_int_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf", float("nan"), float("inf")):
            self.assertEqual(safe_int(value, 7), 7)

    def test_safe_int_preserves_finite_values(self):
        self.assertEqual(safe_int("12.9", 1), 12)

    def test_clamp_accepts_numeric_strings(self):
        self.assertEqual(clamp("12.5", 0, 10), 10)

    def test_clamp_uses_lower_bound_for_invalid_values(self):
        for value in ("bad", "nan", "inf", float("nan"), float("inf")):
            self.assertEqual(clamp(value, 2, 10), 2)


if __name__ == "__main__":
    unittest.main()
