# backend/tests/test_learning_dynamic_outcomes.py
"""Tests for LearningService generalization — dynamic outcome keys and factor iteration.

Verifies:
1. Binary Brier score works for basketball (home_win/away_win, no draw)
2. 4-factor EWMA update works for basketball (elo/home_court/rest/form)
3. Football 3-way regression unchanged (existing behavior preserved)
4. Mixed competition isolation (NBA update doesn't affect football weights)
5. Empty explanation safe handling (no crash)
"""
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
from app.kernel.factor_registry import FactorRegistry, FactorConfig


def _make_match(match_id="m1", competition="nba") -> MatchIdentity:
    sport = SportIdentity(code="basketball", name="Basketball")
    comp = CompetitionIdentity(code=competition, name="NBA", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2024-25")
    home = TeamIdentity(code="BOS", name="Boston Celtics", competition=comp)
    away = TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _make_football_match(match_id="fm1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_basketball_prediction(
    elo_outcome="home_win", home_court_outcome="home_win",
    rest_outcome="home_win", form_outcome="home_win",
) -> PredictionResult:
    """4-factor basketball prediction with binary outcomes."""
    return PredictionResult(
        predicted_scores={"home": 110.0, "away": 105.0},
        outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
        confidence=0.62, engine_name="basketball",
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.45,
                             available=True, detail="Elo", predicted_outcome=elo_outcome),
            ContributionItem(factor="home_court", direction="support", weight=0.15,
                             available=True, detail="Home", predicted_outcome=home_court_outcome),
            ContributionItem(factor="rest", direction="support", weight=0.15,
                             available=True, detail="Rest", predicted_outcome=rest_outcome),
            ContributionItem(factor="form", direction="support", weight=0.25,
                             available=True, detail="Form", predicted_outcome=form_outcome),
        ],
        betting_analysis=None, feature_version="nba-1.0",
        prediction_timestamp=datetime(2024, 12, 24, tzinfo=timezone.utc),
    )


def _make_football_prediction(elo_outcome="home_win", odds_outcome="home_win") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name="elo_odds",
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome=elo_outcome),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome=odds_outcome),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


def _seed_nba_factors(reg: FactorRegistry) -> None:
    """Manually register NBA factors (replaces reg.ensure_competition_factors("nba")
    from Task 9 — keeps Task 8 independently testable)."""
    for fid, cat, w in [("elo", "elo_rating", 0.45), ("home_court", "home_advantage", 0.15),
                        ("rest", "rest_days", 0.15), ("form", "recent_form", 0.25)]:
        reg.register_factor(FactorConfig(fid, cat, "1.0", w, "nba", True, "test",
                                         datetime.now(timezone.utc)))


@pytest.fixture
def svc_with_registry(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    registry = FactorRegistry()
    service = KernelLearningService(factor_registry=registry)
    yield service, registry
    close_kernel_session()


class TestBinaryBrierScore:
    def test_basketball_brier_score_computed(self, svc_with_registry):
        """compute_error works with binary {home_win, away_win} probabilities."""
        svc, reg = svc_with_registry
        match = _make_match("nba-1")
        pred = _make_basketball_prediction()
        svc.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id="nba-1", home_score=110, away_score=105,
            outcome="home_win",
            finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
        )
        svc.record_outcome(outcome)
        error = svc.compute_error("nba-1")
        assert error is not None
        # Brier = (0.65 - 1)^2 + (0.35 - 0)^2 = 0.1225 + 0.1225 = 0.245
        assert error.brier_score == 0.245


class TestFourFactorEWMA:
    def test_basketball_weight_update(self, svc_with_registry):
        """update_weights works with 4 basketball factors."""
        svc, reg = svc_with_registry
        # Seed NBA factors
        _seed_nba_factors(reg)

        # Seed 12 predictions + outcomes
        for i in range(12):
            match = _make_match(f"nba-{i}")
            pred = _make_basketball_prediction()
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"nba-{i}", home_score=110, away_score=105,
                outcome="home_win",
                finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"nba-{i}")

        old_elo = reg.get_weight("elo", "nba")
        svc.update_weights("nba")
        new_elo = reg.get_weight("elo", "nba")
        # Weights should change (all factors predicted correctly → target ≈ equal weights)
        assert new_elo != old_elo


class TestFootballRegressionUnchanged:
    def test_football_3way_brier_unchanged(self, svc_with_registry):
        """Football Brier score with 3-way outcomes is identical to old behavior."""
        svc, reg = svc_with_registry
        match = _make_football_match("wc-1")
        pred = _make_football_prediction()
        svc.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id="wc-1", home_score=2, away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 22, 0, tzinfo=timezone.utc),
        )
        svc.record_outcome(outcome)
        error = svc.compute_error("wc-1")
        assert error is not None
        # Old formula: (0.55-1)^2 + (0.25-0)^2 + (0.20-0)^2
        #            = 0.2025 + 0.0625 + 0.04 = 0.305
        assert error.brier_score == 0.305


class TestMixedCompetitionIsolation:
    def test_nba_update_doesnt_affect_football(self, svc_with_registry):
        """NBA weight update doesn't change football weights."""
        svc, reg = svc_with_registry
        _seed_nba_factors(reg)

        # Seed NBA data
        for i in range(12):
            match = _make_match(f"nba-{i}")
            pred = _make_basketball_prediction()
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"nba-{i}", home_score=110, away_score=105,
                outcome="home_win",
                finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"nba-{i}")

        # Football weight should be default (0.30) before NBA update
        old_football_elo = reg.get_weight("elo", "world_cup")
        assert old_football_elo == 0.30

        svc.update_weights("nba")

        # Football weight unchanged after NBA update
        assert reg.get_weight("elo", "world_cup") == 0.30


class TestEmptyExplanationSafeHandling:
    def test_empty_explanation_no_crash(self, svc_with_registry):
        """update_weights doesn't crash when explanation is empty."""
        svc, reg = svc_with_registry
        _seed_nba_factors(reg)

        for i in range(12):
            match = _make_match(f"empty-{i}")
            pred = PredictionResult(
                predicted_scores={"home": 110.0, "away": 105.0},
                outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
                confidence=0.62, engine_name="basketball",
                explanation=[],  # Empty!
                betting_analysis=None, feature_version="nba-1.0",
                prediction_timestamp=datetime(2024, 12, 24, tzinfo=timezone.utc),
            )
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"empty-{i}", home_score=110, away_score=105,
                outcome="home_win",
                finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"empty-{i}")

        old_elo = reg.get_weight("elo", "nba")
        # Should not crash, and should not update (no factor data)
        svc.update_weights("nba")
        assert reg.get_weight("elo", "nba") == old_elo
