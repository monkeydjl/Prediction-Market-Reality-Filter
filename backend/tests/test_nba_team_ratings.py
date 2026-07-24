# backend/tests/test_nba_team_ratings.py
"""Tests for NBA static team ORtg/DRtg (P1-B4)."""
from __future__ import annotations

import pytest

from app.sports.basketball.nba_team_ratings import (
    PRIMARY_FRANCHISES,
    _TEAM_RATINGS,
    ratings_for_team,
)


class TestPrimaryCoverage:
    def test_primary_franchises_are_thirty(self):
        assert len(PRIMARY_FRANCHISES) == 30

    def test_every_primary_has_table_key(self):
        missing = [n for n in PRIMARY_FRANCHISES if n not in _TEAM_RATINGS]
        assert missing == [], f"missing ratings: {missing}"

    def test_clippers_alias_matches_primary(self):
        assert "Los Angeles Clippers" in _TEAM_RATINGS
        assert "LA Clippers" in _TEAM_RATINGS
        assert _TEAM_RATINGS["LA Clippers"] == _TEAM_RATINGS["Los Angeles Clippers"]


class TestValueRanges:
    def test_all_primary_ortg_drtg_in_band(self):
        for name in PRIMARY_FRANCHISES:
            row = _TEAM_RATINGS[name]
            assert 105.0 <= float(row["ortg"]) <= 125.0, f"{name} ortg={row['ortg']}"
            assert 105.0 <= float(row["drtg"]) <= 125.0, f"{name} drtg={row['drtg']}"


class TestNetDirection:
    def test_strong_net_above_weak_net(self):
        """OKC-ish top net should beat WAS-ish bottom net (soft ordering)."""
        strong = _TEAM_RATINGS["Oklahoma City Thunder"]
        weak = _TEAM_RATINGS["Washington Wizards"]
        net_s = float(strong["ortg"]) - float(strong["drtg"])
        net_w = float(weak["ortg"]) - float(weak["drtg"])
        assert net_s > net_w
        assert net_s > 0
        assert net_w < 0


class TestRatingsForTeam:
    def test_known_team_returns_ortg_drtg(self):
        row = ratings_for_team("Boston Celtics")
        assert row is not None
        assert "ortg" in row and "drtg" in row
        assert 105.0 <= row["ortg"] <= 125.0

    def test_unknown_and_empty_return_none(self):
        assert ratings_for_team("Totally Fake FC") is None
        assert ratings_for_team("") is None
        assert ratings_for_team("   ") is None

    def test_returns_copy_not_live_table_row(self):
        row = ratings_for_team("Boston Celtics")
        assert row is not None
        row["ortg"] = -1.0
        again = ratings_for_team("Boston Celtics")
        assert again is not None
        assert again["ortg"] != -1.0
