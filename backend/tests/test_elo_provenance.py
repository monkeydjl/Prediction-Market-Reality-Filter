"""An Elo rating with a non-real source must not reach an engine.

``elo_ratings_service.get_elo_rating`` never fails.  For a team it does not know
it returns ``{"elo_rating": 1500.0, "source": "default"}``; for a team it has
only a FIFA rank for, a value computed from that rank with ``source:
"estimated"``.  Both football adapters read ``elo_rating`` and discarded
``source``, so those invented ratings arrived at the engines indistinguishable
from measured ones.

Measured on the production path before the fix (``Atlantis`` vs ``Freedonia``,
neither in the 49-entry hardcoded table), via
``app.api.routes.predictions._get_kernel`` against isolated copies of the two
live databases:

===================================  ==========  =================  ============
state                                confidence  data_completeness  data_quality
===================================  ==========  =================  ============
invented 1500/1500, no odds              0.5475              0.400  partial
Elo absent, no odds                      0.4138              0.000  partial
invented 1500/1500 + odds                0.6673              1.000  **real**
Elo absent + odds                        0.5736              0.400  partial
===================================  ==========  =================  ============

The invented pair bought +0.134 of confidence with no odds and +0.094 with odds,
and with odds present promoted the prediction to ``data_quality="real"`` while
the user-facing explanation read ``"Elo 1500.0 vs 1500.0"`` as a supporting
factor.

Reachability, measured on the live fixture store: all 48 distinct fixture team
names currently resolve to ``hardcoded_eloratings``, so no live prediction is
affected today -- but **five of those 48** (``Bosnia-Herzegovina``, ``Cape Verde
Islands``, ``Congo DR``, ``Curaçao``, ``United States``) are absent from the
hardcoded table by name and reach a real rating only through the 13-entry
hand-maintained ``_ELO_TEAM_ALIASES`` map.  One name-format change in the
fixture feed and those five silently become 1500/1500 scored as real.
"""
from __future__ import annotations

import ast
import pathlib
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.kernel.domain import (
    CompetitionIdentity,
    MatchIdentity,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
)
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.kernel.feature_provenance import (
    ELO_SOURCE_NOT_REAL_NOTE,
    resolve_elo_provenance,
)
from app.sports.football.feature_builder import FootballFeatureBuilder

# backend/tests/ -> backend/
_BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: Every sport whose feature builder must run the provenance check.  Declared as
#: data and asserted as an exact partition against a filesystem scan below, in
#: both directions, so a sixth sport cannot arrive unchecked and a renamed one
#: cannot rot here.
_EXPECTED_BUILDERS: frozenset[str] = frozenset({
    "app/sports/baseball/feature_builder.py",
    "app/sports/basketball/feature_builder.py",
    "app/sports/football/feature_builder.py",
    "app/sports/hockey/feature_builder.py",
    "app/sports/lol/feature_builder.py",
})

_REAL = "hardcoded_eloratings/hardcoded_eloratings"


def _make_match(home: str = "Atlantis", away: str = "Freedonia") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    return MatchIdentity(
        match_id="provenance-test",
        season=season,
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="ATL", name=home, competition=comp),
        away=TeamIdentity(code="FRE", name=away, competition=comp),
        kickoff_utc=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
    )


def _build(team: dict, market: dict | None = None):
    raw = {
        "team": dict(team),
        "market": dict(market or {}),
        "player": {},
        "environment": {},
        "general": {},
    }
    return FootballFeatureBuilder().build(_make_match(), raw)


ODDS = {
    "odds_home": 2.1,
    "odds_draw": 3.4,
    "odds_away": 3.2,
    "odds_source": "probe",
    "odds_fresh": True,
}


class ResolveEloProvenanceTests(unittest.TestCase):
    """The decision itself, at the one place five builders call."""

    def test_a_real_source_passes_both_ratings_through(self):
        got = resolve_elo_provenance(
            {"elo_home": 1986.0, "elo_away": 1650.0, "elo_source": _REAL}
        )
        self.assertEqual((got.elo_home, got.elo_away), (1986.0, 1650.0))
        self.assertEqual(got.elo_source, _REAL)
        self.assertEqual(got.notes, ())

    def test_a_default_source_drops_the_pair_and_records_why(self):
        got = resolve_elo_provenance(
            {"elo_home": 1500.0, "elo_away": 1500.0, "elo_source": "default/default"}
        )
        self.assertIsNone(got.elo_home)
        self.assertIsNone(got.elo_away)
        self.assertEqual(got.elo_source, "default/default")
        self.assertEqual(got.notes, (ELO_SOURCE_NOT_REAL_NOTE,))

    def test_one_non_real_side_invalidates_the_pair(self):
        """``all_sources_look_real`` requires every segment, and it must.

        The engine consumes the two ratings as a *difference*.  A measured 1986
        against an invented 1500 is not "half real evidence", it is a fabricated
        350-point gap -- worse than no rating, because BTD reads it as a strong
        home favourite.
        """
        got = resolve_elo_provenance(
            {
                "elo_home": 1986.0,
                "elo_away": 1500.0,
                "elo_source": "hardcoded_eloratings/default",
            }
        )
        self.assertIsNone(got.elo_home)
        self.assertIsNone(got.elo_away)
        self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, got.notes)

    def test_the_cached_prefix_does_not_launder_a_non_real_source(self):
        """``get_elo_rating`` returns ``f"cached_{row.source}"`` on a cache hit.

        The live ``elo_ratings`` table holds one such row (``Costa Rica``,
        ``estimated``, 1948.0), so ``cached_estimated`` is a reachable label and
        not a hypothetical one.  ``source_looks_real`` matches tokens as
        substrings, which is what makes the prefix harmless -- pinned here
        because a switch to equality comparison would silently readmit it.
        """
        for label in ("cached_estimated", "cached_default"):
            with self.subTest(label=label):
                got = resolve_elo_provenance(
                    {"elo_home": 1948.0, "elo_away": 1500.0,
                     "elo_source": f"{label}/{label}"}
                )
                self.assertIsNone(got.elo_home)
                self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, got.notes)

    def test_an_unknown_token_from_a_failed_fetch_is_not_real(self):
        """Both adapters write ``"unknown"`` when one side's fetch raised."""
        got = resolve_elo_provenance(
            {"elo_home": 1986.0, "elo_away": None,
             "elo_source": "hardcoded_eloratings/unknown"}
        )
        self.assertIsNone(got.elo_home)
        self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, got.notes)

    def test_no_source_reported_is_not_the_same_as_not_real(self):
        """Absence is the MLB/NBA/NHL/LoL convention and must pass through.

        Those adapters read ``kernel_elo_ratings``, which yields ``None`` for an
        unknown team, so they cannot invent a value and have nothing to label.
        Treating absence as non-real would delete every rating in three sports.
        """
        for source in (None, ""):
            with self.subTest(source=source):
                team = {"elo_home": 1600.0, "elo_away": 1550.0}
                if source is not None:
                    team["elo_source"] = source
                got = resolve_elo_provenance(team)
                self.assertEqual((got.elo_home, got.elo_away), (1600.0, 1550.0))
                self.assertIsNone(got.elo_source)
                self.assertEqual(got.notes, ())

    def test_a_non_real_source_with_nothing_to_drop_still_reports_the_reason(self):
        got = resolve_elo_provenance({"elo_source": "default/default"})
        self.assertIsNone(got.elo_home)
        self.assertEqual(got.notes, (ELO_SOURCE_NOT_REAL_NOTE,))

    def test_an_empty_team_dict_is_handled(self):
        got = resolve_elo_provenance({})
        self.assertEqual(
            (got.elo_home, got.elo_away, got.elo_source, got.notes),
            (None, None, None, ()),
        )


class InventedEloDoesNotReachTheEngineTests(unittest.TestCase):
    """The behaviour, end to end through the production builder and engine."""

    def setUp(self):
        self.engine = EloOddsEngine()
        self.match = _make_match()

    def _predict(self, team: dict, market: dict | None = None):
        features = _build(team, market)
        return features, self.engine.predict(features, self.match)

    def test_a_defaulted_pair_is_reported_as_unavailable(self):
        features, result = self._predict(
            {"elo_home": 1500.0, "elo_away": 1500.0, "elo_source": "default/default"}
        )
        self.assertIsNone(features.team.elo_rating_home)
        elo_item = next(i for i in result.explanation if i.factor == "elo")
        self.assertFalse(elo_item.available)
        self.assertEqual(elo_item.detail, "Elo unavailable")
        self.assertNotIn("1500", elo_item.detail)

    def test_a_defaulted_pair_no_longer_promotes_quality_to_real(self):
        """The worst of the four measured consequences.

        With odds present, an invented Elo lifted ``data_quality`` from
        ``partial`` to ``real`` and ``data_completeness`` from 0.4 to 1.0 -- the
        prediction claimed complete inputs on a rating nobody measured.
        """
        invented, _ = self._predict(
            {"elo_home": 1500.0, "elo_away": 1500.0, "elo_source": "default/default"},
            ODDS,
        )
        self.assertEqual(invented.data_quality, "partial")
        self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, invented.quality_notes)

        real, _ = self._predict(
            {"elo_home": 1986.0, "elo_away": 1650.0, "elo_source": _REAL}, ODDS
        )
        self.assertEqual(real.data_quality, "real")
        self.assertNotIn(ELO_SOURCE_NOT_REAL_NOTE, real.quality_notes)

    def test_a_defaulted_pair_confers_no_confidence_over_an_absent_one(self):
        """Pins the two measured numbers as *equal*, not merely both low.

        Before the fix these were 0.5475 (invented) and 0.4138 (absent).  The
        claim is that the two states are now indistinguishable, which a "both
        below some threshold" assertion could not tell from a partial fix.
        """
        _, invented = self._predict(
            {"elo_home": 1500.0, "elo_away": 1500.0, "elo_source": "default/default"}
        )
        _, absent = self._predict({})
        self.assertEqual(invented.confidence, absent.confidence)
        self.assertEqual(
            invented.betting_analysis["confidence_breakdown"]["data_completeness"],
            absent.betting_analysis["confidence_breakdown"]["data_completeness"],
        )
        self.assertEqual(invented.outcome_probabilities, absent.outcome_probabilities)

    def test_a_real_pair_still_earns_its_confidence(self):
        """The fix must not flatten everything: real ratings must still count."""
        _, real = self._predict(
            {"elo_home": 1986.0, "elo_away": 1650.0, "elo_source": _REAL}
        )
        _, absent = self._predict({})
        self.assertGreater(real.confidence, absent.confidence)
        real_item = next(i for i in real.explanation if i.factor == "elo")
        self.assertTrue(real_item.available)
        self.assertIn("1986.0", real_item.detail)

    def test_a_rating_with_no_reported_source_still_reaches_the_engine(self):
        """The three sports that report no provenance must be unaffected."""
        features, result = self._predict({"elo_home": 1600.0, "elo_away": 1550.0})
        self.assertEqual(features.team.elo_rating_home, 1600.0)
        self.assertIsNone(features.team.elo_source)
        elo_item = next(i for i in result.explanation if i.factor == "elo")
        self.assertTrue(elo_item.available)

    def test_the_source_survives_onto_the_feature_set(self):
        features = _build(
            {"elo_home": 1986.0, "elo_away": 1650.0, "elo_source": _REAL}
        )
        self.assertEqual(features.team.elo_source, _REAL)


def _scan_builders() -> dict[str, ast.Module]:
    """Parse every sport feature builder, raising rather than skipping.

    Two files in this repository carry a UTF-8 BOM, and a scan that swallows
    ``SyntaxError`` loses exactly the rows it exists to count -- so read with
    ``utf-8-sig`` and let a parse failure fail the test.
    """
    found = {}
    for path in sorted((_BACKEND / "app" / "sports").glob("*/feature_builder.py")):
        rel = path.relative_to(_BACKEND).as_posix()
        found[rel] = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
    return found


def _calls(tree: ast.Module, name: str) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _team_features_kwargs(tree: ast.Module) -> list[dict[str, ast.expr]]:
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "TeamFeatures"
        ):
            out.append({kw.arg: kw.value for kw in node.keywords if kw.arg})
    return out


class EverySportRunsTheCheckTests(unittest.TestCase):
    """An exact partition, both directions, over a filesystem scan.

    The list of sports is not declared anywhere in production -- it is whichever
    directories exist under ``app/sports/`` -- so a sixth sport is exactly the
    shape that escapes a hand-maintained list.
    """

    def setUp(self):
        self.builders = _scan_builders()

    def test_the_scan_found_something(self):
        """Guard the denominator: two empty sets compare equal forever."""
        self.assertGreaterEqual(len(self.builders), 5)

    def test_the_declared_set_is_exactly_what_exists(self):
        self.assertEqual(
            set(self.builders),
            set(_EXPECTED_BUILDERS),
            "a sport feature builder was added or renamed; add it to "
            "_EXPECTED_BUILDERS and make sure it calls resolve_elo_provenance",
        )

    def test_every_builder_calls_the_shared_resolver(self):
        for rel, tree in self.builders.items():
            with self.subTest(builder=rel):
                self.assertGreaterEqual(
                    _calls(tree, "resolve_elo_provenance"),
                    1,
                    f"{rel} does not run the Elo provenance check",
                )

    def test_no_builder_wires_the_raw_rating_straight_into_TeamFeatures(self):
        """The discriminating half.

        A builder can import the resolver, call it, and still pass
        ``team_raw.get("elo_home")`` to ``elo_rating_home`` -- which compiles,
        keeps every other test green, and reinstates the defect.  So assert on
        what reaches the dataclass, not on whether the helper was called.
        """
        for rel, tree in self.builders.items():
            for kwargs in _team_features_kwargs(tree):
                for field in ("elo_rating_home", "elo_rating_away"):
                    with self.subTest(builder=rel, field=field):
                        value = kwargs.get(field)
                        self.assertIsNotNone(
                            value, f"{rel} does not set {field} by keyword"
                        )
                        self.assertEqual(
                            ast.unparse(value).split(".")[0],
                            "elo",
                            f"{rel} passes {ast.unparse(value)!r} to {field}; it must "
                            "pass the resolver's result so a non-real source is dropped",
                        )

    def test_every_builder_reports_the_source_onto_TeamFeatures(self):
        for rel, tree in self.builders.items():
            constructions = _team_features_kwargs(tree)
            with self.subTest(builder=rel):
                self.assertTrue(constructions, f"{rel} constructs no TeamFeatures")
                for kwargs in constructions:
                    self.assertIn(
                        "elo_source",
                        kwargs,
                        f"{rel} drops the provenance label on the floor",
                    )

    def test_every_builder_passes_the_real_team_dict_to_the_resolver(self):
        """Shape checks cannot see a resolver called on the wrong dict.

        An injection that changed one builder to ``resolve_elo_provenance({})``
        passed every other assertion in this class -- the call is present, the
        keyword is wired, and the ratings all become ``None`` for a reason no
        test was watching.  So pin the argument too.
        """
        for rel, tree in self.builders.items():
            with self.subTest(builder=rel):
                args = [
                    node.args
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "resolve_elo_provenance"
                ]
                self.assertTrue(args, f"{rel} does not call the resolver")
                for call_args in args:
                    self.assertEqual(
                        [ast.unparse(a) for a in call_args],
                        ["team_raw"],
                        f"{rel} calls resolve_elo_provenance on something other "
                        "than its own raw team dict",
                    )

    def test_every_builder_forwards_the_resolver_notes(self):
        """A dropped rating must leave a visible reason in ``quality_notes``."""
        for rel, tree in self.builders.items():
            with self.subTest(builder=rel):
                self.assertIn(
                    "elo.notes",
                    ast.unparse(tree),
                    f"{rel} discards the provenance note, so a dropped rating "
                    "would be silent",
                )


class EverySportBehavesTheSameWayTests(unittest.TestCase):
    """Behaviour, not shape, for all five builders.

    The AST class above pins how the builders are written; this one pins what
    they do, because the two failure modes are different.  A builder can satisfy
    every structural assertion and still delete a real rating (the resolver
    called on an empty dict) or keep an invented one.
    """

    def _builders(self):
        from app.sports.baseball.feature_builder import BaseballFeatureBuilder
        from app.sports.basketball.feature_builder import BasketballFeatureBuilder
        from app.sports.football.feature_builder import (
            FootballFeatureBuilder as FB,
        )
        from app.sports.hockey.feature_builder import HockeyFeatureBuilder
        from app.sports.lol.feature_builder import LolFeatureBuilder

        return {
            "baseball": BaseballFeatureBuilder(),
            "basketball": BasketballFeatureBuilder(),
            "football": FB(),
            "hockey": HockeyFeatureBuilder(),
            "lol": LolFeatureBuilder(),
        }

    def _build(self, builder, team: dict):
        raw = {
            "team": dict(team),
            "market": {},
            "player": {},
            "environment": {},
            "general": {},
            "custom": {},
        }
        return builder.build(_make_match(), raw)

    def test_a_real_rating_survives_in_every_sport(self):
        for sport, builder in self._builders().items():
            with self.subTest(sport=sport):
                features = self._build(
                    builder, {"elo_home": 1800.0, "elo_away": 1700.0, "elo_source": _REAL}
                )
                self.assertEqual(features.team.elo_rating_home, 1800.0)
                self.assertEqual(features.team.elo_rating_away, 1700.0)
                self.assertEqual(features.team.elo_source, _REAL)

    def test_an_unlabelled_rating_survives_in_every_sport(self):
        """The MLB/NBA/NHL/LoL case: no provenance reported, nothing dropped."""
        for sport, builder in self._builders().items():
            with self.subTest(sport=sport):
                features = self._build(builder, {"elo_home": 1800.0, "elo_away": 1700.0})
                self.assertEqual(features.team.elo_rating_home, 1800.0)
                self.assertIsNone(features.team.elo_source)

    def test_a_non_real_rating_is_dropped_in_every_sport(self):
        for sport, builder in self._builders().items():
            with self.subTest(sport=sport):
                features = self._build(
                    builder,
                    {"elo_home": 1500.0, "elo_away": 1500.0, "elo_source": "default/default"},
                )
                self.assertIsNone(features.team.elo_rating_home)
                self.assertIsNone(features.team.elo_rating_away)
                self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, features.quality_notes)


class BothFootballAdaptersReportProvenanceTests(unittest.TestCase):
    """The other end of the seam: the two adapters that can invent a rating.

    Only these two call ``elo_ratings_service.get_elo_rating``, which returns a
    value for every input.  MLB/NBA/NHL read a ratings table that returns
    ``None`` for an unknown team, so they have nothing to label.
    """

    _SOURCES = (
        "app/sports/football/adapters/_shared.py",
        "app/sports/football/adapters/world_cup_adapter.py",
    )

    def test_each_writes_an_elo_source_key(self):
        for rel in self._SOURCES:
            with self.subTest(adapter=rel):
                text = (_BACKEND / rel).read_text(encoding="utf-8-sig")
                self.assertIn('raw["team"]["elo_source"]', text)

    def test_the_shared_fetch_labels_a_real_pair(self):
        from app.sports.football.adapters import _shared

        async def ok(name, scope="national", alias=None):
            return {"elo_rating": 1800.0, "source": "clubelo"}

        with patch.object(_shared, "fetch_team_elo", new=ok), \
             patch.object(_shared, "fetch_match_odds", new=AsyncMock(return_value=None)):
            raw = _shared.fetch_elo_and_odds(_make_match(), elo_scope="club")

        self.assertEqual(raw["team"]["elo_source"], "clubelo/clubelo")
        self.assertEqual(
            resolve_elo_provenance(raw["team"]).elo_home, 1800.0
        )

    def test_the_shared_fetch_labels_a_half_failed_pair_non_real(self):
        """A raised fetch on one side must invalidate the pair, not half of it.

        The engine consumes the two ratings as a difference, so keeping the
        surviving 1800 against a missing value would manufacture a gap.  Runs the
        real ``asyncio.gather(return_exceptions=True)`` path rather than asserting
        on the source text, so the ``"unknown"`` fallback is pinned by behaviour.
        """
        from app.sports.football.adapters import _shared

        async def half(name, scope="national", alias=None):
            if name == "Freedonia":
                raise RuntimeError("upstream down")
            return {"elo_rating": 1800.0, "source": "clubelo"}

        with patch.object(_shared, "fetch_team_elo", new=half), \
             patch.object(_shared, "fetch_match_odds", new=AsyncMock(return_value=None)):
            raw = _shared.fetch_elo_and_odds(_make_match(), elo_scope="club")

        self.assertEqual(raw["team"]["elo_source"], "clubelo/unknown")
        self.assertEqual(raw["team"].get("elo_home"), 1800.0)
        resolved = resolve_elo_provenance(raw["team"])
        self.assertIsNone(resolved.elo_home)
        self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, resolved.notes)

    def test_the_world_cup_adapter_labels_a_defaulted_pair(self):
        """The one adapter on the 1500-default path.

        ``get_elo_rating`` is patched at its source module because the adapter
        imports it lazily inside the function body; patching it on the adapter
        would be silent and the call would go out over the network.
        """
        from app.sports.football.adapters.world_cup_adapter import WorldCupAdapter

        defaulted = AsyncMock(return_value={"elo_rating": 1500.0, "source": "default"})
        with patch("app.services.elo_ratings_service.get_elo_rating", new=defaulted), \
             patch("app.services.odds_cache_service.get_cached_odds",
                   new=AsyncMock(return_value=None)):
            raw = WorldCupAdapter().fetch_all_data(_make_match())

        self.assertEqual(raw["team"]["elo_source"], "default/default")
        resolved = resolve_elo_provenance(raw["team"])
        self.assertIsNone(resolved.elo_home)
        self.assertIn(ELO_SOURCE_NOT_REAL_NOTE, resolved.notes)


if __name__ == "__main__":
    unittest.main()
