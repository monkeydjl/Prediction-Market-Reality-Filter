from pathlib import Path

import pytest

from app.core import config
from app.kernel.kernel_db import (
    close_kernel_session,
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
