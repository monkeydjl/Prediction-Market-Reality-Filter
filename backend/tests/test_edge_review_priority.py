"""Edge review_priority hygiene (P1-O3)."""
from app.kernel.edge_detector_service import EdgeDetectorService


def test_critical_when_large_edge_and_stale():
    svc = EdgeDetectorService()
    assert (
        svc._review_priority(
            adjusted_edge=0.15,
            stale=True,
            liquidity_factor=1.0,
            sources_count=2,
            trust=0.8,
        )
        == "critical"
    )


def test_high_for_large_fresh_edge():
    svc = EdgeDetectorService()
    assert (
        svc._review_priority(
            adjusted_edge=0.11,
            stale=False,
            liquidity_factor=1.0,
            sources_count=2,
            trust=0.8,
        )
        == "high"
    )


def test_low_for_small_healthy_edge():
    svc = EdgeDetectorService()
    assert (
        svc._review_priority(
            adjusted_edge=0.01,
            stale=False,
            liquidity_factor=1.0,
            sources_count=2,
            trust=0.8,
        )
        == "low"
    )


def test_normal_default_band():
    svc = EdgeDetectorService()
    assert (
        svc._review_priority(
            adjusted_edge=0.05,
            stale=False,
            liquidity_factor=1.0,
            sources_count=2,
            trust=0.8,
        )
        == "normal"
    )
