# backend/tests/test_score_mae_no_scoreline.py
"""An engine that published no scoreline must not be graded as if it said 0-0.

``market_only`` (LoL) returns ``predicted_scores={}`` on purpose -- a series
moneyline carries no scoreline claim. ``compute_error`` used to read it with
``.get("home", 0)`` / ``.get("away", 0)``, so a 3-1 series was graded MAE 2.00
against a prediction the engine never made, and ``engine_score`` then divided
``sum(score_mae or 0)`` by *every* row, so the fabricated value entered the mean --
and a ``None`` would have entered it as 0, pulling MAE down, which reads as a
*better* engine.

Every other engine emits both keys, which is why only this one was affected.
"""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelPrediction,
    close_kernel_db,
    get_kernel_session,
    init_kernel_db,
)
from app.kernel.learning_service import KernelLearningService


@pytest.fixture
def svc(tmp_path):
    close_kernel_db()
    init_kernel_db(str(tmp_path / "kernel_mae.db"))
    yield KernelLearningService()
    close_kernel_db()


def _seed(match_id, predicted_scores, *, home, away, outcome="home_win",
          engine="market_only", probs=None, competition="lol"):
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="lol", competition=competition,
            season="2026", engine=engine,
            predicted_scores=predicted_scores,
            outcome_probabilities=probs or {"home_win": 0.62, "away_win": 0.38},
            confidence=0.7, feature_version="1.0",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ))
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=home, away_score=away,
            outcome=outcome,
            finished_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        ))
        session.commit()
    finally:
        session.close()


class TestNoScorelineIsNotGraded:
    def test_an_empty_scoreline_yields_no_mae(self, svc):
        _seed("lol-1", {}, home=3, away=1)
        error = svc.compute_error("lol-1")
        assert error is not None
        # The defect graded this 2.00. An absent claim is not a wrong one.
        assert error.score_mae is None
        # The rest of the row is still graded: the engine did publish probabilities.
        assert error.outcome_correct is True
        assert error.brier_score == pytest.approx(0.2888, abs=1e-4)

    def test_the_persisted_column_is_null_too(self, svc):
        _seed("lol-2", {}, home=3, away=2)
        svc.compute_error("lol-2")
        session = get_kernel_session()
        try:
            row = session.get(KernelMatchOutcome, "lol-2")
            assert row.score_mae is None
            # outcome_correct must still be written, or the fix would have
            # silently degraded the half of the row that is measurable.
            assert row.outcome_correct == 1
        finally:
            session.close()

    def test_a_real_scoreline_is_still_graded(self, svc):
        """Rival configuration: without this, returning None always would pass."""
        _seed("nba-1", {"home": 108.0, "away": 100.0}, home=110, away=99,
              engine="basketball", competition="nba")
        error = svc.compute_error("nba-1")
        # (|108-110| + |100-99|) / 2 == 1.5
        assert error.score_mae == pytest.approx(1.5)

    @pytest.mark.parametrize("scores", [
        {"home": 1.0},              # away missing
        {"away": 1.0},              # home missing
        {},                         # both missing
        None,                       # column null
    ])
    def test_a_half_published_scoreline_is_not_graded_either(self, svc, scores):
        """One key present is still not a scoreline; 0 for the other is invented."""
        _seed("nhl-1", scores, home=4, away=2, engine="hockey", competition="nhl")
        error = svc.compute_error("nhl-1")
        assert error.score_mae is None


class TestEngineScoreMaeDenominator:
    def test_ungraded_rows_leave_the_mae_mean_entirely(self, svc):
        """Three scored rows plus one ungraded must average over three."""
        _seed("nba-1", {"home": 100.0, "away": 99.0}, home=101, away=99,
              engine="basketball", competition="nba")           # mae 0.5
        _seed("nba-2", {"home": 100.0, "away": 99.0}, home=102, away=99,
              engine="basketball", competition="nba")           # mae 1.0
        _seed("nba-3", {"home": 100.0, "away": 99.0}, home=103, away=99,
              engine="basketball", competition="nba")           # mae 1.5
        _seed("nba-4", {}, home=110, away=90,
              engine="basketball", competition="nba")           # ungraded
        for mid in ("nba-1", "nba-2", "nba-3", "nba-4"):
            svc.compute_error(mid)

        score = svc.engine_score("basketball", "nba")
        assert score is not None
        # (0.5 + 1.0 + 1.5) / 3 == 1.0. Under the defect the ungraded row entered
        # the divisor: 3.0 / 4 == 0.75, i.e. the engine looked better than it is.
        assert score.avg_mae == pytest.approx(1.0)
        assert score.mae_n == 3
        # sample_count still counts every row, because accuracy and Brier do use
        # all four. The two denominators are genuinely different.
        assert score.sample_count == 4

    def test_all_rows_ungraded_reports_no_mae_rather_than_zero(self, svc):
        """0.0 is the best possible MAE; it must not mean 'never measured'."""
        _seed("lol-1", {}, home=3, away=0)
        _seed("lol-2", {}, home=3, away=1)
        for mid in ("lol-1", "lol-2"):
            svc.compute_error(mid)

        score = svc.engine_score("market_only", "lol")
        assert score is not None
        assert score.avg_mae is None
        assert score.mae_n == 0
        assert score.sample_count == 2
