"""Tests for the Dixon-Coles fitting script and rho loader.

Covers:
1. ``fit_dixon_coles.fit_dixon_coles()`` produces a sane rho on a synthetic
   dataset (negative, in literature range, increases draw probability vs
   pure Poisson).
2. ``world_cup_rule_engine._load_rho`` reads the fitted params file and
   applies sanity clamping.
3. ``_load_rho`` falls back to 0.0 when the params file is missing.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make scripts/ importable.
_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "scripts"))

from fit_dixon_coles import fit_dixon_coles  # noqa: E402
from app.services.world_cup_engines import world_cup_rule_engine as rule_engine  # noqa: E402


class DixonColesFittingTests(unittest.TestCase):
    """Validate the fitter produces sane parameters on real historical data."""

    @classmethod
    def setUpClass(cls):
        # Fit on a small recent window so the test runs in a few seconds.
        # half_life=730d + since=2020 keeps the sample count manageable while
        # still leaving enough matches for a stable fit.
        cls.params = fit_dixon_coles(half_life_days=730.0, since_year=2020, min_team_matches=3)

    def test_rho_is_negative(self):
        """Standard Dixon-Coles rho is negative (boosts 0-0 and 1-1 draws).

        A negative rho is the literature-consistent direction: it corrects
        Poisson's systematic underestimation of low-scoring draws. The
        legacy hardcoded ``rho=0.96`` corresponded to rho_dc=+0.04, which
        was directionally WRONG (reduced 1-1 probability). This test guards
        against the fitter ever producing a positive rho due to a sign bug.
        """
        self.assertLess(self.params["rho"], 0.0,
                        f"rho={self.params['rho']} should be negative (DC convention)")

    def test_rho_in_literature_range(self):
        """Fitted rho should be in the typical [-0.3, 0.0] range."""
        self.assertGreaterEqual(self.params["rho"], -0.3)
        self.assertLess(self.params["rho"], 0.0)

    def test_home_advantage_positive_and_sane(self):
        """Home advantage should be a small positive log-shift (~0.2-0.5)."""
        ha = self.params["home_advantage"]
        self.assertGreater(ha, 0.1)
        self.assertLess(ha, 0.6)

    def test_mu_in_goal_range(self):
        """Base goals per game should be in a realistic match range."""
        mu = self.params["mu"]
        self.assertGreater(mu, 0.8)
        self.assertLess(mu, 2.0)

    def test_sample_count_sufficient(self):
        """Need enough samples for a stable fit."""
        self.assertGreater(self.params["sample_count"], 500)

    def test_rho_negative_increases_draw_probability(self):
        """The whole point of DC: negative rho should boost draw vs rho=0."""
        from app.services.world_cup_engines.world_cup_rule_engine import (
            calculate_outcome_probabilities,
        )
        # 1.5 vs 1.2 is a typical competitive match xG pair.
        with patch.object(rule_engine, "_load_rho", return_value=self.params["rho"]):
            probs_dc = calculate_outcome_probabilities(1.5, 1.2)
        with patch.object(rule_engine, "_load_rho", return_value=0.0):
            probs_pure = calculate_outcome_probabilities(1.5, 1.2)
        # Draw probability must INCREASE with negative rho (the DC correction).
        self.assertGreater(
            probs_dc["draw"], probs_pure["draw"],
            f"DC draw ({probs_dc['draw']}) should exceed pure-Poisson draw ({probs_pure['draw']})",
        )


class RhoLoaderTests(unittest.TestCase):
    """Validate _load_rho file loading, clamping, and fallback behavior."""

    def test_load_rho_reads_json_file(self):
        """_load_rho returns the rho value from a valid params JSON."""
        params = {"rho": -0.1234, "home_advantage": 0.3, "mu": 1.2}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(params, tf)
            tmp_path = tf.name
        try:
            rule_engine._load_rho.cache_clear()
            with patch.object(rule_engine, "_PARAMS_PATH", Path(tmp_path)):
                rho = rule_engine._load_rho()
            self.assertAlmostEqual(rho, -0.1234, places=4)
        finally:
            rule_engine._load_rho.cache_clear()
            os.unlink(tmp_path)

    def test_load_rho_clamps_out_of_range(self):
        """rho outside [-0.5, 0.5] is clamped to the boundary."""
        params = {"rho": 0.9}  # Way out of range
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(params, tf)
            tmp_path = tf.name
        try:
            rule_engine._load_rho.cache_clear()
            with patch.object(rule_engine, "_PARAMS_PATH", Path(tmp_path)):
                rho = rule_engine._load_rho()
            self.assertEqual(rho, 0.5)  # Clamped to upper bound
        finally:
            rule_engine._load_rho.cache_clear()
            os.unlink(tmp_path)

    def test_load_rho_falls_back_to_zero_when_file_missing(self):
        """Missing params file -> rho=0.0 (pure Poisson, no correction)."""
        rule_engine._load_rho.cache_clear()
        with patch.object(rule_engine, "_PARAMS_PATH", Path("/nonexistent/path/params.json")):
            rho = rule_engine._load_rho()
        self.assertEqual(rho, 0.0)

    def test_load_rho_falls_back_on_invalid_json(self):
        """Corrupt JSON -> rho=0.0 (graceful degradation)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            tf.write("not valid json {{{")
            tmp_path = tf.name
        try:
            rule_engine._load_rho.cache_clear()
            with patch.object(rule_engine, "_PARAMS_PATH", Path(tmp_path)):
                rho = rule_engine._load_rho()
            self.assertEqual(rho, 0.0)
        finally:
            rule_engine._load_rho.cache_clear()
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
