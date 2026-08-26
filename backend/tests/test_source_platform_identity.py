"""A source's rank must follow the name its records actually carry.

Every adapter stamps ``source.platform`` from ``settings.<X>_SOURCE_NAME``
(`kalshi_event_source.py:146` and its siblings; only Polymarket hardcodes its
name). ``candidate_dedup_service`` ranked platforms from a dict of the *default*
spellings, so an operator who set ``KALSHI_SOURCE_NAME`` got that spelling on
every record while the ranking still looked for ``"Kalshi"`` -- the source
stopped being recognised and fell to ``_UNKNOWN_PRIORITY = 99``, i.e. **below**
Open Web, so an LLM-reworded news item with no price evicted a real market.

Metaculus was the same defect without a rename: the dict listed five platforms
and Metaculus was not one of them, so a curated community forecast lost to Open
Web on every duplicate, contradicting the module's own docstring.

These tests pin the classification against ``event_store._PLATFORM_NAME_SETTINGS``
-- which is itself pinned to an AST scan of the adapters by
``test_event_store_source_names`` -- so a source added to one list cannot stay
unclassified in the other.
"""

import unittest
from unittest.mock import patch

from app.core.config import settings
from app.memory import event_store as store
from app.services import candidate_dedup_service as dedup


# Platform-name settings that dedup deliberately does not rank by platform, with
# the reason per row. Declared as data so the partition below can be exact: a
# subset check would pass while a newly added source went unranked, which is how
# Metaculus stayed at priority 99.
_CLASSIFIED_BY_SOURCE_TYPE = {
    # world_cup_event_source stamps `type: "sports_event"`, and _priority reads
    # the type before the platform, so its rank does not depend on the name.
    "WORLD_CUP_SOURCE_NAME": dedup._SPORTS_EVENT_TYPE,
    # event_extraction_service stamps `type: "open_web"`, likewise.
    "OPEN_WEB_SOURCE_NAME": dedup._OPEN_WEB_TYPE,
}
_NOT_A_DISCOVERY_SOURCE = {
    # Manifold is retired: config.py calls its settings "legacy ... kept only so
    # existing .env files do not break startup", and no candidate source feeds
    # it into discovery. Ranking it would resurrect a deliberately dropped
    # source, so it stays at _UNKNOWN_PRIORITY and
    # test_a_retired_platform_still_loses_to_every_live_source pins that.
    "MANIFOLD_SOURCE_NAME",
}

_QUESTION = "Will the Federal Reserve cut interest rates at the September 2026 meeting?"
# Same event, reworded the way the open-web extractor rewords a headline: token
# overlap lands above CROSS_THRESHOLD and below MARKET_THRESHOLD.
_REWORDED = "Will the Federal Reserve cut interest rates at its September 2026 meeting?"


def _market(platform: str, source_type: str = "prediction_market") -> dict:
    return {
        "question": _QUESTION,
        "baseline_probability": 41.0,
        "volume": 250_000.0,
        "source": {"type": source_type, "platform": platform},
    }


def _open_web() -> dict:
    """The candidate that should lose: no price, no volume, a reworded question."""
    return {
        "question": _REWORDED,
        "baseline_probability": 50.0,
        "volume": 0.0,
        "source": {"type": "open_web", "platform": settings.OPEN_WEB_SOURCE_NAME},
    }


def _survivor(pool: list[dict]) -> str:
    kept = dedup.dedupe_candidates(pool)
    assert len(kept) == 1, f"expected the pair to dedupe to one: {kept}"
    return str(kept[0]["source"]["platform"])


class PlatformSettingsArePartitionedTests(unittest.TestCase):
    """Every platform an adapter can stamp is classified exactly once."""

    def test_scan_source_is_populated(self):
        """Guard the guard: an empty source list makes the partition vacuous."""
        self.assertGreaterEqual(
            len(store._PLATFORM_NAME_SETTINGS), 8, store._PLATFORM_NAME_SETTINGS
        )

    def test_every_platform_name_setting_is_classified(self):
        classified = (
            set(dedup._MARKET_PRIORITY_SETTINGS)
            | set(dedup._CURATED_PLATFORM_SETTINGS)
            | set(_CLASSIFIED_BY_SOURCE_TYPE)
            | _NOT_A_DISCOVERY_SOURCE
        )
        expected = set(store._PLATFORM_NAME_SETTINGS)
        self.assertEqual(
            classified, expected,
            "unclassified — would silently rank below Open Web: "
            f"{sorted(expected - classified)}; "
            "classified but no adapter stamps it any more: "
            f"{sorted(classified - expected)}",
        )

    def test_no_setting_is_classified_twice(self):
        groups = (
            set(dedup._MARKET_PRIORITY_SETTINGS),
            set(dedup._CURATED_PLATFORM_SETTINGS),
            set(_CLASSIFIED_BY_SOURCE_TYPE),
            _NOT_A_DISCOVERY_SOURCE,
        )
        total = sum(len(group) for group in groups)
        union = set().union(*groups)
        self.assertEqual(total, len(union), f"a setting appears in two tiers: {groups}")

    def test_the_tiers_are_ordered_markets_then_curated_then_open_web(self):
        """The ranks themselves, so a reordering of the tuples is caught here."""
        ranks = dedup._platform_ranks()
        market_ranks = [
            ranks[dedup._platform_token(str(getattr(settings, attr)))]
            for attr in dedup._MARKET_PRIORITY_SETTINGS
        ]
        self.assertEqual(market_ranks, sorted(market_ranks), market_ranks)
        self.assertTrue(
            max(market_ranks) < dedup._CURATED_PRIORITY < dedup._OPEN_WEB_PRIORITY,
            f"{market_ranks} / {dedup._CURATED_PRIORITY} / {dedup._OPEN_WEB_PRIORITY}",
        )
        self.assertLess(dedup._OPEN_WEB_PRIORITY, dedup._UNKNOWN_PRIORITY)


class RankFollowsTheConfiguredNameTests(unittest.TestCase):
    """The behavioural surface: which candidate survives a duplicate pair."""

    def test_a_market_beats_open_web_under_every_default_name(self):
        """Non-vacuous baseline: this arm passed before the fix and must keep passing."""
        names = [settings.KALSHI_SOURCE_NAME, settings.LIMITLESS_SOURCE_NAME,
                 settings.OPINION_SOURCE_NAME, settings.PREDICT_FUN_SOURCE_NAME]
        names += list(dedup._LITERAL_MARKET_NAMES)
        self.assertGreaterEqual(len(names), 5, names)
        for name in names:
            with self.subTest(platform=name):
                self.assertEqual(_survivor([_market(name), _open_web()]), name)
                self.assertEqual(_survivor([_open_web(), _market(name)]), name)

    def test_renaming_a_market_source_keeps_it_above_open_web(self):
        """The load-bearing arm.

        Kalshi and Limitless are enabled on a default install with no API key, so
        an operator renaming a display label is all it took: the market candidate
        was evicted by the open-web rewrite in *both* input orders, because the
        ranking looked for a spelling no record carried any more.
        """
        renames = {
            "KALSHI_SOURCE_NAME": "Kalshi Markets",
            "LIMITLESS_SOURCE_NAME": "Limitless Exchange",
            "OPINION_SOURCE_NAME": "Opinion Labs",
            "PREDICT_FUN_SOURCE_NAME": "PredictFun Exchange",
        }
        self.assertEqual(set(renames), set(dedup._MARKET_PRIORITY_SETTINGS))
        for attr, renamed in renames.items():
            with self.subTest(setting=attr):
                with patch.object(settings, attr, renamed):
                    self.assertEqual(
                        _survivor([_market(renamed), _open_web()]), renamed)
                    self.assertEqual(
                        _survivor([_open_web(), _market(renamed)]), renamed)

    def test_a_differently_cased_source_name_is_the_same_platform(self):
        """`PREDICT_FUN_SOURCE_NAME=predict.fun` is one keystroke from the default.

        The old lookup was an exact dict membership test, so the lowercase `p`
        alone dropped the platform to 99.
        """
        with patch.object(settings, "PREDICT_FUN_SOURCE_NAME", "predict.fun"):
            self.assertEqual(
                _survivor([_open_web(), _market("predict.fun")]), "predict.fun")

    def test_metaculus_outranks_open_web_in_both_orders(self):
        """Reachable as soon as METACULUS_API_TOKEN is set, with no rename.

        The dict listed five platforms and Metaculus was not one of them, so a
        curated community forecast lost to an LLM rewording of a news article --
        the opposite of what candidate_dedup_service's docstring promises.
        """
        name = settings.METACULUS_SOURCE_NAME
        curated = _market(name, source_type="prediction_question")
        self.assertEqual(_survivor([curated, _open_web()]), name)
        self.assertEqual(_survivor([_open_web(), curated]), name)

    def test_metaculus_still_loses_to_every_market(self):
        """Curated, not a market: it must not be promoted past a traded price."""
        curated = _market(settings.METACULUS_SOURCE_NAME,
                          source_type="prediction_question")
        for attr in dedup._MARKET_PRIORITY_SETTINGS:
            market_name = str(getattr(settings, attr))
            with self.subTest(setting=attr):
                self.assertEqual(
                    _survivor([curated, _market(market_name)]), market_name)

    def test_a_retired_platform_still_loses_to_every_live_source(self):
        """Manifold is deliberately unranked; pin that it stays that way.

        Its settings are legacy leftovers and nothing feeds it into discovery, so
        making the coverage settings-derived must not quietly readmit it.
        """
        retired = _market(settings.MANIFOLD_SOURCE_NAME)
        self.assertEqual(
            _survivor([retired, _market(settings.KALSHI_SOURCE_NAME)]),
            settings.KALSHI_SOURCE_NAME,
        )
        self.assertEqual(
            _survivor([retired, _open_web()]), settings.OPEN_WEB_SOURCE_NAME)

    def test_an_unset_source_name_does_not_promote_the_sources_below_it(self):
        """A blank name leaves a gap in the ranks rather than shifting them up.

        Building the map by enumerating the full list first and filtering second
        is what makes this true; filtering first would hand Limitless's rank to
        Opinion and silently reorder the tier.
        """
        opinion = dedup._platform_token(settings.OPINION_SOURCE_NAME)
        before = dedup._platform_ranks()[opinion]
        with patch.object(settings, "LIMITLESS_SOURCE_NAME", ""):
            ranks = dedup._platform_ranks()
        self.assertNotIn("", ranks)
        self.assertEqual(ranks[opinion], before)
        self.assertEqual(len(ranks), len(dedup._platform_ranks()) - 1)


if __name__ == "__main__":
    unittest.main()
