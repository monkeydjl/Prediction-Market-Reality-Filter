"""Golden baseline tests for world_cup_elo_odds_engine.

Lock the numerical output and prediction_method strings.
"""

import unittest
from app.services.world_cup_engines.world_cup_elo_odds_engine import (
    predict_match_elo_odds,
    calculate_elo_win_probability,
    odds_to_probabilities,
    fuse_elo_and_odds,
    probabilities_to_expected_scores,
    calculate_confidence,
)


class EloOddsEngineGoldenTests(unittest.TestCase):
    """Lock numerical behavior of elo_odds engine with fixed inputs."""

    def test_calculate_elo_win_probability_even(self):
        """Lock Elo probability calculation for even matchup (BTD model)."""
        probs = calculate_elo_win_probability(2000, 2000, is_knockout=False)
        # Even Elo -> 50/50 split after draw; BTD gamma gives ~0.25 draw
        self.assertAlmostEqual(probs["home_win"], 0.3743, places=4)
        self.assertAlmostEqual(probs["draw"], 0.2515, places=4)
        self.assertAlmostEqual(probs["away_win"], 0.3743, places=4)

    def test_calculate_elo_win_probability_advantage(self):
        """Lock Elo probability with +100 Elo advantage (BTD model)."""
        probs = calculate_elo_win_probability(2100, 2000, is_knockout=False)
        # +100 Elo should give home team advantage
        self.assertAlmostEqual(probs["home_win"], 0.4840, places=4)
        self.assertAlmostEqual(probs["draw"], 0.2438, places=4)
        self.assertAlmostEqual(probs["away_win"], 0.2722, places=4)

    def test_calculate_elo_win_probability_knockout(self):
        """Lock knockout stage draw probability reduction (BTD gamma scaled)."""
        group_probs = calculate_elo_win_probability(2000, 2000, is_knockout=False)
        knockout_probs = calculate_elo_win_probability(2000, 2000, is_knockout=True)
        # Knockout should have lower draw probability
        self.assertLess(knockout_probs["draw"], group_probs["draw"])
        self.assertAlmostEqual(knockout_probs["draw"], 0.1991, places=4)

    def test_odds_to_probabilities_removes_margin(self):
        """Lock odds-to-probability conversion with margin removal."""
        probs = odds_to_probabilities(2.10, 3.20, 3.50)
        # Raw implied: 1/2.1=0.476, 1/3.2=0.313, 1/3.5=0.286, total=1.075 (7.5% margin)
        # Normalized: divide by 1.075
        self.assertAlmostEqual(probs["home_win"], 0.4432, places=4)
        self.assertAlmostEqual(probs["draw"], 0.2909, places=4)
        self.assertAlmostEqual(probs["away_win"], 0.2659, places=4)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=10)

    def test_fuse_elo_and_odds_default_weights(self):
        """Lock 30/70 Elo/Odds fusion."""
        elo_probs = {"home_win": 0.50, "draw": 0.25, "away_win": 0.25}
        market_probs = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}
        fused = fuse_elo_and_odds(elo_probs, market_probs, elo_weight=0.30, odds_weight=0.70)
        # home_win = 0.50*0.3 + 0.40*0.7 = 0.15 + 0.28 = 0.43
        self.assertAlmostEqual(fused["home_win"], 0.4300, places=4)
        self.assertAlmostEqual(fused["draw"], 0.2850, places=4)
        self.assertAlmostEqual(fused["away_win"], 0.2850, places=4)

    def test_probabilities_to_expected_scores(self):
        """Lock probability-to-score conversion."""
        probs = {"home_win": 0.45, "draw": 0.25, "away_win": 0.30}
        scores = probabilities_to_expected_scores(probs, league_avg_goals=2.7)
        # home_advantage = (0.45 - 0.30)/2 = 0.075, home_share = 0.5 + 0.075 = 0.575
        # home_goals = 2.7 * 0.575 = 1.5525, away_goals = 2.7 * 0.425 = 1.1475
        # draw_factor = 1.0 - (0.25 - 0.20)*0.5 = 0.975
        # final: home = 1.5525*0.975 = 1.51, away = 1.1475*0.975 = 1.12
        self.assertAlmostEqual(scores["home"], 1.51, places=2)
        self.assertAlmostEqual(scores["away"], 1.12, places=2)

    def test_calculate_confidence_high_agreement(self):
        """Lock confidence when Elo and market agree."""
        elo_probs = {"home_win": 0.45, "draw": 0.25, "away_win": 0.30}
        market_probs = {"home_win": 0.46, "draw": 0.24, "away_win": 0.30}
        fused_probs = {"home_win": 0.455, "draw": 0.245, "away_win": 0.30}
        conf = calculate_confidence(elo_probs, market_probs, fused_probs)
        # avg_disagreement = (0.01 + 0.01 + 0)/3 = 0.0067
        # base = 0.90 - 0.0067*1.5 = 0.89
        # max_prob = 0.455 < 0.60, no boost
        self.assertAlmostEqual(conf, 0.890, places=3)

    def test_calculate_confidence_low_agreement(self):
        """Lock confidence when Elo and market disagree."""
        elo_probs = {"home_win": 0.60, "draw": 0.20, "away_win": 0.20}
        market_probs = {"home_win": 0.35, "draw": 0.30, "away_win": 0.35}
        fused_probs = {"home_win": 0.425, "draw": 0.27, "away_win": 0.305}
        conf = calculate_confidence(elo_probs, market_probs, fused_probs)
        # avg_disagreement = (0.25 + 0.10 + 0.15)/3 = 0.167
        # base = 0.90 - 0.167*1.5 = 0.65, clamped to [0.30, 0.95]
        self.assertAlmostEqual(conf, 0.650, places=3)

    def test_predict_match_elo_odds_no_odds(self):
        """Lock full prediction with Elo only (no odds)."""
        pred = predict_match_elo_odds(
            home_team="Brazil",
            away_team="Argentina",
            elo_home=2100,
            elo_away=2050,
            # No odds
        )

        # Check structure
        self.assertEqual(pred["home_team"], "Brazil")
        self.assertEqual(pred["away_team"], "Argentina")
        self.assertEqual(pred["prediction_method"], "elo_only")
        self.assertFalse(pred["has_betting_odds"])
        self.assertIsNone(pred["market_probabilities"])
        self.assertIsNone(pred["market_favorite"])

        # Check Elo ratings
        self.assertEqual(pred["elo_ratings"]["home"], 2100)
        self.assertEqual(pred["elo_ratings"]["away"], 2050)
        self.assertEqual(pred["elo_ratings"]["difference"], 50.0)

        # Lock predicted score
        self.assertAlmostEqual(pred["predicted_score"]["home"], 1.46, places=2)
        self.assertAlmostEqual(pred["predicted_score"]["away"], 1.18, places=2)

        # Lock outcome probabilities (Elo-only, BTD model)
        self.assertAlmostEqual(pred["outcome_probabilities"]["home_win"], 0.4289, places=4)
        self.assertAlmostEqual(pred["outcome_probabilities"]["draw"], 0.2495, places=4)
        self.assertAlmostEqual(pred["outcome_probabilities"]["away_win"], 0.3216, places=4)

        # Lock confidence
        self.assertAlmostEqual(pred["confidence"], 0.900, places=3)

    def test_predict_match_elo_odds_with_odds(self):
        """Lock full prediction with Elo + Odds fusion."""
        pred = predict_match_elo_odds(
            home_team="Spain",
            away_team="Germany",
            elo_home=2080,
            elo_away=2070,
            odds_home=2.10,
            odds_draw=3.20,
            odds_away=3.50,
        )

        # Check structure
        self.assertEqual(pred["prediction_method"], "elo_odds_fusion (Elo 30% + Odds 70%)")
        self.assertTrue(pred["has_betting_odds"])
        self.assertIsNotNone(pred["market_probabilities"])
        self.assertIn(pred["market_favorite"], ["home", "away"])

        # Lock market probabilities
        self.assertAlmostEqual(pred["market_probabilities"]["home_win"], 0.4432, places=4)
        self.assertAlmostEqual(pred["market_probabilities"]["draw"], 0.2909, places=4)
        self.assertAlmostEqual(pred["market_probabilities"]["away_win"], 0.2659, places=4)

        # Lock fused outcome probabilities (30% Elo + 70% odds, BTD model)
        self.assertAlmostEqual(pred["outcome_probabilities"]["home_win"], 0.4258, places=4)
        self.assertAlmostEqual(pred["outcome_probabilities"]["draw"], 0.2790, places=4)
        self.assertAlmostEqual(pred["outcome_probabilities"]["away_win"], 0.2952, places=4)

        # Lock predicted score
        self.assertAlmostEqual(pred["predicted_score"]["home"], 1.47, places=2)
        self.assertAlmostEqual(pred["predicted_score"]["away"], 1.13, places=2)

        # Lock confidence (high agreement between Elo and market)
        self.assertAlmostEqual(pred["confidence"], 0.802, places=2)

    def test_predict_match_elo_odds_score_matrix(self):
        """Lock that score_probability_matrix is present and valid."""
        pred = predict_match_elo_odds(
            home_team="France",
            away_team="Portugal",
            elo_home=2100,
            elo_away=2000,
        )

        matrix = pred["score_probability_matrix"]
        self.assertIsInstance(matrix, dict)
        # Check a few keys exist
        self.assertIn("0-0", matrix)
        self.assertIn("1-1", matrix)
        self.assertIn("2-1", matrix)
        # Sum should be ~1.0
        self.assertAlmostEqual(sum(matrix.values()), 1.0, places=2)

    def test_predict_match_elo_odds_top_5_scores(self):
        """Lock that top_5_scores is present and sorted."""
        pred = predict_match_elo_odds(
            home_team="France",
            away_team="Portugal",
            elo_home=2100,
            elo_away=2000,
        )

        top_5 = pred["top_5_scores"]
        self.assertEqual(len(top_5), 5)
        for item in top_5:
            self.assertIn("score", item)
            self.assertIn("probability", item)
        # Verify descending order
        probs = [item["probability"] for item in top_5]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_predict_match_elo_odds_prediction_interval(self):
        """Lock prediction_interval structure."""
        pred = predict_match_elo_odds(
            home_team="Brazil",
            away_team="Mexico",
            elo_home=2100,
            elo_away=1900,
        )

        interval = pred["prediction_interval"]
        self.assertIn("p10_total_goals", interval)
        self.assertIn("p90_total_goals", interval)
        self.assertIn("total_goals_distribution", interval)
        # p10 should be less than p90
        self.assertLess(interval["p10_total_goals"], interval["p90_total_goals"])

    def test_predict_match_elo_odds_custom_weights(self):
        """Lock that custom elo_weight / odds_weight are respected."""
        pred = predict_match_elo_odds(
            home_team="England",
            away_team="Belgium",
            elo_home=2050,
            elo_away=2040,
            odds_home=2.20,
            odds_draw=3.10,
            odds_away=3.40,
            elo_weight=0.50,
            odds_weight=0.50,
        )

        # Check prediction_method string reflects custom weights
        self.assertEqual(pred["prediction_method"], "elo_odds_fusion (Elo 50% + Odds 50%)")


if __name__ == "__main__":
    unittest.main()
