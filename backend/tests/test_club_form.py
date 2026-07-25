"""Tests for club_form.team_form_from_kernel."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    KernelMatchFixture,
    KernelMatchResult,
    close_kernel_session,
    get_kernel_session,
    init_kernel_db,
)
from app.sports.football.club_form import (
    h2h_from_kernel,
    points_form_rate,
    team_form_from_kernel,
)


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


class TestPointsFormRate:
    def test_all_wins(self):
        assert points_form_rate(10, 0, 10) == pytest.approx(1.0)

    def test_all_draws(self):
        assert points_form_rate(0, 10, 10) == pytest.approx(0.3333)

    def test_all_losses(self):
        assert points_form_rate(0, 0, 10) == pytest.approx(0.0)

    def test_mixed_w1_d1_n2(self):
        # (3*1 + 1) / (3*2) = 4/6
        assert points_form_rate(1, 1, 2) == pytest.approx(0.6667)

    def test_existing_enrich_fixture_shape(self):
        # wins=6, draws=2, played=10 → 20/30
        assert points_form_rate(6, 2, 10) == pytest.approx(0.6667)

    def test_n_zero_returns_none(self):
        assert points_form_rate(0, 0, 0) is None

    def test_negative_n_returns_none(self):
        assert points_form_rate(1, 0, -1) is None

    def test_none_ish_coercion(self):
        assert points_form_rate(None, None, 5) == pytest.approx(0.0)
        assert points_form_rate(2, None, 4) == pytest.approx(0.5)

    def test_dirty_over_points_clamped(self):
        # W+D > N would exceed 1.0 without clamp
        assert points_form_rate(10, 10, 5) == pytest.approx(1.0)


def _seed_h2h_matches(tmp_path):
    """Arsenal vs Chelsea twice: Arsenal home win; Chelsea home (Arsenal away) draw."""
    close_kernel_session()
    init_kernel_db(str(tmp_path / "kernel_h2h.db"))
    session = get_kernel_session()
    try:
        fixtures = [
            KernelMatchFixture(
                match_id="h2h-1",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 8, 20, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-2",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Chelsea",
                away_team="Arsenal",
                kickoff_utc=datetime(2025, 9, 5, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-other",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Liverpool",
                kickoff_utc=datetime(2025, 9, 12, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-future",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 12, 1, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-ucl",
                competition="ucl",
                season="2025",
                stage="group",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 9, 1, tzinfo=timezone.utc),
            ),
        ]
        results = [
            KernelMatchResult(
                match_id="h2h-1",
                home_score=2,
                away_score=0,
                finished_at=datetime(2025, 8, 20, 22, tzinfo=timezone.utc),
            ),
            KernelMatchResult(
                match_id="h2h-2",
                home_score=1,
                away_score=1,
                finished_at=datetime(2025, 9, 5, 22, tzinfo=timezone.utc),
            ),
            KernelMatchResult(
                match_id="h2h-other",
                home_score=3,
                away_score=1,
                finished_at=datetime(2025, 9, 12, 22, tzinfo=timezone.utc),
            ),
            # h2h-future: no result yet
            KernelMatchResult(
                match_id="h2h-ucl",
                home_score=1,
                away_score=0,
                finished_at=datetime(2025, 9, 1, 22, tzinfo=timezone.utc),
            ),
        ]
        for row in fixtures + results:
            session.add(row)
        session.commit()
    finally:
        session.close()


class TestH2hFromKernel:
    def test_current_home_perspective_two_meetings(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            before = datetime(2025, 10, 1, tzinfo=timezone.utc)
            h2h = h2h_from_kernel(
                "Arsenal", "Chelsea", competition="epl", before=before,
            )
            assert h2h is not None
            # h2h-1: Arsenal (current home) won; h2h-2: draw at Chelsea
            # future excluded; Liverpool match excluded; ucl filtered out by competition
            assert h2h["matches_played"] == 2
            assert h2h["home_wins"] == 1
            assert h2h["draws"] == 1
            assert h2h["away_wins"] == 0
            assert h2h["data_source"] == "kernel_match_results"
        finally:
            close_kernel_session()

    def test_venue_swap_still_current_home(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            before = datetime(2025, 10, 1, tzinfo=timezone.utc)
            # Swap current home/away: Chelsea is current home
            h2h = h2h_from_kernel(
                "Chelsea", "Arsenal", competition="epl", before=before,
            )
            assert h2h is not None
            assert h2h["matches_played"] == 2
            # From Chelsea perspective: loss at Arsenal, draw at home
            assert h2h["home_wins"] == 0
            assert h2h["draws"] == 1
            assert h2h["away_wins"] == 1
        finally:
            close_kernel_session()

    def test_unknown_pair_returns_none(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            h2h = h2h_from_kernel(
                "Arsenal", "NotATeam", competition="epl",
                before=datetime(2025, 10, 1, tzinfo=timezone.utc),
            )
            assert h2h is None
        finally:
            close_kernel_session()

    def test_same_team_returns_none(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            assert h2h_from_kernel("Arsenal", "Arsenal", competition="epl") is None
        finally:
            close_kernel_session()

    def test_before_excludes_future(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            # Only h2h-1 finished before Aug 25
            h2h = h2h_from_kernel(
                "Arsenal", "Chelsea", competition="epl",
                before=datetime(2025, 8, 25, tzinfo=timezone.utc),
            )
            assert h2h is not None
            assert h2h["matches_played"] == 1
            assert h2h["home_wins"] == 1
        finally:
            close_kernel_session()

    def test_competition_filter(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            h2h = h2h_from_kernel(
                "Arsenal", "Chelsea", competition="ucl",
                before=datetime(2025, 10, 1, tzinfo=timezone.utc),
            )
            assert h2h is not None
            assert h2h["matches_played"] == 1
            assert h2h["home_wins"] == 1
        finally:
            close_kernel_session()
