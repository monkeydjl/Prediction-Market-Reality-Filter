"""Tests for scoring_service pure functions."""
import unittest
from app.services import scoring_service as svc


class RecommendedActionTests(unittest.TestCase):
    def test_long_signal_returns_structured_yes(self):
        action = svc.recommended_action(
            50, 50, 10.0, signal_direction="LONG", confidence="high"
        )
        self.assertIn("YES", action)
        self.assertIn("high", action)

    def test_strong_long_signal_returns_structured_yes(self):
        action = svc.recommended_action(
            50, 50, 10.0, signal_direction="STRONG_LONG", confidence="medium"
        )
        self.assertIn("YES", action)
        self.assertIn("medium", action)

    def test_short_signal_returns_structured_no(self):
        action = svc.recommended_action(
            50, 50, -10.0, signal_direction="SHORT", confidence="low"
        )
        self.assertIn("NO", action)
        self.assertIn("low", action)

    def test_watchlist_signal_returns_wait(self):
        action = svc.recommended_action(
            50, 50, 1.0, signal_direction="WATCHLIST", confidence="low"
        )
        self.assertIn("等待", action)

    def test_no_signal_falls_back_to_legacy_logic(self):
        # No signal_direction -> old trust/impact based logic
        action = svc.recommended_action(75, 65, 10.0)
        self.assertIn("人工复核", action)

    def test_no_signal_falls_back_to_keep_observing(self):
        action = svc.recommended_action(30, 30, 1.0)
        self.assertIn("保持观察", action)


if __name__ == "__main__":
    unittest.main()
