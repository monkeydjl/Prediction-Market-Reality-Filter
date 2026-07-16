"""Tests for sport_recommendation_cli.

Follows test_sport_edge_cli.py pattern — tests main() function directly.
"""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db, get_kernel_session
from app.kernel.kernel_db import KernelPrediction, KernelCalibration
from app.kernel.edge_store import EdgeStore
from datetime import datetime, timezone


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    db_path = tmp_path / "rec_cli_test.db"
    # Patch the DB path before init_kernel_db is called by the CLI
    monkeypatch.setenv("KERNEL_DB_PATH", str(db_path))
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed_data(match_id="m1"):
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
        existing = session.query(KernelCalibration).filter_by(
            engine="BasketballEngine", competition="nba"
        ).one_or_none()
        if existing is None:
            session.add(KernelCalibration(
                engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
                sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
            ))
        session.commit()
    finally:
        session.close()
    EdgeStore().append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=0.50, raw_edge=0.15, trust=0.72,
        liquidity_factor=1.0, adjusted_edge=0.108, spread=None,
        sources_count=1, stale=False, captured_at=now,
    )


def test_cli_match_command(kernel_db):
    _seed_data("m1")
    from scripts.sport_recommendation_cli import main
    code = main(["match", "--match-id", "m1"])
    assert code == 0


def test_cli_open_command(kernel_db):
    _seed_data("m1")
    from scripts.sport_recommendation_cli import main
    code = main(["open", "--limit", "10"])
    assert code == 0


def test_cli_picks_command(kernel_db):
    _seed_data("m1")
    from scripts.sport_recommendation_cli import main
    code = main(["picks", "--limit", "10"])
    assert code == 0
