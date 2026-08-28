import importlib
import os
import unittest
from unittest.mock import patch

from app.core import config
from app.core.config import settings


class ConfigHelperTests(unittest.TestCase):
    def test_env_bool_accepts_explicit_truthy_values(self):
        truthy = {"BOOL_UNDER_TEST": "yes"}
        falsy = {"BOOL_UNDER_TEST": "no"}
        with patch.dict(os.environ, truthy, clear=False):
            self.assertTrue(config._env_bool("BOOL_UNDER_TEST"))
        with patch.dict(os.environ, falsy, clear=False):
            self.assertFalse(config._env_bool("BOOL_UNDER_TEST"))

    def test_env_csv_strips_empty_items(self):
        with patch.dict(os.environ, {"CSV_UNDER_TEST": "GET, POST,, OPTIONS "}, clear=False):
            self.assertEqual(
                config._env_csv("CSV_UNDER_TEST", ""),
                ["GET", "POST", "OPTIONS"],
            )


class FanOutBoundsTests(unittest.TestCase):
    """The fan-out settings that drive unattended outbound spend are bounded.

    `/events/discover` has always validated the same quantity (`limit: int =
    Query(default=10, ge=1, le=50)`), but the scheduler read
    EVENT_DISCOVER_LIMIT raw from the environment. Measured on the real
    collector: limit=0 fetched every enabled source and then sliced the pool to
    empty, so a scan spent the network calls, analyzed nothing, and reported
    "no candidates from any source" while the sources were healthy;
    limit=100000 asked Polymarket alone for 300000 candidates in one call.
    LLM_CONCURRENCY=0 built a Semaphore nobody can acquire, hanging the scan for
    the full 600s timeout.
    """

    def test_clamp_int_returns_in_range_values_untouched(self):
        """Non-vacuous baseline: the clamp is not rewriting healthy values."""
        for value in (1, 10, 100, 500):
            with self.subTest(value=value):
                self.assertEqual(config._clamp_int(value, "X", 1, 500), value)

    def test_clamp_int_lifts_non_positive_to_the_floor(self):
        for value in (0, -1, -500):
            with self.subTest(value=value):
                self.assertEqual(config._clamp_int(value, "X", 1, 500), 1)

    def test_clamp_int_lowers_out_of_range_to_the_ceiling(self):
        for value in (501, 100000):
            with self.subTest(value=value):
                self.assertEqual(config._clamp_int(value, "X", 1, 500), 500)

    def test_clamp_int_logs_the_correction_it_made(self):
        """A silent clamp would hide the operator's typo, so the log line is
        part of the contract, not decoration."""
        with self.assertLogs("app.core.config", level="WARNING") as captured:
            config._clamp_int(0, "EVENT_DISCOVER_LIMIT", 1, 500)
        joined = "\n".join(captured.output)
        self.assertIn("EVENT_DISCOVER_LIMIT=0", joined)
        self.assertIn("[1, 500]", joined)
        self.assertIn("using 1", joined)

    def test_clamp_int_does_not_log_for_an_in_range_value(self):
        import logging as _logging

        logger = _logging.getLogger("app.core.config")
        with patch.object(logger, "warning") as warn:
            config._clamp_int(10, "EVENT_DISCOVER_LIMIT", 1, 500)
        warn.assert_not_called()

    def test_env_int_bounded_reads_and_clamps(self):
        cases = {"0": 1, "1": 1, "100": 100, "500": 500, "99999": 500}
        for raw, expected in cases.items():
            with self.subTest(raw=raw), \
                    patch.dict(os.environ, {"INT_UNDER_TEST": raw}, clear=False):
                self.assertEqual(
                    config._env_int_bounded("INT_UNDER_TEST", "100", 1, 500),
                    expected,
                )

    def test_discover_limit_is_bounded_and_defaults_documented(self):
        self.assertEqual(settings.EVENT_DISCOVER_LIMIT_MIN, 1)
        self.assertEqual(settings.EVENT_DISCOVER_LIMIT_MAX, 500)
        self.assertGreaterEqual(
            settings.EVENT_DISCOVER_LIMIT, settings.EVENT_DISCOVER_LIMIT_MIN
        )
        self.assertLessEqual(
            settings.EVENT_DISCOVER_LIMIT, settings.EVENT_DISCOVER_LIMIT_MAX
        )

    def test_llm_concurrency_is_bounded_and_never_zero(self):
        """0 is the dangerous value here: asyncio.Semaphore(0) constructs fine
        and then blocks every candidate forever."""
        self.assertEqual(settings.LLM_CONCURRENCY_MIN, 1)
        self.assertEqual(settings.LLM_CONCURRENCY_MAX, 64)
        self.assertGreaterEqual(
            settings.LLM_CONCURRENCY, settings.LLM_CONCURRENCY_MIN
        )
        self.assertLessEqual(
            settings.LLM_CONCURRENCY, settings.LLM_CONCURRENCY_MAX
        )

    def test_settings_reload_clamps_a_fat_fingered_environment(self):
        """Drives the real module import path, not just the helper: a bad env
        var must not survive into `settings`.

        The restoring reload runs *outside* the `patch.dict` block on purpose.
        `importlib.reload` rebinds `config.settings` to a fresh instance built
        from whatever `os.environ` holds at that moment, so reloading inside the
        block rebuilt it from the fat-fingered values and left
        EVENT_DISCOVER_LIMIT=500 / LLM_CONCURRENCY=1 in place for every later
        test in the process -- `patch.dict` restores the environment, not the
        object already derived from it. That leak perturbed an unrelated
        timestamp-ordering test in the full suite.
        """
        original_limit = config.settings.EVENT_DISCOVER_LIMIT
        original_concurrency = config.settings.LLM_CONCURRENCY
        try:
            with patch.dict(os.environ,
                            {"EVENT_DISCOVER_LIMIT": "100000",
                             "LLM_CONCURRENCY": "0"},
                            clear=False):
                reloaded = importlib.reload(config)
                self.assertEqual(reloaded.settings.EVENT_DISCOVER_LIMIT, 500)
                self.assertEqual(reloaded.settings.LLM_CONCURRENCY, 1)
        finally:
            importlib.reload(config)
        # Assert the cleanup, so a future edit cannot silently reintroduce the
        # leak this docstring describes.
        self.assertEqual(config.settings.EVENT_DISCOVER_LIMIT, original_limit)
        self.assertEqual(config.settings.LLM_CONCURRENCY, original_concurrency)


class FootballDataConfigTests(unittest.TestCase):
    def test_football_data_config_can_be_configured_from_env(self):
        from app.core import config as config_module

        custom_base_url = "https://football-data-proxy.example/v4"
        custom_key = "football-data-test-key"
        with patch.dict(
            os.environ,
            {
                "FOOTBALL_DATA_API_KEY": custom_key,
                "FOOTBALL_DATA_BASE_URL": custom_base_url,
            },
            clear=False,
        ):
            reloaded_config = importlib.reload(config_module)
            try:
                self.assertEqual(
                    reloaded_config.settings.FOOTBALL_DATA_API_KEY,
                    custom_key,
                )
                self.assertEqual(
                    reloaded_config.settings.FOOTBALL_DATA_BASE_URL,
                    custom_base_url,
                )
            finally:
                importlib.reload(config_module)


class OddsApiConfigTests(unittest.TestCase):
    def test_odds_api_base_url_can_be_configured_from_env(self):
        from app.core import config as config_module
        from app.services import odds_api_service

        custom_base_url = "https://odds-proxy.example/v4"
        with patch.dict(
            os.environ,
            {"ODDS_API_BASE_URL": custom_base_url},
            clear=False,
        ):
            reloaded_config = importlib.reload(config_module)
            try:
                reloaded_odds_service = importlib.reload(odds_api_service)

                self.assertEqual(
                    reloaded_config.settings.ODDS_API_BASE_URL,
                    custom_base_url,
                )
                self.assertEqual(reloaded_odds_service.ODDS_API_BASE, custom_base_url)
            finally:
                importlib.reload(config_module)
                importlib.reload(odds_api_service)


class ConfigDefaultTests(unittest.TestCase):
    def test_scheduler_misfire_default_is_operationally_useful(self):
        self.assertGreaterEqual(settings.SCHEDULER_MISFIRE_GRACE_SECONDS, 60 * 60)

    def test_sec_user_agent_is_declared(self):
        self.assertTrue(settings.SEC_USER_AGENT.strip())

    def test_review_queue_auto_resolve_confidence_default(self):
        self.assertEqual(settings.REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE, 0.95)


class TestPhase4Config:
    """Phase 4 NBA configuration fields."""

    def test_phase4_nba_enabled_defaults_false(self):
        from app.core import config
        assert config.settings.PHASE4_NBA_ENABLED is False

    def test_balldontlie_api_key_defaults_empty(self):
        from app.core import config
        assert config.settings.BALLDONTLIE_API_KEY == ""

    def test_nba_elo_hfa_default(self):
        from app.core import config
        assert config.settings.NBA_ELO_HFA == 100

    def test_nba_elo_k_regular_default(self):
        from app.core import config
        assert config.settings.NBA_ELO_K_REGULAR == 20

    def test_nba_elo_k_playoff_default(self):
        from app.core import config
        assert config.settings.NBA_ELO_K_PLAYOFF == 30

    def test_nba_league_avg_total_default(self):
        from app.core import config
        assert config.settings.NBA_LEAGUE_AVG_TOTAL == 220.0


class TestPhase5Config:
    """Phase 5 MLB/NHL configuration fields."""

    def test_phase5_mlb_enabled_defaults_false(self):
        from app.core import config
        assert config.settings.PHASE5_MLB_ENABLED is False

    def test_phase5_nhl_enabled_defaults_false(self):
        from app.core import config
        assert config.settings.PHASE5_NHL_ENABLED is False

    def test_mlb_elo_hfa_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_HFA == 50

    def test_mlb_elo_k_regular_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_K_REGULAR == 20

    def test_mlb_elo_k_playoff_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_K_PLAYOFF == 30

    def test_mlb_elo_season_carry_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_SEASON_CARRY == 0.7

    def test_mlb_league_avg_total_default(self):
        from app.core import config
        assert config.settings.MLB_LEAGUE_AVG_TOTAL == 8.5

    def test_nhl_elo_hfa_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_HFA == 55

    def test_nhl_elo_k_regular_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_K_REGULAR == 20

    def test_nhl_elo_k_playoff_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_K_PLAYOFF == 30

    def test_nhl_elo_season_carry_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_SEASON_CARRY == 0.75

    def test_nhl_league_avg_total_default(self):
        from app.core import config
        assert config.settings.NHL_LEAGUE_AVG_TOTAL == 5.5


if __name__ == "__main__":
    unittest.main()
