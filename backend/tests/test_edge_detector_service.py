"""Tests for EdgeDetectorService (domain service).

Covers: skip cases, single/multi source aggregation, trust computation,
liquidity factor ramp, staleness, binary-sport skip, persistence, top
discrepancies ordering.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelPrediction,
    KernelCalibration,
    get_kernel_session,
)
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.edge_detector_service import (
    EdgeDetectorService,
    EdgeResult,
    EdgeDetectionSummary,
)


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_service_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def service(kernel_db):
    return EdgeDetectorService()


def _utcnow():
    return datetime.now(timezone.utc)


def _seed_prediction(
    match_id="m1",
    engine="BasketballEngine",
    competition="nba",
    probs=None,
    ts=None,
):
    """Insert a KernelPrediction row."""
    if probs is None:
        probs = {"home_win": 0.6, "away_win": 0.4}
    if ts is None:
        ts = _utcnow()
    session = get_kernel_session()
    try:
        row = KernelPrediction(
            match_id=match_id,
            sport="basketball",
            competition=competition,
            season="2025-26",
            engine=engine,
            predicted_scores={},
            outcome_probabilities=probs,
            confidence=0.7,
            feature_version="nba-1.0",
            explanation={},
            created_at=ts,
            updated_at=ts,
        )
        session.add(row)
        session.commit()
    finally:
        session.close()


def _seed_link_and_snapshot(
    match_id="m1",
    contract_id="c1",
    source="polymarket",
    mapped_outcome="home_win",
    implied_prob=0.55,
    liquidity=None,
    verified=True,
    snap_ts=None,
):
    """Insert a verified link + its latest snapshot. Returns the link dict."""
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id=contract_id, source=source,
        outcome_label="YES", mapped_outcome=mapped_outcome, link_method="rule",
        link_confidence=0.95, verified=verified, market_question="q",
        implied_prob=implied_prob,
    )
    if verified and snap_ts is not None:
        snap_store = MarketSnapshotStore()
        snap_store.append_snapshot(
            link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
            liquidity=liquidity, volume=None, captured_at=snap_ts,
        )
    elif verified:
        # Default: snapshot with same implied_prob, no liquidity
        snap_store = MarketSnapshotStore()
        snap_store.append_snapshot(
            link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
            liquidity=liquidity, volume=None, captured_at=_utcnow(),
        )
    return link


def _seed_calibration(engine="BasketballEngine", competition="nba",
                      avg_accuracy=0.72, sample_count=20):
    session = get_kernel_session()
    try:
        row = KernelCalibration(
            engine=engine, competition=competition, slope=1.0, intercept=0.0,
            sample_count=sample_count, avg_confidence=0.65,
            avg_accuracy=avg_accuracy, last_updated=_utcnow(),
        )
        session.add(row)
        session.commit()
    finally:
        session.close()


def test_detect_edges_no_prediction_returns_skipped(service):
    """Match with no KernelPrediction -> skipped=True, skip_reason='no_prediction'."""
    _seed_link_and_snapshot(match_id="m1")
    result = service.detect_edges("m1")
    assert isinstance(result, EdgeDetectionSummary)
    assert result.skipped is True
    assert result.skip_reason == "no_prediction"
    assert result.outcomes == []


def test_detect_edges_no_verified_links_returns_skipped(service):
    """Match with prediction but no verified links -> skipped='no_verified_links'."""
    _seed_prediction(match_id="m1")
    # Unverified link only
    _seed_link_and_snapshot(match_id="m1", verified=False)
    result = service.detect_edges("m1")
    assert result.skipped is True
    assert result.skip_reason == "no_verified_links"
    assert result.outcomes == []


def test_detect_edges_single_outcome_single_source(service):
    """One outcome, one link -> correct raw_edge, market_prob, adjusted_edge."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.65, "away_win": 0.35})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.58)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.skipped is False
    # Only home_win has a verified link; away_win is skipped
    assert len(result.outcomes) == 1
    edge = result.outcomes[0]
    assert edge.mapped_outcome == "home_win"
    assert edge.model_prob == pytest.approx(0.65)
    assert edge.market_prob == pytest.approx(0.58)
    assert edge.raw_edge == pytest.approx(0.07)
    assert edge.trust == pytest.approx(0.72)
    # liquidity=None -> liquidity_factor=1.0
    assert edge.liquidity_factor == pytest.approx(1.0)
    # adjusted_edge = 0.07 * 0.72 * 1.0
    assert edge.adjusted_edge == pytest.approx(0.0504, abs=1e-4)
    assert edge.sources_count == 1
    assert edge.spread is None
    assert edge.stale is False


def test_detect_edges_multi_source_liquidity_weighted(service):
    """Two links for same outcome with different liquidities -> weighted avg."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.65})
    ts = _utcnow()
    # Link 1: implied=0.55, liquidity=1000 -> weight=1000
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c1", mapped_outcome="home_win",
        implied_prob=0.55, liquidity=1000.0, snap_ts=ts,
    )
    # Link 2: implied=0.60, liquidity=3000 -> weight=3000
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c2", mapped_outcome="home_win",
        implied_prob=0.60, liquidity=3000.0, snap_ts=ts,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    # market_prob = (0.55*1000 + 0.60*3000) / (1000+3000) = (550+1800)/4000 = 0.5875
    assert edge.market_prob == pytest.approx(0.5875, abs=1e-4)
    assert edge.sources_count == 2
    # liquidity_factor = min(max(1000,3000)/5000, 1.0) = 3000/5000 = 0.6
    assert edge.liquidity_factor == pytest.approx(0.6)


def test_detect_edges_traditional_odds_no_liquidity_uses_weight_1(service):
    """liquidity=None -> weight=1, liquidity_factor=1.0."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c1", source="the_odds_api",
        mapped_outcome="home_win", implied_prob=0.58, liquidity=None,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.market_prob == pytest.approx(0.58)
    assert edge.liquidity_factor == pytest.approx(1.0)


def test_detect_edges_trust_cold_start(service):
    """No KernelCalibration row -> trust=0.5 (DIAGNOSIS_DORMANT_TRUST)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    # No _seed_calibration call -> cold start
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.5)


def test_detect_edges_trust_dormant(service):
    """sample_count < 8 -> trust=0.5 (dormant)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=5)  # < 8 -> dormant
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.5)


def test_detect_edges_trust_qualified(service):
    """sample_count >= 8 -> trust=clamp(avg_accuracy, 0.1, 1.0)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.72)


def test_detect_edges_trust_qualified_floor(service):
    """avg_accuracy below floor -> trust=floor (0.1)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.05, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.1)


def test_detect_edges_liquidity_factor_ramp(service):
    """liquidity=2500 -> factor=0.5; 5000 -> 1.0; 10000 -> 1.0 (clamped).

    Uses distinct match_ids in the same fixture DB (no DB reset needed).
    """
    # 2500 -> 0.5 (2500/5000)
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m1", mapped_outcome="home_win", implied_prob=0.55, liquidity=2500.0,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].liquidity_factor == pytest.approx(0.5)

    # 5000 -> 1.0 (5000/5000, clamped to 1.0)
    _seed_prediction(match_id="m2", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m2", mapped_outcome="home_win", implied_prob=0.55, liquidity=5000.0,
    )
    result2 = service.detect_edges("m2")
    assert result2.outcomes[0].liquidity_factor == pytest.approx(1.0)

    # 10000 -> 1.0 (clamped, > floor)
    _seed_prediction(match_id="m3", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m3", mapped_outcome="home_win", implied_prob=0.55, liquidity=10000.0,
    )
    result3 = service.detect_edges("m3")
    assert result3.outcomes[0].liquidity_factor == pytest.approx(1.0)


def test_detect_edges_stale_when_prediction_old(service):
    """prediction_timestamp 100h old -> stale=True."""
    old_ts = _utcnow() - timedelta(hours=100)
    _seed_prediction(match_id="m1", probs={"home_win": 0.6}, ts=old_ts)
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].stale is True


def test_detect_edges_stale_when_all_snapshots_old(service):
    """All snapshots 100h old -> stale=True (prediction is fresh)."""
    fresh_ts = _utcnow()
    _seed_prediction(match_id="m1", probs={"home_win": 0.6}, ts=fresh_ts)
    old_snap_ts = _utcnow() - timedelta(hours=100)
    _seed_link_and_snapshot(
        match_id="m1", mapped_outcome="home_win", implied_prob=0.55, snap_ts=old_snap_ts,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].stale is True


def test_detect_edges_not_stale_when_one_snapshot_fresh(service):
    """One snapshot 1h old, another 100h old -> stale=False (newest is fresh)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    # Link 1: old snapshot
    old_ts = _utcnow() - timedelta(hours=100)
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c1", mapped_outcome="home_win",
        implied_prob=0.55, snap_ts=old_ts,
    )
    # Link 2: fresh snapshot
    fresh_ts = _utcnow() - timedelta(hours=1)
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c2", mapped_outcome="home_win",
        implied_prob=0.57, snap_ts=fresh_ts,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].stale is False


def test_detect_edges_binary_sport_skips_missing_outcome(service):
    """outcome_probabilities has away_win but no verified link for it -> skipped."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6, "away_win": 0.4})
    # Only home_win has a link
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    # Only home_win edge computed; away_win skipped
    assert len(result.outcomes) == 1
    assert result.outcomes[0].mapped_outcome == "home_win"


def test_detect_edges_persists_to_edge_store(service):
    """After detect_edges, get_latest_edges returns the computed edges."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.65})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.58)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    service.detect_edges("m1")
    latest = service.get_latest_edges("m1")
    assert len(latest) == 1
    assert latest[0].model_prob == pytest.approx(0.65)
    assert latest[0].raw_edge == pytest.approx(0.07)


def test_get_top_discrepancies_orders_by_abs_adjusted_edge_desc(service):
    """Multiple matches -> ordered by |adjusted_edge| DESC."""
    # m1: small edge
    _seed_prediction(match_id="m1", probs={"home_win": 0.55})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.54)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    service.detect_edges("m1")

    # m2: large edge
    _seed_prediction(match_id="m2", probs={"home_win": 0.70})
    _seed_link_and_snapshot(match_id="m2", mapped_outcome="home_win", implied_prob=0.50)
    service.detect_edges("m2")

    top = service.get_top_discrepancies(limit=20)
    assert len(top) == 2
    # m2 has larger |adjusted_edge|
    assert abs(top[0].adjusted_edge) >= abs(top[1].adjusted_edge)
    assert top[0].match_id == "m2"
