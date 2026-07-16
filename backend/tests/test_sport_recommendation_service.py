"""Tests for SportRecommendationService (stateless recommendation engine).

Covers: direction derivation, risk/confidence/allocation computation,
primary outcome selection, unit conversion (0-1 → 0-100), qualified
determination, get_recommendation, get_open_decisions, get_top_picks.
"""
from datetime import datetime, timezone

import pytest

from app.core import config
from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelPrediction,
    KernelCalibration,
    get_kernel_session,
)
from app.kernel.edge_store import EdgeStore
from app.kernel.sport_recommendation_service import (
    SportActionableRecommendation,
    SportRecommendationService,
    _derive_direction,
    _compute_risk_level,
    _compute_confidence,
    _compute_allocation,
    _build_rationale,
    _select_primary_outcome,
)


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "rec_service_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def service(kernel_db):
    return SportRecommendationService()


def _utcnow():
    return datetime.now(timezone.utc)


def _seed_prediction(match_id="m1", engine="BasketballEngine", competition="nba", probs=None):
    if probs is None:
        probs = {"home_win": 0.6, "away_win": 0.4}
    ts = _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition=competition,
            season="2025-26", engine=engine, predicted_scores={},
            outcome_probabilities=probs, confidence=0.7, feature_version="nba-1.0",
            explanation={}, created_at=ts, updated_at=ts,
        ))
        session.commit()
    finally:
        session.close()


def _seed_calibration(engine="BasketballEngine", competition="nba", sample_count=20, avg_accuracy=0.72):
    """Insert calibration row. Idempotent — checks before inserting."""
    session = get_kernel_session()
    try:
        existing = session.query(KernelCalibration).filter_by(
            engine=engine, competition=competition
        ).one_or_none()
        if existing is None:
            session.add(KernelCalibration(
                engine=engine, competition=competition, slope=1.0, intercept=0.0,
                sample_count=sample_count, avg_confidence=0.65,
                avg_accuracy=avg_accuracy, last_updated=_utcnow(),
            ))
            session.commit()
    finally:
        session.close()


def _seed_edge(match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.55,
               raw_edge=0.10, trust=0.72, liquidity_factor=1.0, adjusted_edge=0.072,
               stale=False, captured_at=None):
    """Insert an edge row directly via EdgeStore."""
    store = EdgeStore()
    return store.append_edge(
        match_id=match_id, mapped_outcome=mapped_outcome,
        model_prob=model_prob, market_prob=market_prob,
        raw_edge=raw_edge, trust=trust, liquidity_factor=liquidity_factor,
        adjusted_edge=adjusted_edge, spread=None, sources_count=1,
        stale=stale, captured_at=captured_at or _utcnow(),
    )


# --- Pure function tests (no DB needed) ---

def test_direction_yes_when_raw_edge_positive():
    assert _derive_direction(raw_edge=0.05, stale=False, risk_level="low") == "YES"


def test_direction_no_when_raw_edge_negative():
    assert _derive_direction(raw_edge=-0.05, stale=False, risk_level="low") == "NO"


def test_direction_wait_when_stale():
    assert _derive_direction(raw_edge=0.05, stale=True, risk_level="low") == "WAIT"


def test_direction_avoid_when_high_risk():
    assert _derive_direction(raw_edge=0.05, stale=False, risk_level="high") == "AVOID"


def test_direction_wait_when_raw_edge_zero():
    assert _derive_direction(raw_edge=0.0, stale=False, risk_level="low") == "WAIT"


def test_risk_level_high_when_stale():
    assert _compute_risk_level(liquidity_factor=1.0, trust=0.9, stale=True) == "high"


def test_risk_level_high_when_low_liquidity():
    assert _compute_risk_level(liquidity_factor=0.1, trust=0.9, stale=False) == "high"


def test_risk_level_high_when_low_trust():
    assert _compute_risk_level(liquidity_factor=1.0, trust=0.1, stale=False) == "high"


def test_risk_level_medium():
    assert _compute_risk_level(liquidity_factor=0.4, trust=0.9, stale=False) == "medium"


def test_risk_level_low():
    assert _compute_risk_level(liquidity_factor=0.8, trust=0.8, stale=False) == "low"


def test_confidence_high():
    # 6pp * 0.67 trust = 4.02 >= 4.0
    assert _compute_confidence(adjusted_edge_pct=6.0, trust=0.67) == "high"


def test_confidence_medium():
    # 4pp * 0.5 trust = 2.0 >= 2.0
    assert _compute_confidence(adjusted_edge_pct=4.0, trust=0.5) == "medium"


def test_confidence_low():
    assert _compute_confidence(adjusted_edge_pct=1.0, trust=0.5) == "low"


def test_allocation_zero_when_skip():
    assert _compute_allocation(adjusted_edge_pct=10.0, risk_level="low", decision="skip") == 0.0


def test_allocation_zero_when_high_risk():
    assert _compute_allocation(adjusted_edge_pct=10.0, risk_level="high", decision="act") == 0.0


def test_allocation_capped_at_2():
    # 12pp / 6pp = 2.0, capped at 2.0
    result = _compute_allocation(adjusted_edge_pct=12.0, risk_level="low", decision="act")
    assert result == 2.0


def test_allocation_halved_for_medium_risk():
    # 6pp / 6pp = 1.0, * 0.5 = 0.5
    result = _compute_allocation(adjusted_edge_pct=6.0, risk_level="medium", decision="act")
    assert result == 0.5


def test_rationale_contains_outcome_and_edge():
    rationale = _build_rationale(
        direction="YES", mapped_outcome="home_win", edge_pct=7.2,
        trust=0.72, liquidity_factor=0.8, stale=False, decision="act",
    )
    assert "主胜" in rationale
    assert "7.20" in rationale or "7.2" in rationale
    assert "act" in rationale
    assert "仅供参考" in rationale


def test_rationale_stale_message():
    rationale = _build_rationale(
        direction="WAIT", mapped_outcome="home_win", edge_pct=0.0,
        trust=0.5, liquidity_factor=0.5, stale=True, decision="watch",
    )
    assert "数据过期" in rationale


def test_select_primary_outcome_picks_max_abs_adjusted_edge():
    edges = [
        {"mapped_outcome": "home_win", "adjusted_edge": 0.03},
        {"mapped_outcome": "away_win", "adjusted_edge": -0.08},
        {"mapped_outcome": "draw", "adjusted_edge": 0.01},
    ]
    primary = _select_primary_outcome(edges)
    assert primary["mapped_outcome"] == "away_win"


def test_select_primary_outcome_empty_returns_none():
    assert _select_primary_outcome([]) is None


# --- DB-integrated tests (require kernel_db fixture) ---

def test_get_recommendation_returns_none_when_no_edges(service):
    assert service.get_recommendation("nonexistent") is None


def test_get_recommendation_returns_rec(service):
    _seed_prediction(match_id="m1")
    _seed_calibration()
    _seed_edge(match_id="m1", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    rec = service.get_recommendation("m1")
    assert rec is not None
    assert rec.match_id == "m1"
    assert rec.mapped_outcome == "home_win"
    assert rec.direction == "YES"
    assert rec.edge_pct == pytest.approx(7.2, abs=0.01)
    assert rec.engine_name == "BasketballEngine"
    assert rec.competition == "nba"
    assert rec.calibration_status == "calibrated"


def test_get_recommendation_qualified_false_when_no_calibration(service):
    _seed_prediction(match_id="m2", engine="NewEngine", competition="new")
    # No calibration row for NewEngine/new
    _seed_edge(match_id="m2", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.05)
    rec = service.get_recommendation("m2")
    assert rec is not None
    assert rec.calibration_status == "uncalibrated_provisional"
    assert rec.decision in ("provisional_act", "watch")  # cold_start_bypass


def test_get_recommendation_stale_returns_wait(service):
    _seed_prediction(match_id="m3")
    _seed_calibration()
    _seed_edge(match_id="m3", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072, stale=True)
    rec = service.get_recommendation("m3")
    assert rec is not None
    assert rec.direction == "WAIT"
    assert "数据过期" in rec.rationale


def test_get_recommendation_negative_edge_returns_no(service):
    _seed_prediction(match_id="m4", probs={"home_win": 0.4, "away_win": 0.6})
    _seed_calibration()
    _seed_edge(match_id="m4", mapped_outcome="home_win", raw_edge=-0.10, adjusted_edge=-0.072)
    rec = service.get_recommendation("m4")
    assert rec is not None
    assert rec.direction == "NO"


def test_get_recommendation_picks_primary_outcome(service):
    """When multiple outcomes exist, picks the one with max |adjusted_edge|."""
    _seed_prediction(match_id="m5", probs={"home_win": 0.6, "away_win": 0.4})
    _seed_calibration()
    _seed_edge(match_id="m5", mapped_outcome="home_win", raw_edge=0.03, adjusted_edge=0.022)
    _seed_edge(match_id="m5", mapped_outcome="away_win", raw_edge=-0.08, adjusted_edge=-0.058)
    rec = service.get_recommendation("m5")
    assert rec is not None
    assert rec.mapped_outcome == "away_win"  # larger |adjusted_edge|


def test_get_open_decisions_excludes_skip(service):
    _seed_prediction(match_id="m6")
    _seed_calibration()
    # Small edge → skip decision
    _seed_edge(match_id="m6", mapped_outcome="home_win", raw_edge=0.001, adjusted_edge=0.0007)
    recs = service.get_open_decisions(limit=10)
    match_ids = [r.match_id for r in recs]
    assert "m6" not in match_ids  # skip excluded


def test_get_open_decisions_includes_act(service):
    _seed_prediction(match_id="m7")
    _seed_calibration()
    # Large edge → act decision (0.10 * 0.72 * 1.0 = 0.072 → 7.2pp >= 6.0)
    _seed_edge(match_id="m7", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    recs = service.get_open_decisions(limit=10)
    match_ids = [r.match_id for r in recs]
    assert "m7" in match_ids


def test_get_open_decisions_filters_by_decision(service):
    _seed_prediction(match_id="m8")
    _seed_calibration()
    _seed_edge(match_id="m8", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    recs = service.get_open_decisions(limit=10, decision="act")
    assert all(r.decision == "act" for r in recs)
    assert any(r.match_id == "m8" for r in recs)


def test_get_top_picks_includes_skip(service):
    _seed_prediction(match_id="m9")
    _seed_calibration()
    _seed_edge(match_id="m9", mapped_outcome="home_win", raw_edge=0.001, adjusted_edge=0.0007)
    recs = service.get_top_picks(limit=10)
    match_ids = [r.match_id for r in recs]
    assert "m9" in match_ids  # includes skip decisions


def test_get_top_picks_respects_min_abs_edge(service):
    _seed_prediction(match_id="m10")
    _seed_calibration()
    _seed_edge(match_id="m10", mapped_outcome="home_win", raw_edge=0.001, adjusted_edge=0.0007)
    # min_abs_edge_pct=1.0 means adjusted_edge >= 0.01 (0.01 * 100 = 1.0pp)
    recs = service.get_top_picks(limit=10, min_abs_edge_pct=1.0)
    match_ids = [r.match_id for r in recs]
    assert "m10" not in match_ids  # 0.0007 < 0.01


def test_get_open_decisions_deduplicates_by_match(service):
    """Multiple outcomes for same match → only 1 recommendation per match."""
    _seed_prediction(match_id="m11", probs={"home_win": 0.6, "away_win": 0.4})
    _seed_calibration()
    _seed_edge(match_id="m11", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    _seed_edge(match_id="m11", mapped_outcome="away_win", raw_edge=-0.08, adjusted_edge=-0.058)
    recs = service.get_open_decisions(limit=10)
    match_count = sum(1 for r in recs if r.match_id == "m11")
    assert match_count == 1
