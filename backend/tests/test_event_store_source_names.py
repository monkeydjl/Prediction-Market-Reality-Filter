"""Provenance labels must never reach the event category dimension.

``event_store._category`` falls back through ``source.type`` and then
``source.platform`` when a record carries no category of its own. Both of those
name *where* an event came from, so if either survives the fallback the
dashboard files the event under its platform: on the live 235-record store 48
records were categorized ``manifold``, the single largest bucket.

The old guard listed platform names by hand and covered exactly the three
sources that existed when it was written, so every source added since leaked.
These tests scan the adapter modules and assert the coverage is exact, which is
what makes a *new* source fail here instead of shipping a platform name as a
category.
"""

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.memory import event_store as store
from tests.test_event_store import _make_record

_APP_DIR = Path(store.__file__).resolve().parents[1]
_SERVICES_DIR = _APP_DIR / "services"

# Modules that stamp `source.platform` / `source.type` onto a candidate record.
_ADAPTER_PATHS = sorted(_SERVICES_DIR.glob("*_event_source.py")) + [
    _SERVICES_DIR / "event_extraction_service.py",
]


def _resolve(node: ast.expr) -> tuple[str, str] | None:
    """Resolve a source-dict value to (settings attribute or "", literal value).

    Handles the two spellings the adapters use: a bare string literal, and
    ``settings.SOMETHING``. Anything else (an f-string, a variable) is returned
    as None so the scan reports it rather than silently passing.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "", node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "settings"
    ):
        return node.attr, str(getattr(settings, node.attr, "") or "")
    return None


def _scan_source_dicts() -> list[dict[str, tuple[str, str]]]:
    """Every source dict an adapter builds, as {key: (settings attr, value)}.

    A dict counts as a source dict when it carries a ``"platform"`` key. That
    anchor matters: keying off ``"type"`` alone also matches HTTP query params
    (``{"type": "forecast", ...}`` in the Metaculus adapter), which are not
    provenance labels and never reach a record.
    """
    found: list[dict[str, tuple[str, str]]] = []
    for path in _ADAPTER_PATHS:
        # utf-8-sig, not utf-8: `app/services/llm_gateway_service.py` carries a
        # BOM that ast.parse rejects, so a new adapter arriving with one would
        # break this scan rather than be covered by it.
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            entries: dict[str, tuple[str, str]] = {}
            for dict_key, dict_value in zip(node.keys, node.values):
                if not (
                    isinstance(dict_key, ast.Constant)
                    and dict_key.value in {"platform", "type"}
                ):
                    continue
                resolved = _resolve(dict_value)
                entries[str(dict_key.value)] = (
                    ("<unresolved>", ast.dump(dict_value))
                    if resolved is None
                    else resolved
                )
            if "platform" in entries:
                entries["module"] = ("", path.name)
                found.append(entries)
    return found


def _scanned(key: str) -> list[tuple[str, str, str]]:
    """(module, settings attribute, value) for `key` across every source dict."""
    return [
        (dicts["module"][1], dicts[key][0], dicts[key][1])
        for dicts in _scan_source_dicts()
        if key in dicts
    ]


class AdapterProvenanceScanTests(unittest.TestCase):
    """The generic-label coverage is pinned against the adapters themselves."""

    def test_scan_finds_the_adapters(self):
        """Guard the guard: an empty scan would make every test below vacuous."""
        self.assertGreaterEqual(len(_ADAPTER_PATHS), 8, _ADAPTER_PATHS)
        for path in _ADAPTER_PATHS:
            self.assertTrue(path.exists(), path)
        platforms = _scanned("platform")
        self.assertGreaterEqual(len(platforms), 8, platforms)

    def test_every_platform_an_adapter_writes_is_a_generic_label(self):
        leaked = [
            (module, attr, value)
            for module, attr, value in _scanned("platform")
            if store._specific_category(value) is not None
        ]
        self.assertEqual(
            leaked, [],
            "these platform names would be served as event categories: "
            f"{leaked}",
        )

    def test_every_source_type_an_adapter_writes_is_generic_or_a_real_category(self):
        # `sports_event` is the one type that IS a category: _category returns it
        # directly before the fallback chain runs.
        leaked = [
            (module, attr, value)
            for module, attr, value in _scanned("type")
            if value != "sports_event"
            and store._specific_category(value) is not None
        ]
        self.assertEqual(
            leaked, [],
            f"these source types would be served as event categories: {leaked}",
        )

    def test_platform_name_settings_matches_what_the_adapters_read(self):
        """Exact partition, not a subset: extra or missing entries both fail.

        A subset check would pass while an adapter went uncovered, which is how
        the hand-typed list drifted three sources behind in the first place.
        """
        scanned = {
            attr for _, attr, _ in _scanned("platform") if attr
        }
        self.assertEqual(
            scanned, set(store._PLATFORM_NAME_SETTINGS),
            "missing from _PLATFORM_NAME_SETTINGS: "
            f"{sorted(scanned - set(store._PLATFORM_NAME_SETTINGS))}; "
            "no longer read by any adapter: "
            f"{sorted(set(store._PLATFORM_NAME_SETTINGS) - scanned)}",
        )

    def test_literal_platform_names_matches_what_the_adapters_hardcode(self):
        hardcoded = {
            value for _, attr, value in _scanned("platform")
            if not attr
        }
        self.assertEqual(hardcoded, set(store._LITERAL_PLATFORM_NAMES), hardcoded)


def _platform_names() -> list[str]:
    """Every platform string an adapter can stamp, under current settings."""
    names = list(store._LITERAL_PLATFORM_NAMES)
    names += [
        str(getattr(settings, attr, "") or "")
        for attr in store._PLATFORM_NAME_SETTINGS
    ]
    return [name for name in names if name]


class CategoryIsNeverAProvenanceLabelTests(unittest.TestCase):
    """The user-visible consequence: the category dropdown and its filter."""

    # A title the deterministic inference maps to a real subject, so a record
    # that stops falling back to its platform has somewhere better to land.
    TITLE = "No change in Bank of England's interest rates after July 2026 meeting?"

    def _counts(self, records: list[dict]) -> dict[str, int]:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events(records)
                return store.count_events_by_category(status="active")

    def _record(self, event_id: str, platform: str, **source) -> dict:
        record = _make_record(event_id, value_score=70, estimated=62)
        record["event_title"] = self.TITLE
        record["source"] = {"type": "prediction_market", "platform": platform,
                            **source}
        record["legacy_analysis"] = {}
        return record

    def test_no_platform_gets_a_category_bucket_of_its_own(self):
        """One bucket for the shared subject, not one bucket per platform.

        Seeding every platform at once is what makes this non-vacuous: a guard
        covering only some of them still produces extra buckets here, and the
        assertion names them.
        """
        names = _platform_names()
        self.assertGreaterEqual(len(names), 9, names)
        records = [
            self._record(f"evt-{index}", platform)
            for index, platform in enumerate(names)
        ]

        self.assertEqual(self._counts(records), {"monetary": len(names)})

    def test_open_web_type_is_not_a_category_when_the_extractor_sends_unknown(self):
        """Reachable on the default configuration, with no source enabled.

        ``event_extraction_service`` substitutes ``event_type="unknown"`` when the
        LLM omits it. ``unknown`` was already generic, so the chain fell through
        to ``source.type`` -- and ``open_web`` was not in the generic set, so the
        collection channel became the subject.

        The renamed arm is the load-bearing one. Under the default name the
        platform token is *also* ``open_web``, so the platform coverage happens to
        mask the type; renaming the source separates them and tests the type on
        its own.
        """
        for name in (settings.OPEN_WEB_SOURCE_NAME, "Web Search"):
            with self.subTest(open_web_source_name=name):
                with patch.object(settings, "OPEN_WEB_SOURCE_NAME", name):
                    record = self._record(
                        "open-web", name, type="open_web", event_type="unknown",
                    )
                    self.assertEqual(self._counts([record]), {"monetary": 1})

    def test_manual_type_is_not_a_category(self):
        """`_make_record`'s own default source is ``{"type": "manual"}``, and one
        live record carries it: a hand-entered event is not an event *about*
        manual.
        """
        record = _make_record("hand-entered", value_score=70, estimated=62)
        record["event_title"] = self.TITLE
        record["legacy_analysis"] = {}

        self.assertEqual(self._counts([record]), {"monetary": 1})

    def test_renaming_a_source_through_the_environment_keeps_it_generic(self):
        """The coverage follows the setting, because the adapter reads it too.

        A hardcoded list of platform *strings* would go stale the moment an
        operator set KALSHI_SOURCE_NAME; the adapter would stamp the new name and
        the guard would still be looking for the old one.
        """
        renamed = "Kalshi Markets"
        with patch.object(settings, "KALSHI_SOURCE_NAME", renamed):
            counts = self._counts([self._record("renamed", renamed)])

        self.assertEqual(counts, {"monetary": 1})

    def test_a_real_category_that_looks_like_a_platform_still_survives(self):
        """The guard swallows provenance labels, not every string that resembles
        one: an explicit base-rate category is honoured even when a platform of
        the same spelling exists.
        """
        record = self._record("explicit", "Polymarket")
        record["legacy_analysis"] = {"base_rate_category": "crypto_price_btc"}

        self.assertEqual(self._counts([record]), {"crypto_price_btc": 1})
