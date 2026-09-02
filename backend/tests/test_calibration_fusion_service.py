"""Tests for CalibrationFusionService — sample-count-weighted trust fusion."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError

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


def _set_market_sample_count(
    sample_count, engine="BasketballEngine", competition="nba",
):
    """Move an existing market row's sample_count.

    KernelMarketCalibration has UNIQUE(engine, competition), so a monotonicity
    sweep has to update the row rather than re-seed it.
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelMarketCalibration)
            .filter(
                KernelMarketCalibration.engine == engine,
                KernelMarketCalibration.competition == competition,
            )
            .one()
        )
        row.sample_count = sample_count
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


def test_dormant_source_gets_zero_weight(kernel_db):
    """A dormant source must not dilute a qualified one.

    phase3: qualified (sample_count=20, accuracy=0.72)
    market: dormant (sample_count=3) -> market_trust is the 0.5 sentinel

    The sentinel means "no usable estimate", so it carries no weight and the
    composite is exactly the qualified source's value. The previous arithmetic
    weighted the sentinel by its sample count and returned 0.6913.
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.95, sample_count=3)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.trust == pytest.approx(0.72)
    assert result.phase3_weight == pytest.approx(1.0)
    assert result.market_weight == pytest.approx(0.0)
    assert result.phase3_trust == pytest.approx(0.72)
    assert result.market_trust == pytest.approx(0.5)  # sentinel, still reported
    # Only one channel informed the number, so don't claim corroboration.
    assert result.source == "phase3_only"
    # Sample counts stay observable even at zero weight.
    assert result.market_sample_count == 3


def test_a_row_that_says_i_dont_know_does_not_move_the_answer(kernel_db):
    """Presence of a dormant row must equal absence of the row.

    Both carry the same information — none. Under the previous arithmetic a
    1-sample dormant market row moved composite trust from 0.7200 to 0.7095.
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    svc = CalibrationFusionService()
    without_row = svc.compute_trust("BasketballEngine", "nba").trust

    _seed_market_calibration(direction_accuracy=0.95, sample_count=1)
    with_dormant_row = svc.compute_trust("BasketballEngine", "nba").trust

    assert with_dormant_row == pytest.approx(without_row)


def test_accumulating_dormant_evidence_never_lowers_trust(kernel_db):
    """Monotonicity across the qualification threshold.

    This is the discriminating case. With Phase 3 fixed at 0.72 over 20 samples
    and a market channel whose real direction accuracy is 0.95, the previous
    arithmetic produced 0.7095 / 0.6630 / 0.6517 as the market row accumulated
    1 / 7 / 9 dormant samples, then jumped to 0.7967 at sample 10 — trust fell
    as evidence about a *good* channel accumulated. No shrinkage-toward-prior
    reading can justify that: under shrinkage more data means less pull toward
    the prior.
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    svc = CalibrationFusionService()

    _seed_market_calibration(direction_accuracy=0.95, sample_count=1)
    trusts = []
    for n in (1, 7, 9, 10, 20):
        _set_market_sample_count(n)
        trusts.append(svc.compute_trust("BasketballEngine", "nba").trust)

    assert trusts == sorted(trusts), f"trust fell as evidence accumulated: {trusts}"
    # Below MIN the market channel is silent, so all three equal Phase 3 alone.
    assert trusts[0] == pytest.approx(0.72)
    assert trusts[1] == pytest.approx(0.72)
    assert trusts[2] == pytest.approx(0.72)
    # At MIN it starts contributing: w2 = 10 / 30.
    assert trusts[3] == pytest.approx((20 / 30) * 0.72 + (10 / 30) * 0.95)


def test_dormant_source_does_not_flatter_a_bad_engine(kernel_db):
    """The distortion also ran upward, which is the worse half.

    An engine measured at 0.20 over 20 samples was pulled up to 0.2931 by a
    9-sample dormant market row — a 47% relative inflation of the trust that
    gates its edges.
    """
    _seed_phase3_calibration(avg_accuracy=0.20, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.95, sample_count=9)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.trust == pytest.approx(0.20)
    assert result.source == "phase3_only"


def test_both_sources_dormant_is_dormant_not_an_average_of_sentinels(kernel_db):
    """Neither source qualifies -> no usable signal, so report dormant."""
    _seed_phase3_calibration(avg_accuracy=0.90, sample_count=2)
    _seed_market_calibration(direction_accuracy=0.95, sample_count=2)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "dormant"
    assert result.trust == pytest.approx(0.5)
    assert result.phase3_weight == pytest.approx(0.0)
    assert result.market_weight == pytest.approx(0.0)


def test_market_only_qualified_reports_market_only(kernel_db):
    """Mirror of the phase3 case: label follows whichever channel qualified."""
    _seed_phase3_calibration(avg_accuracy=0.90, sample_count=2)  # dormant
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "market_only"
    assert result.trust == pytest.approx(0.80)
    assert result.market_weight == pytest.approx(1.0)


def test_an_unreadable_market_row_is_not_reported_as_a_dormant_channel(kernel_db):
    """A failed calibration read must not become the dormant sentinel.

    ``get_calibrations`` used to swallow query failures into ``[]``, which
    ``compute_trust`` converts to ``market_has_data = False`` — and the dormant
    value is a sentinel meaning "no usable estimate", not a measurement. So an
    estimate that existed and was merely unreadable was reported as one that had
    never been made, with ``source`` still naming a channel as the basis.

    Measured with Phase 3 below its MIN, so the market channel is the only
    qualified signal: a channel measured at 1.00/n=12 reported trust 0.50
    ``phase3_only``, and one measured at 0.167 also reported 0.50 — so
    ``adjusted_edge = raw_edge * trust * liquidity`` halved for the good engine
    and tripled for the bad one.
    """
    from sqlalchemy import text
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=3)  # dormant
    _seed_market_calibration(direction_accuracy=1.0, sample_count=12)
    svc = CalibrationFusionService()
    healthy = svc.compute_trust("BasketballEngine", "nba")
    assert healthy.trust == pytest.approx(1.0)
    assert healthy.source == "market_only"

    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_market_calibrations"))
        session.commit()
    finally:
        session.close()

    with pytest.raises(OperationalError, match="no such table"):
        svc.compute_trust("BasketballEngine", "nba")


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
