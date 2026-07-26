"""Tests for football_xg.xg_for_team (P1-F5)."""
import pytest

from app.sports.football.football_xg import xg_for_team


class TestXgForTeam:
    def test_known_top_club_in_band(self):
        xg = xg_for_team("Arsenal")
        assert xg is not None
        assert 0.8 <= float(xg) <= 2.5

    def test_top_attack_above_mid_table(self):
        top = xg_for_team("Manchester City")
        mid = xg_for_team("Everton")
        assert top is not None and mid is not None
        assert float(top) > float(mid)

    def test_unknown_returns_none(self):
        assert xg_for_team("NotAFootballClubXYZ") is None

    def test_empty_returns_none(self):
        assert xg_for_team("") is None
        assert xg_for_team("   ") is None

    def test_normalize_case_and_spaces(self):
        a = xg_for_team("Arsenal")
        b = xg_for_team("  arsenal  ")
        c = xg_for_team("ARSENAL")
        assert a is not None
        assert a == b == c

    def test_common_alias_man_city(self):
        primary = xg_for_team("Manchester City")
        alias = xg_for_team("Man City")
        assert primary is not None
        assert primary == alias

    def test_fixture_style_real_madrid_cf(self):
        # _make_match in adapter tests uses "Real Madrid CF"
        xg = xg_for_team("Real Madrid CF")
        assert xg is not None
        assert 0.8 <= float(xg) <= 2.5

    def test_fixture_style_bayern(self):
        xg = xg_for_team("FC Bayern München")
        assert xg is not None
        assert 0.8 <= float(xg) <= 2.5
