"""Tests for club_form.team_form_from_kernel."""
from datetime import datetime, timezone

from app.kernel.kernel_db import (
    KernelMatchFixture,
    KernelMatchResult,
    close_kernel_session,
    get_kernel_session,
    init_kernel_db,
)
from app.sports.football.club_form import team_form_from_kernel


def _seed_matches(tmp_path):
    close_kernel_session()
    init_kernel_db(str(tmp_path / "kernel.db"))
    session = get_kernel_session()
    try:
        fixtures = [
            KernelMatchFixture(
                match_id="epl-1",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 9, 1, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="epl-2",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Liverpool",
                away_team="Arsenal",
                kickoff_utc=datetime(2025, 9, 10, tzinfo=timezone.utc),
            ),
        ]
        results = [
            KernelMatchResult(
                match_id="epl-1",
                home_score=2,
                away_score=1,
                finished_at=datetime(2025, 9, 1, 22, tzinfo=timezone.utc),
            ),
            KernelMatchResult(
                match_id="epl-2",
                home_score=0,
                away_score=0,
                finished_at=datetime(2025, 9, 10, 22, tzinfo=timezone.utc),
            ),
        ]
        for row in fixtures + results:
            session.add(row)
        session.commit()
    finally:
        session.close()


def test_team_form_from_kernel(tmp_path):
    _seed_matches(tmp_path)
    try:
        before = datetime(2025, 9, 20, tzinfo=timezone.utc)
        stats = team_form_from_kernel("Arsenal", competition="epl", before=before)
        assert stats is not None
        assert stats["played"] == 2
        assert stats["wins"] == 1  # beat Chelsea
        assert stats["draws"] == 1  # drew at Liverpool
        assert stats["losses"] == 0
        assert stats["last_match_date"] == "2025-09-10"
        assert stats["data_source"] == "kernel_match_results"
    finally:
        close_kernel_session()


def test_team_form_unknown(tmp_path):
    _seed_matches(tmp_path)
    try:
        stats = team_form_from_kernel(
            "NotATeam",
            competition="epl",
            before=datetime(2025, 10, 1, tzinfo=timezone.utc),
        )
        assert stats is None
    finally:
        close_kernel_session()
