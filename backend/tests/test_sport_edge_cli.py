"""Tests for sport_edge_cli (manual edge computation and inspection)."""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.kernel_db import KernelPrediction, KernelCalibration, get_kernel_session
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_cli_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed(match_id="m1", implied=0.58):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
            confidence=0.7, feature_version="nba-1.0", explanation={},
            created_at=now, updated_at=now,
        ))
        # Calibration row is keyed by (engine, competition) — only insert if
        # not already present (helper may be called multiple times per test).
        existing_cal = (
            session.query(KernelCalibration)
            .filter_by(engine="BasketballEngine", competition="nba")
            .one_or_none()
        )
        if existing_cal is None:
            session.add(KernelCalibration(
                engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
                sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
            ))
        session.commit()
    finally:
        session.close()
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=implied,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied, price=implied,
        liquidity=None, volume=None, captured_at=now,
    )


def test_cli_detect(kernel_db, capsys):
    """detect subcommand -> exit 0, edge persisted."""
    _seed(match_id="m1")
    from scripts.sport_edge_cli import main
    rc = main(["detect", "--match-id", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "m1" in out


def test_cli_latest(kernel_db, capsys):
    """latest subcommand -> exit 0, output contains edge data."""
    _seed(match_id="m1")
    from scripts.sport_edge_cli import main
    main(["detect", "--match-id", "m1"])  # compute first
    capsys.readouterr()  # clear
    rc = main(["latest", "--match-id", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "home_win" in out


def test_cli_discrepancies(kernel_db, capsys):
    """discrepancies subcommand -> exit 0."""
    _seed(match_id="m1")
    from scripts.sport_edge_cli import main
    main(["detect", "--match-id", "m1"])  # compute first
    capsys.readouterr()  # clear
    rc = main(["discrepancies"])
    assert rc == 0
