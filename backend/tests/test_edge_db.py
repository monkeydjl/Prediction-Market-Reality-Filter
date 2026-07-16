"""Tests for KernelSportEdge table and get_calibration accessor."""
import pytest

from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelCalibration,
    KernelSportEdge,
    get_calibration,
    get_kernel_session,
)
from datetime import datetime


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_db_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def test_phased_edge_detector_enabled_defaults_off():
    """Global constraint 1: flag must default to OFF."""
    from app.core import config
    # Re-read settings (already loaded at module import, but we check the value)
    assert config.settings.PHASE7_EDGE_DETECTOR_ENABLED is False
    assert config.settings.EDGE_DETECTION_INTERVAL_MIN == 5


def test_get_calibration_returns_none_when_no_row(kernel_db):
    """Cold start: no calibration row -> None."""
    result = get_calibration("BasketballEngine", "nba")
    assert result is None


def test_get_calibration_returns_row_when_present(kernel_db):
    """Qualified: calibration row exists -> returned."""
    from datetime import timezone
    session = get_kernel_session()
    try:
        row = KernelCalibration(
            engine="BasketballEngine",
            competition="nba",
            slope=1.0,
            intercept=0.0,
            sample_count=20,
            avg_confidence=0.65,
            avg_accuracy=0.72,
            last_updated=datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
    finally:
        session.close()

    result = get_calibration("BasketballEngine", "nba")
    assert result is not None
    assert result.avg_accuracy == pytest.approx(0.72)
    assert result.sample_count == 20
