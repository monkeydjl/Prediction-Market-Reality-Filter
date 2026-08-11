"""Tests for MarketSettlementService and pure helper functions.

Covers: _compute_brier, _compute_signed_error, _compute_direction_correct,
_update_market_calibration (regression fitting), and DB-integrated
process_settlement / scan_and_process / read methods.
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.kernel.market_settlement_service import (
    _compute_brier,
    _compute_signed_error,
    _compute_direction_correct,
    _update_market_calibration,
    MarketSettlementService,
    SettlementResult,
    ScanResult,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

def test_compute_brier_zero_when_equal():
    assert _compute_brier(0.65, 0.65) == 0.0


def test_compute_brier_positive_when_different():
    # (0.7 - 0.5)^2 = 0.04
    assert _compute_brier(0.7, 0.5) == pytest.approx(0.04)


def test_compute_brier_max_when_extremes():
    # (1.0 - 0.0)^2 = 1.0
    assert _compute_brier(1.0, 0.0) == pytest.approx(1.0)


def test_compute_signed_error_positive():
    assert _compute_signed_error(0.7, 0.5) == pytest.approx(0.2)


def test_compute_signed_error_negative():
    assert _compute_signed_error(0.3, 0.6) == pytest.approx(-0.3)


def test_compute_signed_error_zero_when_equal():
    assert _compute_signed_error(0.5, 0.5) == pytest.approx(0.0)


def test_direction_correct_both_positive():
    # raw_edge > 0 (model > market), settlement > market → market moved up → correct
    assert _compute_direction_correct(raw_edge=0.1, market_prob=0.5, settlement_implied_prob=0.9) == 1


def test_direction_correct_both_negative():
    # raw_edge < 0 (model < market), settlement < market → market moved down → correct
    assert _compute_direction_correct(raw_edge=-0.1, market_prob=0.6, settlement_implied_prob=0.3) == 1


def test_direction_correct_mismatched():
    # raw_edge > 0 (model > market), settlement < market → market moved down → wrong
    assert _compute_direction_correct(raw_edge=0.1, market_prob=0.5, settlement_implied_prob=0.3) == 0


def test_direction_correct_zero_edge():
    # raw_edge == 0 → edge_sign == 0 → not correct (no directional bet)
    assert _compute_direction_correct(raw_edge=0.0, market_prob=0.5, settlement_implied_prob=0.9) == 0


def test_direction_correct_zero_market_move():
    # settlement == market → market_sign == 0 → not correct
    assert _compute_direction_correct(raw_edge=0.1, market_prob=0.5, settlement_implied_prob=0.5) == 0


def test_update_market_calibration_insufficient_samples(tmp_path, monkeypatch):
    """When < MIN_SAMPLES_FOR_MARKET_CALIBRATION settlements, no calibration row written."""
    from app.core import config
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    close_kernel_db()
    init_kernel_db(str(tmp_path / "test_cal.db"))
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    from app.kernel.market_settlement_store import MarketSettlementStore
    store = MarketSettlementStore()
    # Insert only 3 settlements — below threshold
    for i in range(3):
        store.append_settlement(
            match_id=f"m{i}", mapped_outcome="home_win", engine="BasketballEngine",
            competition="nba", settlement_implied_prob=0.7, settlement_captured_at=_utcnow(),
            link_id=1, model_prob=0.65, market_prob_at_detection=0.6, raw_edge=0.05,
            adjusted_edge=0.04, brier_score=0.0025, signed_error=0.05, direction_correct=1,
            status="processed", skip_reason=None, match_finished_at=_utcnow(), processed_at=_utcnow(),
        )
    _update_market_calibration(store, "BasketballEngine", "nba")
    cals = store.get_calibrations(engine="BasketballEngine", competition="nba")
    assert len(cals) == 0  # no calibration written
    close_kernel_db()


def test_update_market_calibration_sufficient_samples(tmp_path, monkeypatch):
    """When >= MIN_SAMPLES settlements, calibration row is written with regression."""
    from app.core import config
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    close_kernel_db()
    init_kernel_db(str(tmp_path / "test_cal2.db"))
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 5)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    from app.kernel.market_settlement_store import MarketSettlementStore
    store = MarketSettlementStore()
    # Insert 5 settlements with perfect linear relationship: y = x (slope=1, intercept=0)
    for i in range(5):
        model_p = 0.4 + i * 0.1  # 0.4, 0.5, 0.6, 0.7, 0.8
        settlement_p = model_p  # perfect calibration
        store.append_settlement(
            match_id=f"m{i}", mapped_outcome="home_win", engine="BasketballEngine",
            competition="nba", settlement_implied_prob=settlement_p,
            settlement_captured_at=_utcnow(), link_id=1, model_prob=model_p,
            market_prob_at_detection=0.5, raw_edge=model_p - 0.5,
            adjusted_edge=model_p - 0.5, brier_score=0.0, signed_error=0.0,
            direction_correct=1, status="processed", skip_reason=None,
            match_finished_at=_utcnow(), processed_at=_utcnow(),
        )
    _update_market_calibration(store, "BasketballEngine", "nba")
    cals = store.get_calibrations(engine="BasketballEngine", competition="nba")
    assert len(cals) == 1
    cal = cals[0]
    assert cal["sample_count"] == 5
    assert cal["slope"] == pytest.approx(1.0, abs=0.01)
    assert cal["intercept"] == pytest.approx(0.0, abs=0.01)
    assert cal["avg_brier"] == pytest.approx(0.0, abs=0.001)
    assert cal["direction_accuracy"] == pytest.approx(1.0, abs=0.01)
    close_kernel_db()


def test_update_market_calibration_slope_clamped(tmp_path, monkeypatch):
    """Slope is clamped to [0.0, 2.0]."""
    from app.core import config
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    close_kernel_db()
    init_kernel_db(str(tmp_path / "test_cal3.db"))
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 3)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    from app.kernel.market_settlement_store import MarketSettlementStore
    store = MarketSettlementStore()
    # Steep relationship: y = 5x → slope=5, should be clamped to 2.0
    for i in range(3):
        model_p = 0.1 + i * 0.1
        settlement_p = 5 * model_p
        store.append_settlement(
            match_id=f"m{i}", mapped_outcome="home_win", engine="TestEngine",
            competition="test", settlement_implied_prob=settlement_p,
            settlement_captured_at=_utcnow(), link_id=1, model_prob=model_p,
            market_prob_at_detection=0.5, raw_edge=model_p - 0.5,
            adjusted_edge=model_p - 0.5, brier_score=0.0, signed_error=0.0,
            direction_correct=1, status="processed", skip_reason=None,
            match_finished_at=_utcnow(), processed_at=_utcnow(),
        )
    _update_market_calibration(store, "TestEngine", "test")
    cals = store.get_calibrations(engine="TestEngine", competition="test")
    assert len(cals) == 1
    assert cals[0]["slope"] == pytest.approx(2.0, abs=0.01)  # clamped
    close_kernel_db()


# ---------------------------------------------------------------------------
# DB-integrated tests
# ---------------------------------------------------------------------------

@pytest.fixture
def kernel_db(tmp_path):
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    db_path = tmp_path / "settlement_service_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed_prediction(match_id="m1", engine="BasketballEngine", competition="nba", probs=None):
    """Seed a KernelPrediction row."""
    from app.kernel.kernel_db import KernelPrediction, get_kernel_session
    if probs is None:
        probs = {"home_win": 0.65, "away_win": 0.35}
    now = _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition=competition,
            season="2025-26", engine=engine, predicted_scores={},
            outcome_probabilities=probs, confidence=0.7, feature_version="nba-1.0",
            explanation={}, created_at=now, updated_at=now,
        ))
        session.commit()
    finally:
        session.close()


def _seed_outcome(match_id="m1", finished_at=None, outcome="home_win"):
    """Seed a KernelMatchOutcome row."""
    from app.kernel.kernel_db import KernelMatchOutcome, get_kernel_session
    now = finished_at or _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=2, away_score=1, outcome=outcome,
            engine=None, score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=now, created_at=now,
        ))
        session.commit()
    finally:
        session.close()


def _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.6):
    """Seed a verified market link + snapshot via A's public API.

    Snapshot is backdated 1s so it precedes the match's finished_at (which is
    set to _utcnow() in the test). Without this, the microsecond gap between
    setting finished_at and creating the snapshot causes the snapshot to be
    excluded by the `captured_at <= finished_at` filter.
    """
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    now = _utcnow() - timedelta(seconds=1)
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome=mapped_outcome, link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=implied_prob,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
        liquidity=None, volume=None, captured_at=now,
    )
    return link


def _seed_edge(match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.6):
    """Seed a B edge via EdgeStore.append_edge."""
    from app.kernel.edge_store import EdgeStore
    raw_edge = model_prob - market_prob
    adjusted_edge = raw_edge * 0.8
    store = EdgeStore()
    store.append_edge(
        match_id=match_id, mapped_outcome=mapped_outcome,
        model_prob=model_prob, market_prob=market_prob, raw_edge=raw_edge,
        trust=0.8, liquidity_factor=1.0, adjusted_edge=adjusted_edge,
        spread=None, sources_count=1, stale=False,
    )


def test_process_settlement_happy_path(kernel_db, monkeypatch):
    """Finished match + verified link + snapshot + edge → settlement row written."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    finished = _utcnow()
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1", finished_at=finished)
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.9)
    _seed_edge(match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.6)

    svc = MarketSettlementService()
    result = svc.process_settlement("m1")
    assert result.status == "processed"
    assert result.settlements_count == 1

    settlements = svc.get_settlement("m1")
    assert len(settlements) == 1
    s = settlements[0]
    assert s["mapped_outcome"] == "home_win"
    assert s["engine"] == "BasketballEngine"
    assert s["competition"] == "nba"
    assert s["settlement_implied_prob"] == pytest.approx(0.9)
    assert s["model_prob"] == pytest.approx(0.65)
    assert s["brier_score"] == pytest.approx((0.65 - 0.9) ** 2)
    assert s["signed_error"] == pytest.approx(0.65 - 0.9)
    assert s["direction_correct"] == 1  # raw_edge=0.05>0, settlement(0.9)>market(0.6) → both positive
    assert s["status"] == "processed"


def test_process_settlement_idempotent(kernel_db):
    """Re-processing returns already_processed without writing duplicates."""
    finished = _utcnow()
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1", finished_at=finished)
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.9)
    _seed_edge(match_id="m1", mapped_outcome="home_win")

    svc = MarketSettlementService()
    result1 = svc.process_settlement("m1")
    assert result1.status == "processed"
    result2 = svc.process_settlement("m1")
    assert result2.status == "already_processed"
    assert result2.settlements_count == 0

    settlements = svc.get_settlement("m1")
    assert len(settlements) == 1  # no duplicate


def test_process_settlement_skipped_not_finished(kernel_db):
    """Match without outcome → skipped_not_finished."""
    _seed_prediction(match_id="m2")
    # No outcome seeded
    svc = MarketSettlementService()
    result = svc.process_settlement("m2")
    assert result.status == "skipped_not_finished"
    assert result.settlements_count == 0


def test_process_settlement_skipped_no_edges(kernel_db):
    """Finished match but no B edges → skipped_no_edges."""
    finished = _utcnow()
    _seed_prediction(match_id="m3")
    _seed_outcome(match_id="m3", finished_at=finished)
    # No edges seeded
    svc = MarketSettlementService()
    result = svc.process_settlement("m3")
    assert result.status == "skipped_no_edges"


def test_process_settlement_skipped_no_links(kernel_db):
    """Finished match + edges but no verified links → skipped_no_links settlement row."""
    finished = _utcnow()
    _seed_prediction(match_id="m4")
    _seed_outcome(match_id="m4", finished_at=finished)
    _seed_edge(match_id="m4", mapped_outcome="home_win")
    # No verified link seeded
    svc = MarketSettlementService()
    result = svc.process_settlement("m4")
    assert result.status == "processed"  # process_settlement itself succeeds
    assert result.settlements_count == 1  # one skipped settlement row written
    settlements = svc.get_settlement("m4")
    assert settlements[0]["status"] == "skipped_no_links"
    assert settlements[0]["brier_score"] is None


def test_process_settlement_skipped_no_snapshot(kernel_db):
    """Finished match + edges + verified link but no snapshot before finished_at → skipped."""
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    finished = _utcnow() - timedelta(hours=2)  # finished 2 hours ago
    _seed_prediction(match_id="m5")
    _seed_outcome(match_id="m5", finished_at=finished)
    _seed_edge(match_id="m5", mapped_outcome="home_win")
    # Seed link but snapshot is AFTER finished_at
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id="m5", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.6, price=0.6,
        liquidity=None, volume=None, captured_at=_utcnow(),  # NOW, after finished_at
    )
    svc = MarketSettlementService()
    result = svc.process_settlement("m5")
    assert result.status == "processed"
    settlements = svc.get_settlement("m5")
    assert settlements[0]["status"] == "skipped_no_snapshot"
    assert settlements[0]["brier_score"] is None


def test_scan_and_process_batch(kernel_db, monkeypatch):
    """scan_and_process processes multiple finished matches."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    for i in range(3):
        mid = f"batch_{i}"
        _seed_prediction(match_id=mid)
        _seed_outcome(match_id=mid)
        _seed_verified_link(match_id=mid, mapped_outcome="home_win", implied_prob=0.7)
        _seed_edge(match_id=mid, mapped_outcome="home_win")
    svc = MarketSettlementService()
    result = svc.scan_and_process(limit=10)
    assert result.scanned == 3
    assert result.processed == 3
    assert result.errors == 0


def test_scan_and_process_skips_already_processed(kernel_db, monkeypatch):
    """scan_and_process doesn't re-process already-settled matches."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")
    svc = MarketSettlementService()
    # First scan processes it
    result1 = svc.scan_and_process(limit=10)
    assert result1.processed == 1
    # Second scan finds nothing new
    result2 = svc.scan_and_process(limit=10)
    assert result2.scanned == 0
    assert result2.processed == 0


def test_get_calibrations_empty(kernel_db):
    """get_calibrations returns empty list when no calibrations exist."""
    svc = MarketSettlementService()
    cals = svc.get_calibrations()
    assert cals == []


def test_get_calibrations_after_processing(kernel_db, monkeypatch):
    """After processing enough settlements, calibration row appears."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 2)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    for i in range(2):
        mid = f"cal_{i}"
        _seed_prediction(match_id=mid)
        _seed_outcome(match_id=mid)
        _seed_verified_link(match_id=mid, mapped_outcome="home_win", implied_prob=0.7)
        _seed_edge(match_id=mid, mapped_outcome="home_win", model_prob=0.65, market_prob=0.6)
    svc = MarketSettlementService()
    svc.scan_and_process(limit=10)
    cals = svc.get_calibrations(engine="BasketballEngine", competition="nba")
    assert len(cals) == 1
    assert cals[0]["sample_count"] == 2


def test_get_history(kernel_db, monkeypatch):
    """get_history returns recent settlements."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")
    svc = MarketSettlementService()
    svc.process_settlement("m1")
    history = svc.get_history(limit=10)
    assert len(history) == 1
    assert history[0]["match_id"] == "m1"


def test_get_history_filtered_by_engine(kernel_db, monkeypatch):
    """get_history filters by engine."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1", engine="BasketballEngine")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")
    svc = MarketSettlementService()
    svc.process_settlement("m1")
    history = svc.get_history(limit=10, engine="BasketballEngine")
    assert len(history) == 1
    history_other = svc.get_history(limit=10, engine="OtherEngine")
    assert len(history_other) == 0


def test_link_lookup_failure_does_not_write_permanent_skip(kernel_db, monkeypatch):
    """A DB error during link lookup must not settle the match as "no links".

    ``skipped_no_links`` is a final verdict: matches with settlement rows are
    excluded from the next scan, so recording a transient failure that way
    would permanently drop the match. The error must propagate instead.
    """
    from app.core import config
    from app.kernel import market_settlement_service as mss
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")

    def _boom(match_id, mapped_outcome):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mss, "_find_verified_link_for_outcome", _boom)
    svc = MarketSettlementService()

    with pytest.raises(RuntimeError, match="database is locked"):
        svc.process_settlement("m1")

    # No settlement row was written, so the match stays eligible for a retry.
    assert svc.get_settlement("m1") == []

    # And scan_and_process counts it as an error rather than a skip.
    monkeypatch.setattr(mss, "_find_verified_link_for_outcome", _boom)
    result = svc.scan_and_process(limit=10)
    assert result.errors == 1
    assert result.skipped == 0
    assert any("database is locked" in d for d in result.error_details)


def test_find_verified_link_raises_instead_of_returning_none(kernel_db, monkeypatch):
    """The helper itself must not swallow query errors into a None.

    None means "no such link" and is acted on as a final verdict; a failed
    query must be distinguishable from it.
    """
    from app.kernel import market_settlement_service as mss

    class _BrokenSession:
        def query(self, *a, **k):
            raise RuntimeError("connection reset")

        def close(self):
            self.closed = True

    broken = _BrokenSession()
    monkeypatch.setattr(mss, "get_kernel_session", lambda: broken)

    with pytest.raises(RuntimeError, match="connection reset"):
        mss._find_verified_link_for_outcome("m1", "home_win")

    # The session is still released on the error path.
    assert broken.closed is True


def test_find_verified_link_returns_none_when_absent(kernel_db):
    """A genuinely missing link is still None (not an error)."""
    from app.kernel import market_settlement_service as mss
    assert mss._find_verified_link_for_outcome("no-such-match", "home_win") is None


def test_snapshot_lookup_failure_does_not_write_permanent_skip(kernel_db, monkeypatch):
    """A DB error during snapshot lookup must not settle the match as "no snapshot".

    Same trap as the link lookup above: ``skipped_no_snapshot`` is a final
    verdict and excludes the match from later scans, so a transient failure
    recorded that way permanently drops it.
    """
    from app.core import config
    from app.kernel import market_settlement_service as mss
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")

    def _boom(link_id, finished_at):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mss, "_find_settlement_snapshot", _boom)
    svc = MarketSettlementService()

    with pytest.raises(RuntimeError, match="database is locked"):
        svc.process_settlement("m1")

    # No settlement row was written, so the match stays eligible for a retry.
    assert svc.get_settlement("m1") == []

    monkeypatch.setattr(mss, "_find_settlement_snapshot", _boom)
    result = svc.scan_and_process(limit=10)
    assert result.errors == 1
    assert result.skipped == 0
    assert any("database is locked" in d for d in result.error_details)


def test_find_settlement_snapshot_raises_instead_of_returning_none(kernel_db, monkeypatch):
    """The helper itself must not swallow query errors into a None."""
    from app.kernel import market_settlement_service as mss

    class _BrokenSession:
        def query(self, *a, **k):
            raise RuntimeError("connection reset")

        def close(self):
            self.closed = True

    broken = _BrokenSession()
    monkeypatch.setattr(mss, "get_kernel_session", lambda: broken)

    with pytest.raises(RuntimeError, match="connection reset"):
        mss._find_settlement_snapshot(1, _utcnow())

    # The session is still released on the error path.
    assert broken.closed is True


def test_find_settlement_snapshot_returns_none_when_absent(kernel_db):
    """A genuinely missing snapshot is still None (not an error)."""
    from app.kernel import market_settlement_service as mss
    assert mss._find_settlement_snapshot(99999, _utcnow()) is None
