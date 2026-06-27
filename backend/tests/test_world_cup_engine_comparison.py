"""Tests for engine accuracy comparison (both engines credited per match)."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base, MatchFixture, PredictionHistory
from app.services import engine_comparison_service as svc


def naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EngineAccuracyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _run(self):
        # calculate_engine_accuracy() opens its own session; point it at ours
        # and make close a no-op so tearDown owns the lifecycle.
        with (
            patch.object(svc, "get_prediction_session", return_value=self.session),
            patch.object(svc, "close_prediction_session", lambda s: None),
        ):
            return svc.calculate_engine_accuracy()

    def _add_finished_match(self, home_score: int, away_score: int):
        self.session.add(
            MatchFixture(
                match_id="m1",
                fixture_id="m1",
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=naive() - timedelta(hours=3),
                stage="GROUP_STAGE",
                status="finished",
                home_score=home_score,
                away_score=away_score,
            )
        )

    def _add_history(self, method: str, ph: float, pa: float, hw: float, d: float, aw: float, ts: datetime):
        self.session.add(
            PredictionHistory(
                match_id="m1",
                timestamp=ts,
                predicted_home_score=ph,
                predicted_away_score=pa,
                home_win_prob=hw,
                draw_prob=d,
                away_win_prob=aw,
                confidence=0.7,
                trigger="manual",
                prediction_method=method,
            )
        )

    def test_credits_both_engines_for_a_single_match(self):
        # Actual 2-1 (home win). elo_odds nails it; hybrid says 1-1 (draw, wrong).
        # Both recorded for the SAME match (dual-engine recording).
        self._add_finished_match(2, 1)
        self._add_history("elo_odds_fusion", 2.0, 1.0, 0.6, 0.25, 0.15, naive() - timedelta(hours=4))
        self._add_history("hybrid", 1.0, 1.0, 0.3, 0.4, 0.3, naive() - timedelta(hours=4))
        self.session.commit()

        engines = self._run()["engines"]

        # Both engines must appear — the core regression this fixes.
        self.assertEqual(set(engines.keys()), {"elo_odds", "hybrid"})
        self.assertEqual(engines["elo_odds"]["total_matches"], 1)
        self.assertEqual(engines["hybrid"]["total_matches"], 1)

        # elo_odds perfect: exact score + correct outcome
        self.assertEqual(engines["elo_odds"]["exact_score_rate"], 1.0)
        self.assertEqual(engines["elo_odds"]["outcome_accuracy"], 1.0)
        self.assertEqual(engines["elo_odds"]["avg_score_error"], 0)

        # hybrid wrong outcome, score error |1-2|+|1-1| = 1
        self.assertEqual(engines["hybrid"]["outcome_accuracy"], 0.0)
        self.assertEqual(engines["hybrid"]["avg_score_error"], 1)

    def test_uses_latest_row_per_engine(self):
        # Two elo_odds rows: an old wrong one and a newer correct one.
        # The newer should win.
        self._add_finished_match(0, 0)  # draw
        self._add_history("elo_odds", 2.0, 0.0, 0.8, 0.1, 0.1, naive() - timedelta(hours=9))  # old, wrong
        self._add_history("elo_only", 0.0, 0.0, 0.2, 0.6, 0.2, naive() - timedelta(hours=1))  # new, correct
        self.session.commit()

        engines = self._run()["engines"]

        self.assertEqual(set(engines.keys()), {"elo_odds"})
        self.assertEqual(engines["elo_odds"]["total_matches"], 1)
        # Latest predicted 0-0 == actual draw -> exact + correct outcome
        self.assertEqual(engines["elo_odds"]["exact_score_rate"], 1.0)
        self.assertEqual(engines["elo_odds"]["outcome_accuracy"], 1.0)

    def test_buckets_integrated_separately_from_elo_odds(self):
        self.assertEqual(
            svc._bucket_engine("integrated (elo_odds 40% + hybrid 60%)"),
            "integrated",
        )
        self.assertEqual(svc._bucket_engine("elo_only"), "elo_odds")

    def test_excludes_comparison_history_rows(self):
        self._add_finished_match(2, 1)
        timestamp = naive() - timedelta(hours=4)
        self._add_history("hybrid", 2.0, 1.0, 0.6, 0.25, 0.15, timestamp)
        comparison = PredictionHistory(
            match_id="m1",
            timestamp=timestamp,
            predicted_home_score=0.0,
            predicted_away_score=3.0,
            home_win_prob=0.1,
            draw_prob=0.1,
            away_win_prob=0.8,
            confidence=0.9,
            trigger="manual_comparison",
            prediction_method="elo_only",
        )
        self.session.add(comparison)
        self.session.commit()

        engines = self._run()["engines"]

        self.assertEqual(set(engines.keys()), {"hybrid"})

    def test_no_finished_matches(self):
        result = self._run()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engines"], {})


if __name__ == "__main__":
    unittest.main()
