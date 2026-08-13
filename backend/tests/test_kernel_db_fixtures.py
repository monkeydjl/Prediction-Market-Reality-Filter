# backend/tests/test_kernel_db_fixtures.py
"""Tests for kernel DB fixture/result/club_elo_cache tables."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db, get_kernel_session, close_kernel_session,
    KernelMatchFixture, KernelMatchResult, KernelClubEloCache,
)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield
    close_kernel_session()


class TestKernelMatchFixture:
    def test_create_and_read(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        fixture = KernelMatchFixture(
            match_id="ucl-537327",
            competition="ucl",
            season="2025-26",
            home_team="Real Madrid CF",
            away_team="FC Bayern München",
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
            stage="group_stage",
            status="scheduled",
            home_score=None,
            away_score=None,
            venue="Santiago Bernabéu",
            created_at=now,
            updated_at=now,
        )
        session.add(fixture)
        session.commit()

        fetched = session.get(KernelMatchFixture, "ucl-537327")
        assert fetched is not None
        assert fetched.competition == "ucl"
        assert fetched.home_team == "Real Madrid CF"
        assert fetched.stage == "group_stage"
        session.close()

    def test_update_score_on_finished(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        fixture = KernelMatchFixture(
            match_id="epl-123456",
            competition="epl",
            season="2025-26",
            home_team="Arsenal FC",
            away_team="Chelsea FC",
            kickoff_utc=datetime(2025, 8, 16, 15, 0, tzinfo=timezone.utc),
            stage="regular_season",
            status="scheduled",
            venue="Emirates Stadium",
            created_at=now,
            updated_at=now,
        )
        session.add(fixture)
        session.commit()

        fixture.home_score = 2
        fixture.away_score = 1
        fixture.status = "finished"
        session.commit()

        fetched = session.get(KernelMatchFixture, "epl-123456")
        assert fetched.home_score == 2
        assert fetched.away_score == 1
        assert fetched.status == "finished"
        session.close()


class TestKernelMatchResult:
    def test_create_and_read(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        result = KernelMatchResult(
            match_id="ucl-537327",
            home_score=3,
            away_score=1,
            outcome="home_win",
            finished_at=datetime(2025, 9, 16, 22, 0, tzinfo=timezone.utc),
            created_at=now,
        )
        session.add(result)
        session.commit()

        fetched = session.get(KernelMatchResult, "ucl-537327")
        assert fetched is not None
        assert fetched.home_score == 3
        assert fetched.outcome == "home_win"
        session.close()


class TestKernelClubEloCache:
    def test_create_and_read(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        entry = KernelClubEloCache(
            team_name="arsenal",
            elo_rating=2063.76,
            source="clubelo",
            fetched_at=now,
            country="ENG",
            level=1,
        )
        session.add(entry)
        session.commit()

        fetched = session.get(KernelClubEloCache, "arsenal")
        assert fetched is not None
        assert fetched.elo_rating == 2063.76
        assert fetched.country == "ENG"
        session.close()

    def test_update_existing(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        entry = KernelClubEloCache(
            team_name="mancity",
            elo_rating=1950.0,
            source="clubelo",
            fetched_at=now,
            country="ENG",
            level=1,
        )
        session.add(entry)
        session.commit()

        entry.elo_rating = 1970.85
        session.commit()

        fetched = session.get(KernelClubEloCache, "mancity")
        assert fetched.elo_rating == 1970.85
        session.close()


class TestGetKernelSession:
    def test_missing_factory_raises_named_error(self, monkeypatch):
        """A half-initialized module says so instead of "NoneType is not callable".

        init_kernel_db() returns early once _engine is set, so if a first init
        raised after create_engine (a failing dormant-table migration, say) the
        session factory stays None for the life of the process.
        """
        import app.kernel.kernel_db as kdb

        monkeypatch.setattr(kdb, "_engine", object())
        monkeypatch.setattr(kdb, "_SessionLocal", None)
        with pytest.raises(RuntimeError, match="session factory is unset"):
            kdb.get_kernel_session()
