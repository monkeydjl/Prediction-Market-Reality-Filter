"""End-to-end integration tests for Phase 7 Subprojects A→B→C→D.

Verifies the full data flow:
  A (Sport Market Bridge) → B (Edge Detector) → C (Recommendation) → D (Settlement Feedback)

These tests seed real data through the actual service interfaces and verify
that each subproject's output correctly feeds into the next, and that D's
settlement feedback loop closes properly with calibration regression.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelPrediction,
    KernelCalibration,
    KernelMatchOutcome,
    get_kernel_session,
)
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.edge_detector_service import EdgeDetectorService
from app.kernel.sport_recommendation_service import SportRecommendationService
from app.kernel.market_settlement_service import MarketSettlementService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "e2e_integration_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _seed_prediction(
    match_id: str,
    sport: str = "basketball",
    competition: str = "nba",
    engine: str = "BasketballEngine",
    probs: dict | None = None,
    ts: datetime | None = None,
) -> datetime:
    """Insert a KernelPrediction row. Returns the prediction timestamp."""
    if probs is None:
        probs = {"home_win": 0.65, "away_win": 0.35}
    if ts is None:
        ts = _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport=sport, competition=competition,
            season="2025-26", engine=engine, predicted_scores={},
            outcome_probabilities=probs, confidence=0.7,
            feature_version="nba-1.0", explanation={},
            created_at=ts, updated_at=ts,
        ))
        session.commit()
    finally:
        session.close()
    return ts


def _seed_calibration(
    engine: str = "BasketballEngine",
    competition: str = "nba",
    avg_accuracy: float = 0.72,
    sample_count: int = 20,
) -> None:
    """Insert a KernelCalibration row for trust computation."""
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


def _seed_outcome(
    match_id: str,
    outcome: str,
    finished_at: datetime,
    engine: str = "BasketballEngine",
    home_score: int = 110,
    away_score: int = 105,
) -> None:
    """Insert a KernelMatchOutcome row."""
    session = get_kernel_session()
    try:
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=home_score, away_score=away_score,
            outcome=outcome, engine=engine, score_mae=5.0,
            outcome_correct=1 if "win" in outcome else 0,
            brier_score=0.15, finished_at=finished_at, created_at=_utcnow(),
        ))
        session.commit()
    finally:
        session.close()


def _seed_verified_link_with_snapshot(
    match_id: str,
    mapped_outcome: str,
    implied_prob: float,
    captured_at: datetime,
    contract_id: str = "c1",
    liquidity: float | None = None,
) -> dict:
    """Create a verified market link + snapshot. Returns link dict."""
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id=contract_id, source="polymarket",
        outcome_label="YES", mapped_outcome=mapped_outcome, link_method="rule",
        link_confidence=0.95, verified=True, market_question="q",
        implied_prob=implied_prob,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
        liquidity=liquidity, volume=None, captured_at=captured_at,
    )
    return link


# ---------------------------------------------------------------------------
# Test 1: Happy path — basketball binary outcome full flow
# ---------------------------------------------------------------------------

def test_e2e_happy_path_basketball(kernel_db):
    """Full A→B→C→D flow for a binary basketball match.

    Story:
    - Model predicts home_win=0.65, away_win=0.35
    - Market implies home_win=0.58 (model sees 7pp edge)
    - B detects edge, C recommends YES on home_win
    - Match ends, home_win confirmed
    - Settlement snapshot shows market moved to 0.90 (confirming home_win)
    - D computes brier, direction_correct, updates calibration
    """
    t0 = _utcnow()

    # --- Seed: Prediction + Calibration ---
    _seed_prediction(
        match_id="nba-2026-g1",
        probs={"home_win": 0.65, "away_win": 0.35},
        ts=t0,
    )
    _seed_calibration(engine="BasketballEngine", competition="nba")

    # --- A: Sport Market Bridge — verified link + detection snapshot ---
    detection_prob = 0.58
    link = _seed_verified_link_with_snapshot(
        match_id="nba-2026-g1",
        mapped_outcome="home_win",
        implied_prob=detection_prob,
        captured_at=t0,
    )

    # --- B: Edge Detector — compute and persist edges ---
    edge_svc = EdgeDetectorService()
    edge_summary = edge_svc.detect_edges("nba-2026-g1")

    assert edge_summary.skipped is False
    assert edge_summary.engine_name == "BasketballEngine"
    assert edge_summary.competition == "nba"
    assert len(edge_summary.outcomes) == 1

    home_edge = edge_summary.outcomes[0]
    assert home_edge.mapped_outcome == "home_win"
    assert home_edge.model_prob == pytest.approx(0.65)
    assert home_edge.market_prob == pytest.approx(0.58)
    assert home_edge.raw_edge == pytest.approx(0.07)
    # adjusted_edge = raw_edge * trust * liquidity_factor
    # trust = 0.72 (avg_accuracy), liquidity_factor = 1.0 (no liquidity data → no penalty)
    assert home_edge.trust == pytest.approx(0.72)
    assert home_edge.adjusted_edge == pytest.approx(0.07 * 0.72 * 1.0)

    # Verify edge persisted to DB
    persisted = edge_svc.get_latest_edges("nba-2026-g1")
    assert len(persisted) == 1
    assert persisted[0].raw_edge == pytest.approx(0.07)

    # --- C: Sport Recommendation — generate actionable recommendation ---
    rec_svc = SportRecommendationService()
    rec = rec_svc.get_recommendation("nba-2026-g1")

    assert rec is not None
    assert rec.match_id == "nba-2026-g1"
    assert rec.direction == "YES"  # raw_edge > 0
    # adjusted_edge_pct = 5.04pp < DECISION_ACT_EDGE (6.0) → watch (positive but below threshold)
    assert rec.decision == "watch"
    assert rec.engine_name == "BasketballEngine"
    assert rec.competition == "nba"
    assert rec.model_prob == pytest.approx(0.65)
    assert rec.market_prob == pytest.approx(0.58)

    # --- A: Settlement snapshot — market moved to 0.90 after match ---
    settlement_prob = 0.90
    settlement_time = t0 + timedelta(hours=1)
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=settlement_prob,
        price=settlement_prob, liquidity=5000.0, volume=10000.0,
        captured_at=settlement_time,
    )

    # --- Seed: Match Outcome ---
    finished_at = t0 + timedelta(hours=2)
    _seed_outcome(
        match_id="nba-2026-g1", outcome="home_win", finished_at=finished_at,
    )

    # --- D: Market Settlement Feedback — compute settlement + calibration ---
    settlement_svc = MarketSettlementService()
    result = settlement_svc.process_settlement("nba-2026-g1")

    assert result.status == "processed"
    assert result.settlements_count == 1

    # Verify settlement record
    settlements = settlement_svc.get_settlement("nba-2026-g1")
    assert len(settlements) == 1
    s = settlements[0]
    assert s["mapped_outcome"] == "home_win"
    assert s["status"] == "processed"
    assert s["settlement_implied_prob"] == pytest.approx(0.90)
    assert s["model_prob"] == pytest.approx(0.65)
    assert s["market_prob_at_detection"] == pytest.approx(0.58)
    assert s["raw_edge"] == pytest.approx(0.07)
    # brier = (0.65 - 0.90)^2 = 0.0625
    assert s["brier_score"] == pytest.approx(0.0625)
    # signed_error = 0.65 - 0.90 = -0.25
    assert s["signed_error"] == pytest.approx(-0.25)
    # direction_correct: raw_edge > 0, settlement - market = 0.90 - 0.58 = 0.32 > 0 → 1
    assert s["direction_correct"] == 1

    # Calibration NOT updated yet (only 1 sample < MIN_SAMPLES_FOR_MARKET_CALIBRATION=10)
    calibrations = settlement_svc.get_calibrations()
    assert len(calibrations) == 0


# ---------------------------------------------------------------------------
# Test 2: Multi-outcome football match (3-way: home_win/draw/away_win)
# ---------------------------------------------------------------------------

def test_e2e_multi_outcome_football(kernel_db):
    """Full A→B→C→D flow for a 3-way football match.

    Verifies that multiple outcomes (home_win/draw/away_win) each get
    their own edge, and D processes all outcomes.
    """
    t0 = _utcnow()
    probs = {"home_win": 0.45, "draw": 0.28, "away_win": 0.27}

    _seed_prediction(
        match_id="epl-2026-g1",
        sport="football", competition="epl",
        engine="IntegratedEngine", probs=probs, ts=t0,
    )
    _seed_calibration(
        engine="IntegratedEngine", competition="epl", avg_accuracy=0.68,
    )

    # A: Create verified links for all 3 outcomes
    for outcome, market_prob in [("home_win", 0.50), ("draw", 0.25), ("away_win", 0.30)]:
        _seed_verified_link_with_snapshot(
            match_id="epl-2026-g1",
            mapped_outcome=outcome,
            implied_prob=market_prob,
            captured_at=t0,
            contract_id=f"c_{outcome}",
        )

    # B: Detect edges for all outcomes
    edge_svc = EdgeDetectorService()
    summary = edge_svc.detect_edges("epl-2026-g1")
    assert summary.skipped is False
    assert len(summary.outcomes) == 3

    edges_by_outcome = {e.mapped_outcome: e for e in summary.outcomes}
    # home_win: model 0.45, market 0.50 → raw_edge = -0.05 (model underestimates)
    assert edges_by_outcome["home_win"].raw_edge == pytest.approx(-0.05)
    # draw: model 0.28, market 0.25 → raw_edge = +0.03
    assert edges_by_outcome["draw"].raw_edge == pytest.approx(0.03)
    # away_win: model 0.27, market 0.30 → raw_edge = -0.03
    assert edges_by_outcome["away_win"].raw_edge == pytest.approx(-0.03)

    # C: Recommendation picks primary outcome (max |adjusted_edge|)
    rec_svc = SportRecommendationService()
    rec = rec_svc.get_recommendation("epl-2026-g1")
    assert rec is not None
    # Primary outcome should be home_win (largest |raw_edge| = 0.05)
    assert rec.mapped_outcome == "home_win"
    assert rec.direction == "NO"  # raw_edge < 0

    # D: Settlement — match ended as away_win
    finished_at = t0 + timedelta(hours=3)
    _seed_outcome(
        match_id="epl-2026-g1", outcome="away_win", finished_at=finished_at,
        engine="IntegratedEngine", home_score=1, away_score=2,
    )

    # Add settlement snapshots (market moved to final prices)
    link_store = SportMarketLinkStore()
    links = link_store.get_verified_links(match_id="epl-2026-g1")
    snap_store = MarketSnapshotStore()
    settlement_prices = {"home_win": 0.05, "draw": 0.15, "away_win": 0.85}
    for link in links:
        outcome = link["mapped_outcome"]
        snap_store.append_snapshot(
            link_id=link["id"],
            implied_prob=settlement_prices[outcome],
            price=settlement_prices[outcome],
            captured_at=finished_at - timedelta(seconds=1),
        )

    settlement_svc = MarketSettlementService()
    result = settlement_svc.process_settlement("epl-2026-g1")
    assert result.status == "processed"
    assert result.settlements_count == 3  # all 3 outcomes processed

    settlements = settlement_svc.get_settlement("epl-2026-g1")
    assert len(settlements) == 3
    for s in settlements:
        assert s["status"] == "processed"
        assert s["brier_score"] is not None


# ---------------------------------------------------------------------------
# Test 3: Settlement calibration regression after multiple matches
# ---------------------------------------------------------------------------

def test_e2e_settlement_updates_market_calibration(kernel_db, monkeypatch):
    """After processing enough matches, D updates kernel_market_calibrations.

    Requires MIN_SAMPLES_FOR_MARKET_CALIBRATION matches to trigger
    calibration regression. We lower the threshold via monkeypatch.
    """
    monkeypatch.setattr(
        "app.core.config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION", 3
    )
    monkeypatch.setattr(
        "app.core.config.settings.MARKET_CALIBRATION_WINDOW_SIZE", 10
    )

    t0 = _utcnow()
    _seed_calibration(engine="BasketballEngine", competition="nba")

    # Process 3 matches with consistent bias:
    # model always says 0.60, market says 0.55, settlement says 0.60
    # → slope should be ~1.0, intercept ~0.0
    for i in range(3):
        mid = f"nba-2026-cal-{i}"
        _seed_prediction(match_id=mid, probs={"home_win": 0.60, "away_win": 0.40}, ts=t0)
        link = _seed_verified_link_with_snapshot(
            match_id=mid, mapped_outcome="home_win",
            implied_prob=0.55, captured_at=t0,
        )
        # B: detect edges
        EdgeDetectorService().detect_edges(mid)

        # Settlement snapshot at 0.60
        MarketSnapshotStore().append_snapshot(
            link_id=link["id"], implied_prob=0.60, price=0.60,
            captured_at=t0 + timedelta(hours=1),
        )
        finished_at = t0 + timedelta(hours=2)
        _seed_outcome(match_id=mid, outcome="home_win", finished_at=finished_at)

        # D: process settlement
        result = MarketSettlementService().process_settlement(mid)
        assert result.status == "processed"

    # Verify calibration was computed
    settlement_svc = MarketSettlementService()
    calibrations = settlement_svc.get_calibrations(
        engine="BasketballEngine", competition="nba"
    )
    assert len(calibrations) == 1
    cal = calibrations[0]
    assert cal["engine"] == "BasketballEngine"
    assert cal["competition"] == "nba"
    assert cal["sample_count"] == 3
    # model=0.60, settlement=0.60 → perfect calibration
    # slope=1.0, intercept=0.0
    assert cal["slope"] == pytest.approx(1.0, abs=0.01)
    assert cal["intercept"] == pytest.approx(0.0, abs=0.01)
    # brier = (0.60 - 0.60)^2 = 0 for all → avg_brier = 0
    assert cal["avg_brier"] == pytest.approx(0.0, abs=0.001)
    # direction: raw_edge = 0.60 - 0.55 = 0.05 > 0
    # settlement - market = 0.60 - 0.55 = 0.05 > 0 → correct
    assert cal["direction_accuracy"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test 4: Idempotency + scan_and_process batch
# ---------------------------------------------------------------------------

def test_e2e_idempotency_and_scan(kernel_db):
    """D's process_settlement is idempotent; scan_and_process finds unprocessed."""
    t0 = _utcnow()
    _seed_calibration(engine="BasketballEngine", competition="nba")

    # Seed 2 matches
    for i in range(2):
        mid = f"nba-2026-scan-{i}"
        _seed_prediction(match_id=mid, probs={"home_win": 0.60, "away_win": 0.40}, ts=t0)
        link = _seed_verified_link_with_snapshot(
            match_id=mid, mapped_outcome="home_win",
            implied_prob=0.55, captured_at=t0,
        )
        EdgeDetectorService().detect_edges(mid)
        MarketSnapshotStore().append_snapshot(
            link_id=link["id"], implied_prob=0.58, price=0.58,
            captured_at=t0 + timedelta(hours=1),
        )
        _seed_outcome(
            match_id=mid, outcome="home_win",
            finished_at=t0 + timedelta(hours=2),
        )

    # D: Process match 1 directly
    svc = MarketSettlementService()
    result1 = svc.process_settlement("nba-2026-scan-0")
    assert result1.status == "processed"

    # Idempotency: re-process match 1
    result1_again = svc.process_settlement("nba-2026-scan-0")
    assert result1_again.status == "already_processed"
    assert result1_again.settlements_count == 0

    # scan_and_process: finds only match 1 (match 0 already has settlements,
    # so _find_finished_matches_without_settlements excludes it)
    scan_result = svc.scan_and_process(limit=50)
    assert scan_result.scanned == 1  # only match 1 (match 0 excluded)
    assert scan_result.processed == 1  # match 1 processed
    assert scan_result.already_processed == 0
    assert scan_result.errors == 0

    # Second scan: no unprocessed matches remain
    scan_result2 = svc.scan_and_process(limit=50)
    assert scan_result2.scanned == 0
    assert scan_result2.processed == 0
    assert scan_result2.already_processed == 0
