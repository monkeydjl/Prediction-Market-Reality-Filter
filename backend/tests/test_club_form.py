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
    weighted_points_form_rate,
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


class TestWeightedPointsFormRate:
    """Recency weighting. `results[0]` is the most recent match."""

    def test_all_wins(self):
        assert weighted_points_form_rate(["W"] * 5) == pytest.approx(1.0)

    def test_all_losses(self):
        assert weighted_points_form_rate(["L"] * 5) == pytest.approx(0.0)

    def test_all_draws(self):
        assert weighted_points_form_rate(["D"] * 5) == pytest.approx(1 / 3, abs=1e-4)

    def test_recent_wins_beat_recent_losses_at_equal_counts(self):
        recent_good = weighted_points_form_rate(["W", "W", "L", "L"])
        recent_bad = weighted_points_form_rate(["L", "L", "W", "W"])
        assert recent_good > recent_bad

    def test_flat_when_counts_and_order_match(self):
        """Same result repeated is order-independent, so it equals the flat rate."""
        assert weighted_points_form_rate(["W", "W"]) == pytest.approx(
            points_form_rate(2, 0, 2)
        )

    def test_empty_returns_none(self):
        assert weighted_points_form_rate([]) is None

    def test_unknown_results_are_ignored_not_scored_zero(self):
        # "U" comes from _points_result when neither side is the queried team.
        assert weighted_points_form_rate(["W", "U"]) == pytest.approx(1.0)

    def test_all_unknown_returns_none(self):
        assert weighted_points_form_rate(["U", "?"]) is None

    def test_shorter_half_life_weights_recency_more(self):
        seq = ["W", "L"]
        assert (
            weighted_points_form_rate(seq, half_life=2.0)
            > weighted_points_form_rate(seq, half_life=10.0)
        )

    def test_non_positive_half_life_returns_none(self):
        assert weighted_points_form_rate(["W", "L"], half_life=0.0) is None


class TestFormRateWeightedKeys:
    def test_kernel_form_exposes_weighted_rate_and_results(self, tmp_path):
        _seed_matches(tmp_path)
        try:
            stats = team_form_from_kernel(
                "Arsenal",
                competition="epl",
                before=datetime(2025, 9, 20, tzinfo=timezone.utc),
            )
            assert stats is not None
            # Most recent first: drew at Liverpool (9-10), beat Chelsea (9-1)
            assert stats["recent_results"] == ["D", "W"]
            # The win is the older match, so weighting pulls below the flat 4/6
            assert stats["form_rate_weighted"] < points_form_rate(1, 1, 2)
            assert stats["form_rate_weighted"] == pytest.approx(
                weighted_points_form_rate(["D", "W"])
            )
        finally:
            close_kernel_session()


def _seed_named(tmp_path, db_name, competition, home, away):
    """Seed one finished fixture with caller-chosen names and competition."""
    close_kernel_session()
    init_kernel_db(str(tmp_path / db_name))
    session = get_kernel_session()
    try:
        session.add(KernelMatchFixture(
            match_id="m-1",
            competition=competition,
            season="2025",
            stage="regular",
            home_team=home,
            away_team=away,
            kickoff_utc=datetime(2025, 9, 1, tzinfo=timezone.utc),
        ))
        session.add(KernelMatchResult(
            match_id="m-1",
            home_score=2,
            away_score=1,
            finished_at=datetime(2025, 9, 1, 22, tzinfo=timezone.utc),
        ))
        session.commit()
    finally:
        session.close()


_BEFORE = datetime(2025, 10, 1, tzinfo=timezone.utc)


class TestAliasMatching:
    """Team names reach club_form from adapters/market sources, while stored
    fixture names come from ingest. Exact-string matching silently misses.
    """

    def test_alias_resolves_to_stored_full_name(self, tmp_path):
        _seed_named(tmp_path, "a1.db", "epl", "Manchester City", "Chelsea")
        try:
            stats = team_form_from_kernel(
                "Man City", competition="epl", before=_BEFORE,
            )
            assert stats is not None
            assert stats["played"] == 1
            assert stats["wins"] == 1  # won 2-1 at home
        finally:
            close_kernel_session()

    def test_alias_side_assignment_follows_resolved_identity(self, tmp_path):
        """The away team resolved by alias must be scored as the away side."""
        _seed_named(tmp_path, "a2.db", "epl", "Manchester City", "Chelsea")
        try:
            stats = team_form_from_kernel(
                "CHE", competition="epl", before=_BEFORE,
            )
            assert stats is not None
            assert stats["losses"] == 1  # lost 1-2 away
            assert stats["wins"] == 0
            assert stats["goals_per_game"] == 1.0  # away goals, not home's 2
        finally:
            close_kernel_session()

    def test_no_competition_disables_alias_layer(self, tmp_path):
        _seed_named(tmp_path, "a3.db", "epl", "Manchester City", "Chelsea")
        try:
            assert team_form_from_kernel(
                "Man City", competition=None, before=_BEFORE,
            ) is None
        finally:
            close_kernel_session()

    def test_unknown_competition_disables_alias_layer(self, tmp_path):
        _seed_named(tmp_path, "a4.db", "not_a_league", "Manchester City", "Chelsea")
        try:
            assert team_form_from_kernel(
                "Man City", competition="not_a_league", before=_BEFORE,
            ) is None
        finally:
            close_kernel_session()

    def test_same_abbreviation_does_not_cross_competitions(self, tmp_path):
        """BOS is Boston Celtics in nba and Boston Red Sox in mlb."""
        _seed_named(tmp_path, "a5.db", "nba", "Boston Red Sox", "New York Yankees")
        try:
            assert team_form_from_kernel(
                "BOS", competition="nba", before=_BEFORE,
            ) is None
        finally:
            close_kernel_session()

    def test_same_abbreviation_matches_within_its_own_competition(self, tmp_path):
        _seed_named(tmp_path, "a6.db", "mlb", "Boston Red Sox", "New York Yankees")
        try:
            stats = team_form_from_kernel(
                "BOS", competition="mlb", before=_BEFORE,
            )
            assert stats is not None
            assert stats["wins"] == 1
        finally:
            close_kernel_session()

    def test_unresolvable_names_fall_back_to_string_match(self, tmp_path):
        """Pre-existing behaviour must survive: names absent from the alias
        table still match when they are byte-identical.
        """
        _seed_named(tmp_path, "a7.db", "epl", "Obscure Town FC", "Chelsea")
        try:
            stats = team_form_from_kernel(
                "obscure town fc", competition="epl", before=_BEFORE,
            )
            assert stats is not None
            assert stats["played"] == 1
        finally:
            close_kernel_session()

    def test_h2h_matches_pair_through_aliases(self, tmp_path):
        _seed_named(tmp_path, "a8.db", "epl", "Manchester City", "Tottenham")
        try:
            h2h = h2h_from_kernel(
                "Man City", "Spurs", competition="epl", before=_BEFORE,
            )
            assert h2h is not None
            assert h2h["matches_played"] == 1
            assert h2h["home_wins"] == 1
        finally:
            close_kernel_session()

    def test_h2h_rejects_same_team_under_different_aliases(self, tmp_path):
        _seed_named(tmp_path, "a9.db", "epl", "Manchester City", "Tottenham")
        try:
            assert h2h_from_kernel(
                "Spurs", "Tottenham", competition="epl", before=_BEFORE,
            ) is None
        finally:
            close_kernel_session()


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
