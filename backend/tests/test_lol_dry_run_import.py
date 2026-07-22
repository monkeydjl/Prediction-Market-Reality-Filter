from pathlib import Path

import pytest

from app.kernel.kernel_db import (
    KernelMatchFixture,
    close_kernel_session,
    get_kernel_session,
    init_kernel_db,
)

SAMPLE_PATH = Path(__file__).resolve().parent / "fixtures" / "lol" / "sample_series.json"


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test_lol_dry_run.db")
    init_kernel_db(db_path)
    yield
    close_kernel_session()


def test_import_lol_series_file_upserts_fixture(db):
    from app.sports.lol.dry_run_import import import_lol_series_file

    count = import_lol_series_file(SAMPLE_PATH)
    assert count == 1

    session = get_kernel_session()
    try:
        fixture = session.get(KernelMatchFixture, "lol-dry-lck-001")
        assert fixture is not None
        assert fixture.home_team == "T1"
        assert fixture.away_team == "Gen.G"
        assert fixture.competition == "lol_lck"
        assert fixture.stage == "regular"
        assert fixture.status == "scheduled"
        assert fixture.venue == "Bo3"
    finally:
        session.close()


def test_import_lol_series_file_is_idempotent(db):
    from app.sports.lol.dry_run_import import import_lol_series_file

    assert import_lol_series_file(SAMPLE_PATH) == 1
    assert import_lol_series_file(SAMPLE_PATH) == 1

    session = get_kernel_session()
    try:
        rows = session.query(KernelMatchFixture).filter_by(match_id="lol-dry-lck-001").all()
        assert len(rows) == 1
        assert rows[0].home_team == "T1"
    finally:
        session.close()
