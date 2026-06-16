import unittest

from app.agents.probability_agent import ProbabilityAgent


class ProbabilityAgentTests(unittest.TestCase):
    def test_normalize_result_rejects_non_finite_numbers(self):
        result = ProbabilityAgent()._normalize_result(
            {
                "true_probability": "NaN",
                "confidence_score": "Infinity",
            },
            fallback_probability=45.0,
        )

        self.assertEqual(result["true_probability"], 50.0)
        self.assertEqual(result["confidence_score"], 0.5)


if __name__ == "__main__":
    unittest.main()
