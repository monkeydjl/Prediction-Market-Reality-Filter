import logging
import unittest

from app.utils.failure_policy import (
    deterministic_fallback,
    fail_closed_empty_list,
    fail_closed_none,
)


class FailurePolicyTests(unittest.TestCase):
    def test_fail_closed_empty_list_logs_policy_and_context(self):
        logger = logging.getLogger("tests.failure_policy.empty")
        with self.assertLogs(logger.name, level="WARNING") as logs:
            result = fail_closed_empty_list(
                logger,
                "source_a",
                RuntimeError("down"),
                context={"b": 2, "a": 1},
            )
        self.assertEqual(result, [])
        text = "\n".join(logs.output)
        self.assertIn("source=source_a", text)
        self.assertIn("policy=fail_closed_empty_list", text)
        self.assertIn("a=1 b=2", text)

    def test_fail_closed_none_logs_policy(self):
        logger = logging.getLogger("tests.failure_policy.none")
        with self.assertLogs(logger.name, level="WARNING") as logs:
            result = fail_closed_none(logger, "source_b", ValueError("bad"))
        self.assertIsNone(result)
        self.assertIn("policy=fail_closed_none", "\n".join(logs.output))

    def test_deterministic_fallback_returns_fallback(self):
        logger = logging.getLogger("tests.failure_policy.fallback")
        fallback = {"analysis_quality": "deterministic_fallback"}
        with self.assertLogs(logger.name, level="WARNING") as logs:
            result = deterministic_fallback(logger, "llm", RuntimeError("down"), fallback)
        self.assertIs(result, fallback)
        self.assertIn("policy=deterministic_fallback", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
