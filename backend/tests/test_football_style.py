"""Tests for football_style.stats_for_team (P1-F6)."""
import pytest

from app.sports.football.football_style import stats_for_team


class TestStatsForTeam:
    def test_known_club_has_all_keys_in_band(self):
        s = stats_for_team("Arsenal")
        assert s is not None
        assert set(s.keys()) >= {"possession_pct", "shots_per90", "ppda"}
        assert 30.0 <= float(s["possession_pct"]) <= 75.0
        assert 5.0 <= float(s["shots_per90"]) <= 25.0
        assert 5.0 <= float(s["ppda"]) <= 20.0

    def test_top_possession_above_mid_table(self):
        top = stats_for_team("Manchester City")
        mid = stats_for_team("Everton")
        assert top is not None and mid is not None
        assert float(top["possession_pct"]) > float(mid["possession_pct"])

    def test_low_ppda_press_below_passive(self):
        # Lower PPDA = stronger press
        press = stats_for_team("Liverpool")
        passive = stats_for_team("Everton")
        assert press is not None and passive is not None
        assert float(press["ppda"]) < float(passive["ppda"])

    def test_unknown_returns_none(self):
        assert stats_for_team("NotAFootballClubXYZ") is None

    def test_empty_returns_none(self):
        assert stats_for_team("") is None
        assert stats_for_team("   ") is None

    def test_normalize_case_and_spaces(self):
        a = stats_for_team("Arsenal")
        b = stats_for_team("  arsenal  ")
        c = stats_for_team("ARSENAL")
        assert a is not None
        assert a == b == c

    def test_common_alias_man_city(self):
        primary = stats_for_team("Manchester City")
        alias = stats_for_team("Man City")
        assert primary is not None
        assert primary == alias

    def test_fixture_style_real_madrid_cf(self):
        s = stats_for_team("Real Madrid CF")
        assert s is not None
        assert 30.0 <= float(s["possession_pct"]) <= 75.0

    def test_fixture_style_bayern(self):
        s = stats_for_team("FC Bayern München")
        assert s is not None
        assert 5.0 <= float(s["shots_per90"]) <= 25.0
