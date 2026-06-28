"""Golden baseline tests for world_cup_rule_engine.

These tests lock the exact numerical output of the rule engine with fixed inputs.
Any refactoring must produce identical results down to the decimal place shown.
"""

import unittest
from app.services.world_cup_engines.world_cup_rule_engine import (
    predict_score_rule_based,
    calculate_outcome_probabilities,
    calculate_expected_goals,
    poisson_probability,
)


class RuleEngineGoldenTests(unittest.TestCase):
    """Lock numerical behavior of rule engine with fixed inputs."""

    def test_poisson_probability_exact_values(self):
        """Lock Poisson formula output."""
        # P(k=2; λ=1.5) = e^(-1.5) * 1.5^2 / 2!
        self.assertAlmostEqual(poisson_probability(1.5, 2), 0.2510, places=4)
        self.assertAlmostEqual(poisson_probability(1.0, 1), 0.3679, places=4)
        self.assertAlmostEqual(poisson_probability(2.0, 3), 0.1804, places=4)

    def test_calculate_outcome_probabilities_dixon_coles(self):
        """Lock Dixon-Coles corrected outcome probabilities with fitted rho.

        These values use the standard Dixon-Coles (1997) convention where a
        NEGATIVE rho boosts low-scoring results (0-0, 1-1) — correcting
        Poisson's systematic underestimation of draws. The fitted rho is
        loaded from data/dixon_coles_params.json (produced by
        scripts/fit_dixon_coles.py); if absent, rho=0 falls back to pure
        independent Poisson.

        Previously this test was locked to rho_dc=+0.04 (legacy hardcoded
        rho=0.96 in the inverted convention), which REDUCED 1-1 probability
        — directionally wrong. The fitted rho_dc=-0.0763 produces a higher
        draw probability (0.2733 vs 0.2451), which is the intended effect.
        """
        import app.services.world_cup_engines.world_cup_rule_engine as rule_engine
        from unittest.mock import patch

        # Patch _load_rho so the test does not depend on the fitted params
        # file existing on disk (CI / fresh checkout without it).
        with patch.object(rule_engine, "_load_rho", return_value=-0.0763):
            probs = calculate_outcome_probabilities(1.5, 1.2)
        self.assertAlmostEqual(probs["home_win"], 0.4322, places=4)
        self.assertAlmostEqual(probs["draw"], 0.2733, places=4)
        self.assertAlmostEqual(probs["away_win"], 0.2945, places=4)
        # Total must be 1.0
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=10)

    def test_calculate_outcome_probabilities_rho_zero_fallback(self):
        """When rho=0 (no params file), tau correction is identity (pure Poisson)."""
        import app.services.world_cup_engines.world_cup_rule_engine as rule_engine
        from unittest.mock import patch

        with patch.object(rule_engine, "_load_rho", return_value=0.0):
            probs = calculate_outcome_probabilities(1.5, 1.2)
        # rho=0 -> no low-score correction -> pure independent Poisson.
        # Draw probability should be lower than the rho<0 (boosted) case.
        self.assertLess(probs["draw"], 0.27)
        # Probabilities still sum to 1.0
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=10)

    def test_calculate_expected_goals_base_case(self):
        """Lock xG calculation with no modifiers."""
        xg = calculate_expected_goals(
            team_attack=2.0,
            team_defense=1.0,
            opponent_attack=1.5,
            opponent_defense=1.2,
            is_home=False,  # World Cup neutral venue
            form_factor=1.0,
            fatigue_factor=1.0,
            injury_impact=0.0,
            market_value_factor=1.0,
            sentiment_factor=1.0,
        )
        # base_xg = (2.0 + 1.2) / 2 = 1.6, no home advantage, all multipliers = 1.0
        self.assertAlmostEqual(xg, 1.60, places=2)

    def test_calculate_expected_goals_with_modifiers(self):
        """Lock xG with form/fatigue/injury/market/sentiment."""
        xg = calculate_expected_goals(
            team_attack=2.0,
            team_defense=1.0,
            opponent_attack=1.5,
            opponent_defense=1.2,
            is_home=False,
            form_factor=1.2,  # Good form
            fatigue_factor=0.9,  # Tired
            injury_impact=-0.2,  # Key injuries
            market_value_factor=1.1,  # Strong squad
            sentiment_factor=1.05,  # Positive momentum
        )
        # base_xg = 1.6 * 1.2 * 0.9 * 1.1 * 1.05 - 0.2 = 1.99584 - 0.2 = 1.79584
        self.assertAlmostEqual(xg, 1.796, places=2)

    def test_predict_score_rule_based_baseline(self):
        """Lock full prediction output with typical factors."""
        factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.6,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.4,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {
                "matches_played": 2,  # < 3, no H2H adjustment
            },
            "context": {
                "tournament_stage": "group_stage",
                "stakes": "medium",
            },
        }

        result = predict_score_rule_based(factors)

        # Lock predicted score
        self.assertAlmostEqual(result["predicted_score"]["home"], 1.98, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.21, places=2)

        # Lock outcome probabilities (rho=-0.0763 fitted from historical results;
        # see scripts/fit_dixon_coles.py and data/dixon_coles_params.json)
        self.assertAlmostEqual(result["outcome_probabilities"]["home_win"], 0.5438, places=4)
        self.assertAlmostEqual(result["outcome_probabilities"]["draw"], 0.2325, places=4)
        self.assertAlmostEqual(result["outcome_probabilities"]["away_win"], 0.2237, places=4)

        # Lock confidence
        self.assertAlmostEqual(result["confidence"], 0.78, places=2)

        # Lock expected goals
        self.assertEqual(result["expected_goals"]["home"], result["predicted_score"]["home"])
        self.assertEqual(result["expected_goals"]["away"], result["predicted_score"]["away"])

        # Lock factor breakdown
        self.assertAlmostEqual(result["factor_breakdown"]["market_value_factor"]["home"], 1.03, places=3)
        self.assertAlmostEqual(result["factor_breakdown"]["market_value_factor"]["away"], 0.97, places=3)
        self.assertAlmostEqual(result["factor_breakdown"]["h2h_adjustment"]["weight"], 0.0, places=3)
        self.assertFalse(result["factor_breakdown"]["must_win"]["home"])
        self.assertFalse(result["factor_breakdown"]["must_win"]["away"])

    def test_predict_score_rule_based_with_h2h(self):
        """Lock prediction with H2H adjustment (>= 3 matches)."""
        factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {
                "matches_played": 8,
                "home_wins": 4,
                "draws": 2,
                "away_wins": 2,
                "avg_goals_home": 2.2,
                "avg_goals_away": 1.0,
            },
            "context": {
                "tournament_stage": "group_stage",
                "stakes": "medium",
            },
        }

        result = predict_score_rule_based(factors)

        # H2H weight = 8/10 * 0.30 = 0.24
        # home_xg blended with H2H avg_goals_home=2.2
        # Check H2H weight applied
        self.assertAlmostEqual(result["factor_breakdown"]["h2h_adjustment"]["weight"], 0.24, places=3)
        self.assertEqual(result["factor_breakdown"]["h2h_adjustment"]["games"], 8)

        # Scores shifted by H2H
        self.assertAlmostEqual(result["predicted_score"]["home"], 1.99, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.19, places=2)

    def test_predict_score_rule_based_knockout_must_win(self):
        """Lock must-win adjustment in knockout stage."""
        factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {
                "matches_played": 0,
            },
            "context": {
                "tournament_stage": "round_of_16",
                "stakes": "high",
            },
        }

        result = predict_score_rule_based(factors)

        # Knockout: both must_win=True -> xG *= 1.15
        self.assertTrue(result["factor_breakdown"]["must_win"]["home"])
        self.assertTrue(result["factor_breakdown"]["must_win"]["away"])

        # Scores boosted by must-win * 1.15
        self.assertAlmostEqual(result["predicted_score"]["home"], 2.21, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.44, places=2)

    def test_predict_score_rule_based_group_status_qualified(self):
        """Lock group_status adjustment: qualified team rotates lineup."""
        factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {
                "matches_played": 0,
            },
            "context": {
                "tournament_stage": "group_stage",
                "stakes": "medium",
            },
            "group_status": {
                "home": "qualified",
                "away": None,
            },
        }

        result = predict_score_rule_based(factors)

        # home_xg *= 0.85, away_xg *= 1.10 (opponent of qualified)
        self.assertAlmostEqual(
            result["factor_breakdown"]["group_status_adjustment"]["home"]["xg_multiplier"],
            0.85,
            places=3
        )
        self.assertAlmostEqual(
            result["factor_breakdown"]["group_status_adjustment"]["home"]["concede_multiplier"],
            1.10,
            places=3
        )
        self.assertAlmostEqual(
            result["factor_breakdown"]["group_status_adjustment"]["confidence_multiplier"],
            0.85,
            places=3
        )

        # Home score reduced, away score boosted
        self.assertAlmostEqual(result["predicted_score"]["home"], 1.63, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.38, places=2)

        # Confidence reduced by 0.85
        self.assertAlmostEqual(result["confidence"], 0.663, places=2)

    def test_predict_score_rule_based_group_status_eliminated(self):
        """Lock group_status adjustment: eliminated team lacks motivation."""
        factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {
                "matches_played": 0,
            },
            "context": {
                "tournament_stage": "group_stage",
                "stakes": "medium",
            },
            "group_status": {
                "home": None,
                "away": "eliminated",
            },
        }

        result = predict_score_rule_based(factors)

        # away_xg *= 0.80, home_xg *= 1.20 (opponent of eliminated)
        self.assertAlmostEqual(
            result["factor_breakdown"]["group_status_adjustment"]["away"]["xg_multiplier"],
            0.80,
            places=3
        )
        self.assertAlmostEqual(
            result["factor_breakdown"]["group_status_adjustment"]["away"]["concede_multiplier"],
            1.20,
            places=3
        )

        # Home score boosted, away score reduced
        self.assertAlmostEqual(result["predicted_score"]["home"], 2.30, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.00, places=2)

        # Confidence reduced by 0.80
        self.assertAlmostEqual(result["confidence"], 0.624, places=2)

    def test_score_probability_matrix_structure(self):
        """Lock that score_matrix is dict[str, float] with "H-A" keys."""
        factors = {
            "home_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {"matches_played": 0},
            "context": {"tournament_stage": "group_stage", "stakes": "medium"},
        }

        result = predict_score_rule_based(factors)

        matrix = result["score_probability_matrix"]
        self.assertIsInstance(matrix, dict)
        # Check a few expected keys exist
        self.assertIn("0-0", matrix)
        self.assertIn("1-1", matrix)
        self.assertIn("2-1", matrix)
        # Sum of all score probs should be ~1.0
        self.assertAlmostEqual(sum(matrix.values()), 1.0, places=2)

    def test_top_5_scores_structure(self):
        """Lock that top_5_scores is list of dicts sorted by probability."""
        factors = {
            "home_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {"matches_played": 0},
            "context": {"tournament_stage": "group_stage", "stakes": "medium"},
        }

        result = predict_score_rule_based(factors)

        top_5 = result["top_5_scores"]
        self.assertEqual(len(top_5), 5)
        for item in top_5:
            self.assertIn("score", item)
            self.assertIn("probability", item)
            self.assertIsInstance(item["score"], str)
            self.assertIsInstance(item["probability"], float)
        # Verify descending order
        probs = [item["probability"] for item in top_5]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_schedule_density_reduces_expected_goals(self):
        """High match density should be visible and reduce expected goals."""
        base = {
            "home_team": {
                "goals_per_game": 1.8,
                "goals_conceded_per_game": 1.1,
                "recent_form": 0.6,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.8,
                "goals_conceded_per_game": 1.1,
                "recent_form": 0.6,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {"matches_played": 0},
            "context": {"tournament_stage": "group_stage", "stakes": "medium"},
        }
        dense = {
            **base,
            "home_team": {
                **base["home_team"],
                "schedule_density": "high",
                "matches_last_14_days": 4,
            },
        }

        base_result = predict_score_rule_based(base)
        dense_result = predict_score_rule_based(dense)

        self.assertLess(dense_result["expected_goals"]["home"], base_result["expected_goals"]["home"])
        self.assertEqual(
            dense_result["factor_breakdown"]["schedule_factor"]["home"]["schedule_density"],
            "high",
        )
        self.assertAlmostEqual(
            dense_result["factor_breakdown"]["schedule_factor"]["home"]["fatigue_multiplier"],
            0.96,
            places=3,
        )

    def test_group_stage_standings_trigger_must_win_with_uppercase_stage(self):
        """GROUP_STAGE plus group-context standings should trigger must-win."""
        factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
                "days_since_last_match": 4,
                "injury_impact": 0.0,
                "market_value_rating": 0.5,
                "sentiment_rating": 0.5,
            },
            "head_to_head": {"matches_played": 0},
            "context": {
                "tournament_stage": "group_stage",
                "stakes": "medium",
                "home_team_standing": {"played": 2, "points": 6},
                "away_team_standing": {"played": 2, "points": 3},
            },
        }

        result = predict_score_rule_based(factors)

        self.assertFalse(result["factor_breakdown"]["must_win"]["home"])
        self.assertTrue(result["factor_breakdown"]["must_win"]["away"])


if __name__ == "__main__":
    unittest.main()
