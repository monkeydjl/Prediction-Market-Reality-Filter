"""Tests for EnsembleEngine."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.kernel.domain import (
    CompetitionIdentity,
    EnvironmentFeatures,
    FeatureSet,
    GeneralFeatures,
    MarketFeatures,
    MatchIdentity,
    PlayerFeatures,
    SeasonIdentity,
    SportIdentity,
    TeamFeatures,
    TeamIdentity,
)
from app.kernel.engines.dixon_coles_engine import DixonColesEngine
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.kernel.engines.ensemble_engine import EnsembleEngine
from app.kernel.protocols import PredictionEngine


_FOOTBALL = SportIdentity(code="football", name="Football")
_EPL = CompetitionIdentity(code="epl", name="EPL", sport=_FOOTBALL)


def _features() -> FeatureSet:
    season = SeasonIdentity(competition=_EPL, season_key="2025")
    home = TeamIdentity(code="ARS", name="Arsenal", competition=_EPL)
    away = TeamIdentity(code="CHE", name="Chelsea", competition=_EPL)
    match = MatchIdentity(
        "epl-1", season, "regular_season", None, home, away,
        datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(4.0, 2.0, None, None),
        team=TeamFeatures(1850.0, 1650.0, 0.7, 0.4, 0.5, 0.25, None, None),
        market=MarketFeatures(1.9, 3.4, 4.2, "test", True),
        player=PlayerFeatures(None, None, 0.1, 0.3),
        environment=EnvironmentFeatures("Emirates", None, None, True),
        custom={},
        data_quality="real",
        quality_notes=[],
        feature_version="football-1.0",
    )


def test_protocol():
    eng = EnsembleEngine([EloOddsEngine(), DixonColesEngine()])
    assert isinstance(eng, PredictionEngine)
    assert eng.name() == "ensemble"


def test_fuse_sums_to_one():
    eng = EnsembleEngine([EloOddsEngine(), DixonColesEngine()])
    fs = _features()
    result = eng.predict(fs, fs.match)
    assert abs(sum(result.outcome_probabilities.values()) - 1.0) < 0.01
    assert len(result.explanation) == 2
    assert result.betting_analysis is not None
    assert set(result.betting_analysis["members"]) == {"elo_odds", "dixon_coles"}


def test_inverse_brier_weights():
    class FakeLearning:
        def engine_score(self, name, competition=None):
            if name == "elo_odds":
                return SimpleNamespace(brier_score=0.2, sample_count=20, accuracy=0.6)
            if name == "dixon_coles":
                return SimpleNamespace(brier_score=0.1, sample_count=20, accuracy=0.65)
            return None

    eng = EnsembleEngine(
        [EloOddsEngine(), DixonColesEngine()],
        learning_service=FakeLearning(),
        min_samples=5,
    )
    fs = _features()
    result = eng.predict(fs, fs.match)
    weights = result.betting_analysis["weights"]
    # lower brier (0.1) → higher weight for dixon_coles
    assert weights["dixon_coles"] > weights["elo_odds"]
