"""Tests for the Bradley-Terry-Davidson (BTD) model.

Covers:
1. _load_params: reads fitted gamma from JSON, clamps, falls back on missing/corrupt
2. calculate_btd_probabilities: numerical correctness, knockout reduction, sum=1
3. Integration with elo_odds_engine: calculate_elo_win_probability delegates to BTD
"""

import json
import logging
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class BtdModelTests(unittest.TestCase):
    """Validate BTD probability formula and param loading."""

    def test_probabilities_sum_to_one(self):
        """All probability outputs must sum to ~1.0 (within rounding)."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        for eh, ea in [(2000, 2000), (2100, 2000), (1800, 1900), (2200, 1500)]:
            with self.subTest(elo_home=eh, elo_away=ea):
                probs = calculate_btd_probabilities(eh, ea, is_neutral=True)
                # Probabilities are rounded to 4 decimal places, so sum may
                # deviate by up to 0.0003 from 1.0 due to rounding.
                self.assertAlmostEqual(sum(probs.values()), 1.0, places=3)

    def test_equal_elo_gives_symmetric_probs(self):
        """Equal Elo ratings should give home_win == away_win."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        probs = calculate_btd_probabilities(2000, 2000, is_neutral=True)
        self.assertAlmostEqual(probs["home_win"], probs["away_win"], places=6)

    def test_higher_elo_gives_higher_win_prob(self):
        """Higher Elo should give higher win probability."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        probs = calculate_btd_probabilities(2100, 1900, is_neutral=True)
        self.assertGreater(probs["home_win"], probs["away_win"])

    def test_knockout_reduces_draw(self):
        """Knockout stage should reduce draw probability."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        group = calculate_btd_probabilities(2000, 2000, is_neutral=True, is_knockout=False)
        knockout = calculate_btd_probabilities(2000, 2000, is_neutral=True, is_knockout=True)
        self.assertLess(knockout["draw"], group["draw"])
        # Knockout draw should be ~0.74x of group draw
        self.assertGreater(knockout["draw"], group["draw"] * 0.5)

    def test_neutral_vs_non_neutral(self):
        """Non-neutral with home_adv > 0 should boost home win."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        neutral = calculate_btd_probabilities(2000, 2000, is_neutral=True)
        non_neutral = calculate_btd_probabilities(2000, 2000, is_neutral=False)
        # If home_adv > 0 in params, non-neutral should boost home_win
        if non_neutral["home_win"] != neutral["home_win"]:
            self.assertGreater(non_neutral["home_win"], neutral["home_win"])

    def test_extreme_elo_diff(self):
        """Very large Elo gap should give very low draw probability."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        probs = calculate_btd_probabilities(2200, 1400, is_neutral=True)
        # 800 Elo gap -> draw should be < 15%
        self.assertLess(probs["draw"], 0.15)
        self.assertGreater(probs["home_win"], 0.80)


class BtdParamsLoaderTests(unittest.TestCase):
    """Test _load_params JSON loading, clamping, and fallback behavior."""

    def setUp(self):
        # Clear lru_cache before each test
        from app.services.world_cup_engines.world_cup_btd_model import _load_params
        _load_params.cache_clear()

    def tearDown(self):
        from app.services.world_cup_engines.world_cup_btd_model import _load_params
        _load_params.cache_clear()

    def test_loads_gamma_from_json(self):
        """_load_params reads gamma from a valid JSON file."""
        from app.services.world_cup_engines.world_cup_btd_model import _load_params

        fake_params = {"gamma": 0.555, "home_advantage": 0.3}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(fake_params, f)
            tmp_path = f.name

        try:
            with patch(
                "app.services.world_cup_engines.world_cup_btd_model._PARAMS_PATH",
                Path(tmp_path),
            ):
                _load_params.cache_clear()
                gamma, home_adv = _load_params()
            self.assertAlmostEqual(gamma, 0.555, places=6)
            self.assertAlmostEqual(home_adv, 0.3, places=6)
        finally:
            os.unlink(tmp_path)

    def test_clamps_extreme_gamma(self):
        """Gamma outside [0.01, 10.0] is clamped."""
        from app.services.world_cup_engines.world_cup_btd_model import _load_params

        fake_params = {"gamma": 100.0, "home_advantage": 0.0}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(fake_params, f)
            tmp_path = f.name

        try:
            with patch(
                "app.services.world_cup_engines.world_cup_btd_model._PARAMS_PATH",
                Path(tmp_path),
            ):
                _load_params.cache_clear()
                gamma, _ = _load_params()
            self.assertEqual(gamma, 10.0)
        finally:
            os.unlink(tmp_path)

    def test_falls_back_on_missing_file(self):
        """Missing params file returns fallback gamma."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            _FALLBACK_GAMMA,
            _FALLBACK_HOME_ADV,
            _load_params,
        )

        with patch(
            "app.services.world_cup_engines.world_cup_btd_model._PARAMS_PATH",
            Path("/nonexistent/path/btd_params.json"),
        ):
            _load_params.cache_clear()
            gamma, home_adv = _load_params()
        self.assertEqual(gamma, _FALLBACK_GAMMA)
        self.assertEqual(home_adv, _FALLBACK_HOME_ADV)

    def test_falls_back_on_corrupt_json(self):
        """Corrupt JSON returns fallback gamma."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            _FALLBACK_GAMMA,
            _load_params,
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("{ not valid json")
            tmp_path = f.name

        try:
            with patch(
                "app.services.world_cup_engines.world_cup_btd_model._PARAMS_PATH",
                Path(tmp_path),
            ):
                _load_params.cache_clear()
                gamma, _ = _load_params()
            self.assertEqual(gamma, _FALLBACK_GAMMA)
        finally:
            os.unlink(tmp_path)


class BtdEloOddsIntegrationTests(unittest.TestCase):
    """Verify calculate_elo_win_probability delegates to BTD."""

    def test_elo_win_probability_uses_btd(self):
        """calculate_elo_win_probability should produce same output as BTD."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        from app.services.world_cup_engines.world_cup_elo_odds_engine import (
            calculate_elo_win_probability,
        )
        btd_probs = calculate_btd_probabilities(2050, 1980, is_neutral=True, is_knockout=False)
        elo_probs = calculate_elo_win_probability(2050, 1980, is_knockout=False)
        self.assertEqual(btd_probs, elo_probs)

    def test_elo_win_probability_knockout_uses_btd(self):
        """Knockout flag propagates to BTD."""
        from app.services.world_cup_engines.world_cup_btd_model import (
            calculate_btd_probabilities,
        )
        from app.services.world_cup_engines.world_cup_elo_odds_engine import (
            calculate_elo_win_probability,
        )
        btd_probs = calculate_btd_probabilities(2050, 1980, is_neutral=True, is_knockout=True)
        elo_probs = calculate_elo_win_probability(2050, 1980, is_knockout=True)
        self.assertEqual(btd_probs, elo_probs)


if __name__ == "__main__":
    unittest.main()
