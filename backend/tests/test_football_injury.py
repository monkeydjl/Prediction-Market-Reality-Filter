# backend/tests/test_football_injury.py
"""Tests for football static injury impact (P1-F3)."""
from __future__ import annotations

import pytest

from app.sports.football.football_injury import (
    ROLE_WEIGHTS,
    injury_impact_for_team,
    summarize_injury_impact,
)


class TestRoleWeights:
    def test_expected_tiers(self):
        assert ROLE_WEIGHTS["star"] == 0.35
        assert ROLE_WEIGHTS["starter"] == 0.18
        assert ROLE_WEIGHTS["rotation"] == 0.08
        assert ROLE_WEIGHTS["bench"] == 0.03


class TestSummarizeInjuryImpact:
    def test_none_and_empty_return_none(self):
        assert summarize_injury_impact(None) is None
        assert summarize_injury_impact([]) is None

    def test_single_star_out(self):
        rows = [{"player": "Star A", "role": "star", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.35)

    def test_status_case_insensitive(self):
        rows = [{"player": "X", "role": "starter", "status": "OUT"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.18)

    def test_non_out_statuses_ignored(self):
        rows = [
            {"player": "A", "role": "star", "status": "doubtful"},
            {"player": "B", "role": "starter", "status": "questionable"},
            {"player": "C", "role": "bench", "status": "suspended"},
        ]
        assert summarize_injury_impact(rows) is None

    def test_unknown_role_uses_bench(self):
        rows = [{"player": "Y", "role": "unknown", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.03)

    def test_missing_role_uses_bench(self):
        rows = [{"player": "Z", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.03)

    def test_multiple_outs_sum(self):
        rows = [
            {"player": "A", "role": "star", "status": "out"},
            {"player": "B", "role": "starter", "status": "out"},
            {"player": "C", "role": "rotation", "status": "out"},
        ]
        # 0.35 + 0.18 + 0.08 = 0.61
        assert summarize_injury_impact(rows) == pytest.approx(0.61)

    def test_clamp_to_one(self):
        rows = [{"player": f"S{i}", "role": "star", "status": "out"} for i in range(5)]
        # 5 * 0.35 = 1.75 → 1.0
        assert summarize_injury_impact(rows) == pytest.approx(1.0)

    def test_mixed_out_and_non_out(self):
        rows = [
            {"player": "A", "role": "star", "status": "out"},
            {"player": "B", "role": "starter", "status": "doubtful"},
        ]
        assert summarize_injury_impact(rows) == pytest.approx(0.35)

    def test_ignores_non_dict_rows(self):
        rows = ["bad", None, {"player": "A", "role": "bench", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.03)


class TestInjuryImpactForTeam:
    def test_unknown_team_returns_none(self):
        assert injury_impact_for_team("Totally Fake FC") is None
        assert injury_impact_for_team("") is None

    def test_real_madrid_example(self):
        # Task 2 ships star out → 0.35
        assert injury_impact_for_team("Real Madrid CF") == pytest.approx(0.35)

    def test_bayern_example(self):
        # Task 2 ships starter + rotation → 0.26
        assert injury_impact_for_team("FC Bayern München") == pytest.approx(0.26)
