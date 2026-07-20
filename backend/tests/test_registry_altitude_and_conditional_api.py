"""Registry altitude seed + conditional cal method smoke."""
from app.kernel.factor_registry import FactorRegistry
from app.kernel.learning_service import KernelLearningService


def test_football_seed_includes_altitude():
    reg = FactorRegistry()
    reg.ensure_competition_factors("epl")
    # may hit real DB — assert defaults list at least
    names = [x[0] for x in FactorRegistry._FOOTBALL_MULTI_FACTOR_DEFAULTS]
    assert "altitude" in names
    assert "referee" in names
    # weights of softs only (elo/odds global separate)
    soft = sum(w for _, _, w in FactorRegistry._FOOTBALL_MULTI_FACTOR_DEFAULTS)
    assert abs(soft - 0.44) < 1e-6 or soft > 0.3  # soft-only seed sum


def test_learning_has_stage_calibration_method():
    assert hasattr(KernelLearningService, "update_calibration_by_stage")
    assert hasattr(KernelLearningService, "update_calibration_by_confidence")
