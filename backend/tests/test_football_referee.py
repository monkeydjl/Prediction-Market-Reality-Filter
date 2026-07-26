"""Tests for football_referee.bias_for_referee (P1-F8)."""
import pytest

from app.sports.football.football_referee import bias_for_referee


class TestBiasForReferee:
    def test_known_referee_in_band(self):
        b = bias_for_referee("Michael Oliver")
        assert b is not None
        assert -0.25 <= float(b) <= 0.25

    def test_unknown_returns_none(self):
        assert bias_for_referee("NotARealRefereeXYZ") is None

    def test_empty_returns_none(self):
        assert bias_for_referee("") is None
        assert bias_for_referee("   ") is None

    def test_normalize_case_and_spaces(self):
        a = bias_for_referee("Michael Oliver")
        b = bias_for_referee("  michael oliver  ")
        c = bias_for_referee("MICHAEL OLIVER")
        assert a is not None
        assert a == b == c

    def test_alias_if_present(self):
        # Diacritic / ascii pair must share bias when both keys exist
        a = bias_for_referee("Cüneyt Çakır")
        b = bias_for_referee("Cuneyt Cakir")
        assert a is not None and b is not None
        assert a == b

    def test_mild_not_extreme(self):
        b = bias_for_referee("Anthony Taylor")
        assert b is not None
        assert abs(float(b)) <= 0.15
