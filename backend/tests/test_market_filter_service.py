"""Regression tests for market_filter_service.

Locks the contract that MarketModel's Optional fields (liquidity, volume,
yes_price) do not crash the filter when they are None. Polymarket and other
event sources occasionally omit these fields; before the fix, a None value
raised TypeError on the bare ``<`` / ``*`` comparisons and silently killed the
whole source's contribution to discovery.
"""

import unittest

from app.models.market import MarketModel
from app.services.market_filter_service import (
    filter_markets,
    _get_filter_issues,
    _priority_score,
)


def _market(**overrides) -> MarketModel:
    base = {
        "id": "m1",
        "question": "Will BTC reach 100k by end of 2026 yes or no test",
        "yes_price": 0.45,
        "volume": 10000.0,
        "liquidity": 20000.0,
    }
    base.update(overrides)
    return MarketModel(**base)


class MarketFilterNoneFieldTests(unittest.TestCase):
    def test_normal_market_has_no_filter_issues(self):
        m = _market()
        self.assertEqual(_get_filter_issues(m, 5000, 2000), [])

    def test_none_volume_does_not_crash(self):
        m = _market(volume=None)
        issues = _get_filter_issues(m, 5000, 2000)
        self.assertIn("low_volume", ",".join(issues))

    def test_none_liquidity_does_not_crash(self):
        m = _market(liquidity=None)
        issues = _get_filter_issues(m, 5000, 2000)
        self.assertIn("low_liquidity", ",".join(issues))

    def test_none_yes_price_defaults_to_neutral(self):
        m = _market(yes_price=None)
        # yes_price None -> 0.5 prior -> 50% -> not too_certain, no crash
        issues = _get_filter_issues(m, 5000, 2000)
        joined = ",".join(issues)
        self.assertNotIn("too_certain", joined)

    def test_all_optional_fields_none_does_not_crash(self):
        m = _market(yes_price=None, volume=None, liquidity=None)
        issues = _get_filter_issues(m, 5000, 2000)
        joined = ",".join(issues)
        self.assertIn("low_liquidity", joined)
        self.assertIn("low_volume", joined)

    def test_filter_markets_accepts_none_fields(self):
        m = _market(volume=None, liquidity=None)
        # Should not raise; market is filtered out for low vol/liquidity.
        result = filter_markets([m], max_markets=5)
        self.assertEqual(result, [])

    def test_priority_score_handles_none_fields(self):
        m = _market(volume=None, liquidity=None, yes_price=None)
        # Should not raise; returns a finite float.
        score = _priority_score(m)
        self.assertIsInstance(score, float)


if __name__ == "__main__":
    unittest.main()
