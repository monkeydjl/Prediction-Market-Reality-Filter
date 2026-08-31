# backend/tests/test_learning_weights.py
"""Tests for update_weights EWMA (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome,
)
from app.kernel.learning_service import KernelLearningService
from app.kernel.factor_registry import FactorRegistry


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction(elo_outcome="home_win", odds_outcome="home_win",
                     engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome=elo_outcome),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome=odds_outcome),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc_with_registry(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    registry = FactorRegistry()
    service = KernelLearningService(factor_registry=registry)
    yield service, registry
    close_kernel_session()


def _seed_data(service, count, elo_correct=True, odds_correct=True,
               actual_outcome="home_win", competition="world_cup"):
    """Seed N predictions + outcomes with controlled per-factor accuracy."""
    for i in range(count):
        match = _make_match(f"m{i}", competition)
        elo_out = actual_outcome if elo_correct else "away_win"
        odds_out = actual_outcome if odds_correct else "away_win"
        pred = _make_prediction(elo_out, odds_out)
        service.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id=f"m{i}", home_score=2, away_score=1,
            outcome=actual_outcome,
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        service.record_outcome(outcome)
        service.compute_error(f"m{i}")


class TestUpdateWeights:
    def test_insufficient_samples_skips(self, svc_with_registry):
        """With < 10 samples, update_weights does nothing."""
        svc, reg = svc_with_registry
        _seed_data(svc, 5)
        old_elo = reg.get_weight("elo", "world_cup")
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") == old_elo

    def test_ewma_updates_weights(self, svc_with_registry):
        """With >= 10 samples, weights are updated via EWMA."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12)
        old_elo = reg.get_weight("elo", "world_cup")  # 0.30

        svc.update_weights("world_cup")
        new_elo = reg.get_weight("elo", "world_cup")
        # Weight should change (both factors predicted correctly, so target ≈ 0.5/0.5)
        assert new_elo != old_elo

    def test_weight_clamped_to_floor(self, svc_with_registry):
        """Weight cannot go below 0.05."""
        svc, reg = svc_with_registry
        # Elo always wrong, odds always right → elo target ≈ 0
        _seed_data(svc, 12, elo_correct=False, odds_correct=True)
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") >= 0.05

    def test_weight_clamped_to_ceiling(self, svc_with_registry):
        """Weight cannot go above 0.95."""
        svc, reg = svc_with_registry
        # Elo always right, odds always wrong → elo target ≈ 1.0
        _seed_data(svc, 12, elo_correct=True, odds_correct=False)
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") <= 0.95

    def test_both_factors_wrong_no_change(self, svc_with_registry):
        """When both factors are always wrong (total_acc=0), no update."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12, elo_correct=False, odds_correct=False)
        old_elo = reg.get_weight("elo", "world_cup")
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") == old_elo

    def test_per_competition_isolation(self, svc_with_registry):
        """Weight update for one competition doesn't affect another."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12, competition="world_cup")
        _seed_data(svc, 12, competition="epl")
        svc.update_weights("world_cup")

        # EPL weight should still be default
        assert reg.get_weight("elo", "epl") == 0.30

    def test_weights_persisted_to_db(self, svc_with_registry):
        """Updated weights are persisted to KernelFactor table."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12)
        svc.update_weights("world_cup")

        from app.kernel.kernel_db import KernelFactor
        session = get_kernel_session()
        row = session.query(KernelFactor).filter_by(
            factor_id="elo", competition="world_cup"
        ).first()
        assert row is not None
        assert row.source == "ewma"
        session.close()

    def test_missing_predicted_outcome_skipped(self, svc_with_registry):
        """Predictions without predicted_outcome are skipped in per-factor accuracy."""
        svc, reg = svc_with_registry
        # Seed predictions WITHOUT predicted_outcome (simulating pre-Phase 3 data)
        for i in range(12):
            match = _make_match(f"old_m{i}")
            pred = PredictionResult(
                predicted_scores={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
                confidence=0.72, engine_name="elo_odds",
                explanation=[
                    ContributionItem(factor="elo", direction="support", weight=0.30,
                                     available=True, detail="Elo", predicted_outcome=None),
                    ContributionItem(factor="odds", direction="support", weight=0.70,
                                     available=True, detail="Odds", predicted_outcome=None),
                ],
                betting_analysis=None, feature_version="1.0",
                prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
            )
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"old_m{i}", home_score=2, away_score=1,
                outcome="home_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"old_m{i}")

        old_elo = reg.get_weight("elo", "world_cup")
        svc.update_weights("world_cup")
        # No per-factor data → no update
        assert reg.get_weight("elo", "world_cup") == old_elo


class TestUpdateWeightsReportsWhatItDid:
    """Five conditions decline to learn; all five used to be a bare ``return``.

    The weekly scheduler job called this and logged "Updated weights for %s"
    unconditionally, because the value it got back was ``None`` either way.
    """

    def test_a_real_update_says_updated_with_its_counts(self, svc_with_registry):
        svc, reg = svc_with_registry
        _seed_data(svc, 12)
        result = svc.update_weights("world_cup")
        assert result["updated"] is True
        assert result["reason"] is None
        assert result["factors"] == 2
        assert result["samples"] == 12

    def test_insufficient_samples_is_distinguishable_from_an_update(
        self, svc_with_registry,
    ):
        svc, reg = svc_with_registry
        _seed_data(svc, 5)
        result = svc.update_weights("world_cup")
        # Not just "falsy": the caller has to be able to name the condition, and
        # "did not update" must not be reachable by the same value an update gives.
        assert result["updated"] is False
        assert result["reason"] == "insufficient_samples"
        assert result["samples"] == 5
        assert result["min_samples"] == 10

    def test_zero_total_accuracy_has_its_own_reason(self, svc_with_registry):
        svc, reg = svc_with_registry
        _seed_data(svc, 12, elo_correct=False, odds_correct=False)
        result = svc.update_weights("world_cup")
        assert result["updated"] is False
        assert result["reason"] == "zero_total_accuracy"

    def test_no_factor_samples_has_its_own_reason(self, svc_with_registry):
        """Predictions exist and are numerous, but no factor cast a vote."""
        svc, reg = svc_with_registry
        for i in range(12):
            match = _make_match(f"nv{i}", "world_cup")
            pred = PredictionResult(
                predicted_scores={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
                confidence=0.72, engine_name="elo_odds",
                explanation=[
                    ContributionItem(factor="elo", direction="support", weight=0.30,
                                     available=True, detail="Elo",
                                     predicted_outcome=None),
                ],
                betting_analysis=None, feature_version="1.0",
                prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
            )
            svc.record_prediction(match, pred)
            svc.record_outcome(MatchOutcome(
                match_id=f"nv{i}", home_score=2, away_score=1, outcome="home_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            ))
            svc.compute_error(f"nv{i}")
        result = svc.update_weights("world_cup")
        assert result["updated"] is False
        assert result["reason"] == "no_factor_samples"

    def test_no_registry_says_so_rather_than_looking_like_a_skip(
        self, svc_with_registry,
    ):
        """The one condition that is a wiring fault, not a data shortage.

        Note this branch is **not reachable through the constructor**:
        ``__init__`` does ``factor_registry or FactorRegistry()``, so passing
        ``None`` builds a real registry and this returns
        ``insufficient_samples`` instead. The attribute is set directly here to
        cover the branch honestly rather than to imply the public API can reach it.
        """
        svc, _ = svc_with_registry
        assert KernelLearningService(factor_registry=None)._factor_registry is not None
        svc._factor_registry = None
        result = svc.update_weights("world_cup")
        assert result["updated"] is False
        assert result["reason"] == "no_factor_registry"

    def test_every_reason_is_distinct(self, svc_with_registry):
        """A shared reason string would collapse two conditions for the caller.

        The two competitions need disjoint match ids: ``_seed_data`` names rows
        ``m{i}`` regardless of competition, so seeding a second competition with
        the same count *rewrites* the first one's rows rather than adding to them.
        """
        svc, reg = svc_with_registry
        _seed_data(svc, 5, competition="world_cup")
        few = svc.update_weights("world_cup")["reason"]

        for i in range(12):
            match = _make_match(f"epl_m{i}", "epl")
            pred = _make_prediction("away_win", "away_win")
            svc.record_prediction(match, pred)
            svc.record_outcome(MatchOutcome(
                match_id=f"epl_m{i}", home_score=2, away_score=1,
                outcome="home_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            ))
            svc.compute_error(f"epl_m{i}")
        zero = svc.update_weights("epl")["reason"]

        svc._factor_registry = None
        none_reg = svc.update_weights("world_cup")["reason"]
        assert len({few, zero, none_reg}) == 3, (few, zero, none_reg)
