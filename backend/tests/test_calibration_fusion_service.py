"""Tests for CalibrationFusionService — sample-count-weighted trust fusion."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_db,
    KernelCalibration, KernelMarketCalibration,
    get_kernel_session,
)
from app.kernel.calibration_fusion_service import (
    CalibrationFusionService, CompositeTrust,
)


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "fusion_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_phase3_calibration(
    engine="BasketballEngine", competition="nba",
    avg_accuracy=0.72, sample_count=20,
):
    """Insert a KernelCalibration row."""
    session = get_kernel_session()
    try:
        session.add(KernelCalibration(
            engine=engine, competition=competition, slope=1.0, intercept=0.0,
            sample_count=sample_count, avg_confidence=0.65,
            avg_accuracy=avg_accuracy, last_updated=_utcnow(),
        ))
        session.commit()
    finally:
        session.close()


def _seed_market_calibration(
    engine="BasketballEngine", competition="nba",
    direction_accuracy=0.80, sample_count=30,
):
    """Insert a KernelMarketCalibration row."""
    session = get_kernel_session()
    try:
        session.add(KernelMarketCalibration(
            engine=engine, competition=competition, slope=0.95, intercept=0.02,
            sample_count=sample_count, avg_brier=0.15, avg_signed_error=-0.01,
            direction_accuracy=direction_accuracy, last_updated=_utcnow(),
        ))
        session.commit()
    finally:
        session.close()


# --- Pure trust computation tests ---

def test_compute_trust_dormant_when_both_tables_empty(kernel_db):
    """Both tables empty → dormant trust (0.5), source='dormant'."""
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "dormant"
    assert result.trust == pytest.approx(0.5)
    assert result.phase3_trust == pytest.approx(0.5)
    assert result.market_trust == pytest.approx(0.5)
    assert result.phase3_weight == pytest.approx(0.0)
    assert result.market_weight == pytest.approx(0.0)
    assert result.phase3_sample_count == 0
    assert result.market_sample_count == 0


def test_compute_trust_phase3_only(kernel_db):
    """Only Phase 3 has data → phase3_trust, source='phase3_only'."""
    _seed_phase3_calibration(avg_accuracy=0.75, sample_count=25)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "phase3_only"
    assert result.phase3_trust == pytest.approx(0.75)
    assert result.trust == pytest.approx(0.75)
    assert result.phase3_weight == pytest.approx(1.0)
    assert result.market_weight == pytest.approx(0.0)
    assert result.phase3_sample_count == 25
    assert result.market_sample_count == 0


def test_compute_trust_market_only(kernel_db):
    """Only market calibration has data → market_trust, source='market_only'."""
    _seed_market_calibration(direction_accuracy=0.82, sample_count=35)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "market_only"
    assert result.market_trust == pytest.approx(0.82)
    assert result.trust == pytest.approx(0.82)
    assert result.phase3_weight == pytest.approx(0.0)
    assert result.market_weight == pytest.approx(1.0)
    assert result.phase3_sample_count == 0
    assert result.market_sample_count == 35


def test_compute_trust_fusion_weighted_by_sample_count(kernel_db):
    """Both tables have data → weighted fusion, source='fusion'.

    phase3: avg_accuracy=0.72, sample_count=20
    market: direction_accuracy=0.80, sample_count=30
    w1 = 20 / (20 + 30) = 0.4
    w2 = 30 / (20 + 30) = 0.6
    composite = 0.4 * 0.72 + 0.6 * 0.80 = 0.288 + 0.480 = 0.768
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "fusion"
    assert result.phase3_trust == pytest.approx(0.72)
    assert result.market_trust == pytest.approx(0.80)
    assert result.phase3_weight == pytest.approx(0.4)
    assert result.market_weight == pytest.approx(0.6)
    assert result.trust == pytest.approx(0.768)
    assert result.phase3_sample_count == 20
    assert result.market_sample_count == 30


def test_compute_trust_phase3_dormant_when_sample_count_below_min(kernel_db):
    """Phase 3 with sample_count < CALIBRATION_FEEDBACK_MIN_SAMPLES → dormant (0.5)."""
    _seed_phase3_calibration(avg_accuracy=0.90, sample_count=3)  # below MIN (10)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    # Only Phase 3 with low sample_count → phase3 dormant, no market data
    assert result.source == "phase3_only"  # has a row, just dormant
    assert result.phase3_trust == pytest.approx(0.5)  # dormant
    assert result.trust == pytest.approx(0.5)


def test_compute_trust_market_dormant_when_sample_count_below_min(kernel_db):
    """Market calibration with sample_count < MIN_SAMPLES_FOR_MARKET_CALIBRATION → dormant (0.5)."""
    _seed_market_calibration(direction_accuracy=0.95, sample_count=3)  # below MIN (10)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "market_only"
    assert result.market_trust == pytest.approx(0.5)  # dormant
    assert result.trust == pytest.approx(0.5)


def test_compute_trust_clamped_to_floor(kernel_db):
    """Trust values below DIAGNOSIS_TRUST_FLOOR are clamped."""
    _seed_phase3_calibration(avg_accuracy=0.10, sample_count=20)  # very low accuracy
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    # DIAGNOSIS_TRUST_FLOOR is 0.3 (from config)
    from app.core import config
    expected_floor = config.settings.DIAGNOSIS_TRUST_FLOOR
    assert result.phase3_trust == pytest.approx(expected_floor)
    assert result.trust == pytest.approx(expected_floor)


def test_compute_trust_fusion_with_one_dormant_source(kernel_db):
    """Fusion where one source is dormant (low sample_count).

    phase3: qualified (sample_count=20, accuracy=0.72)
    market: dormant (sample_count=3, direction_accuracy=0.95)
    → fusion, but market_trust = 0.5 (dormant)
    w1 = 20 / (20 + 3) = 0.8696
    w2 = 3 / (20 + 3) = 0.1304
    composite = 0.8696 * 0.72 + 0.1304 * 0.5 = 0.6261 + 0.0652 = 0.6913
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.95, sample_count=3)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "fusion"
    assert result.phase3_trust == pytest.approx(0.72)
    assert result.market_trust == pytest.approx(0.5)  # dormant
    assert result.trust == pytest.approx(0.6913, abs=0.01)


def test_edge_detector_delegates_to_fusion_when_enabled(kernel_db, monkeypatch):
    """When PHASE8_CALIBRATION_FUSION_ENABLED=true, EdgeDetectorService uses fusion."""
    from app.core import config
    monkeypatch.setattr(config.settings, "PHASE8_CALIBRATION_FUSION_ENABLED", True)
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    # Expected: 0.4 * 0.72 + 0.6 * 0.80 = 0.768
    from app.kernel.edge_detector_service import EdgeDetectorService
    svc = EdgeDetectorService()
    trust = svc._compute_trust("BasketballEngine", "nba")
    assert trust == pytest.approx(0.768)


def test_edge_detector_falls_back_to_phase3_when_disabled(kernel_db, monkeypatch):
    """When PHASE8_CALIBRATION_FUSION_ENABLED=false, EdgeDetectorService uses Phase 3 only."""
    from app.core import config
    monkeypatch.setattr(config.settings, "PHASE8_CALIBRATION_FUSION_ENABLED", False)
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    from app.kernel.edge_detector_service import EdgeDetectorService
    svc = EdgeDetectorService()
    trust = svc._compute_trust("BasketballEngine", "nba")
    # Phase 3 only: 0.72 (qualified, not dormant)
    assert trust == pytest.approx(0.72)
    # Should NOT be the fusion value 0.768
    assert trust != pytest.approx(0.768)
