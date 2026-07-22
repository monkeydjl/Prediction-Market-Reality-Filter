from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core import config
from app.kernel.kernel_db import (
    KernelMatchResult,
    close_kernel_session,
    get_kernel_session,
    init_kernel_db,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter
from app.sports.lol.dry_run_import import import_lol_series_file
from app.sports.lol.source import NullLolScheduleSource

SAMPLE_PATH = Path(__file__).resolve().parent / "fixtures" / "lol" / "sample_series.json"


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test_lol_adapter.db")
    init_kernel_db(db_path)
    yield
    close_kernel_session()


@pytest.fixture
def restore_lol_settings():
    prev_import = config.settings.LOL_DRY_RUN_IMPORT
    prev_path = config.settings.LOL_DRY_RUN_FIXTURES_PATH
    yield
    config.settings.LOL_DRY_RUN_IMPORT = prev_import
    config.settings.LOL_DRY_RUN_FIXTURES_PATH = prev_path


def test_lol_adapter_is_data_adapter():
    from app.sports.lol.lol_adapter import LolAdapter

    assert isinstance(LolAdapter(), DataAdapter)


def test_fetch_schedule_empty_when_no_fixtures(db):
    from app.sports.lol.lol_adapter import LolAdapter

    rows = LolAdapter().fetch_schedule(ScheduleFilter())
    assert rows == []


def test_fetch_schedule_after_dry_run_import(db, restore_lol_settings):
    from app.sports.lol.lol_adapter import LolAdapter

    import_lol_series_file(SAMPLE_PATH)
    rows = LolAdapter().fetch_schedule(ScheduleFilter())
    assert len(rows) == 1
    assert rows[0].match.match_id.startswith("lol-")
    assert rows[0].match.home.name == "T1"
    assert rows[0].raw_json.get("best_of") == 3


def test_get_match_identity_after_import(db):
    from app.sports.lol.lol_adapter import LolAdapter

    import_lol_series_file(SAMPLE_PATH)
    identity = LolAdapter().get_match_identity("lol-dry-lck-001")
    assert identity.match_id == "lol-dry-lck-001"
    assert identity.home.name == "T1"
    assert identity.away.name == "Gen.G"
    assert identity.season.competition.sport.code == "lol"
    assert identity.season.competition.code == "lol_lck"


def test_sync_schedule_null_source_dry_run_off_returns_zero(db, restore_lol_settings):
    from app.sports.lol.lol_adapter import LolAdapter

    config.settings.LOL_DRY_RUN_IMPORT = False
    adapter = LolAdapter(source=NullLolScheduleSource())
    assert adapter.sync_schedule() == 0


def test_sync_schedule_dry_run_import_returns_at_least_one(db, restore_lol_settings):
    from app.sports.lol.lol_adapter import LolAdapter

    config.settings.LOL_DRY_RUN_IMPORT = True
    config.settings.LOL_DRY_RUN_FIXTURES_PATH = str(SAMPLE_PATH)
    n = LolAdapter().sync_schedule()
    assert n >= 1


def test_build_match_outcome_equal_or_missing_scores_returns_none():
    from app.sports.lol.lol_adapter import build_match_outcome

    tied = MagicMock()
    tied.match_id = "lol-dry-lck-001"
    tied.home_score = 0
    tied.away_score = 0
    tied.finished_at = None
    assert build_match_outcome(tied) is None

    unfinished = MagicMock()
    unfinished.match_id = "lol-dry-lck-001"
    unfinished.home_score = None
    unfinished.away_score = None
    unfinished.finished_at = None
    assert build_match_outcome(unfinished) is None


def test_fetch_outcome_synthetic_settlement_sample(db):
    """SYNTHETIC dry-run settle sample (ADR-004 P8) — not a real match."""
    from app.sports.lol.lol_adapter import LolAdapter

    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(
            KernelMatchResult(
                match_id="lol-dry-lck-001",
                home_score=2,
                away_score=1,
                outcome="home_win",
                finished_at=now,
                created_at=now,
            )
        )
        session.commit()
    finally:
        session.close()

    outcome = LolAdapter().fetch_outcome("lol-dry-lck-001")
    assert outcome is not None
    assert outcome.match_id == "lol-dry-lck-001"
    assert outcome.home_score == 2
    assert outcome.away_score == 1
    assert outcome.outcome == "home_win"

    session = get_kernel_session()
    try:
        session.merge(
            KernelMatchResult(
                match_id="lol-dry-lck-001",
                home_score=1,
                away_score=2,
                outcome="away_win",
                finished_at=now,
                created_at=now,
            )
        )
        session.commit()
    finally:
        session.close()

    away = LolAdapter().fetch_outcome("lol-dry-lck-001")
    assert away is not None
    assert away.home_score == 1
    assert away.away_score == 2
    assert away.outcome == "away_win"
