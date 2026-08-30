"""The football ``xg`` factor must say which quantity it measured.

``enrich_situational_features`` writes three different things under the same
``custom["xg_home"]`` / ``custom["xg_away"]`` pair -- the club's goals per game,
a hand-typed static table, or a configured live provider's true xG/90 -- and only
the last is expected goals.  The goals proxy was written with **no ``xg_source``
key at all**, and ``xg_source`` had zero readers in production (two write sites,
read only by tests and docs), so ``FootballMultiFactorEngine`` reported all three
as ``factor="xg"``, ``available=True``, ``direction="support"``, which the frontend
renders under an xG label.

Measured before the fix, over the live fixture corpus in ``kernel_match_fixtures``:
**2602 of 2627** club fixtures (99.0%) fall to the unlabelled goals proxy, and EPL
is **0 of 760** -- the static table's normalizer only lowercases, so it never
matches the ``FC`` suffix Football-Data.org appends (``"Arsenal FC"`` vs the
table's ``"arsenal"``).  Per competition: epl 0/760, ligue1 0/612, laliga 6/380,
seriea 2/380, bundesliga 12/306, ucl 5/189.

Measured on the production engine path (Arsenal 2.3 vs Everton 0.9 goals per game
written under the xG keys, Elo 1900/1700 from a real source, no odds):

===================================  ==========  =================  ============
state                                confidence  data_completeness  home_win
===================================  ==========  =================  ============
goals proxy, unlabelled                  0.5879             0.1727     0.5330
same numbers labelled static_table       0.5879             0.1727     0.5330
xG absent                                0.5799             0.0818     0.5468
===================================  ==========  =================  ============

The first two rows agreeing in every field is the proof the label was inert.

Unlike the invented Elo rating in ``test_elo_provenance.py``, goals per game is a
real measurement -- so the fix is not to drop it.  These tests pin that the value
still votes, that the numbers did not move, and that the explanation now names the
origin.
"""
from __future__ import annotations

import ast
import copy
import datetime
import pathlib
import unittest
from unittest.mock import patch

from app.kernel.domain import (
    CompetitionIdentity,
    MatchIdentity,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
)
from app.kernel.feature_provenance import (
    MEASURED_XG_SOURCES,
    XG_SOURCE_GOALS_PROXY,
    XG_SOURCE_UNKNOWN,
    resolve_xg_provenance,
)
from app.sports.football.engines.football_multi_factor_engine import (
    FootballMultiFactorEngine,
)
from app.sports.football.feature_builder import FootballFeatureBuilder

_FOOTBALL = SportIdentity(code="football", name="Football")
_EPL = CompetitionIdentity(code="epl", name="Premier League", sport=_FOOTBALL)

#: Every production site that writes an xG value for football. Declared as data so
#: a fourth branch cannot be added without either appearing here or reddening the
#: partition test below.
_XG_WRITER = "app/sports/football/adapters/_shared.py"

#: The one production reader of the pair.
_XG_READER = "app/sports/football/engines/football_multi_factor_engine.py"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _match() -> MatchIdentity:
    return MatchIdentity(
        match_id="epl-xg-probe",
        season=SeasonIdentity(competition=_EPL, season_key="2025"),
        stage="regular",
        round=None,
        home=TeamIdentity(code="ARS", name="Arsenal FC", competition=_EPL),
        away=TeamIdentity(code="EVE", name="Everton FC", competition=_EPL),
        kickoff_utc=datetime.datetime(2026, 9, 5, 15, 0, tzinfo=datetime.timezone.utc),
    )


_BASE_RAW: dict = {
    "team": {
        "elo_home": 1900.0,
        "elo_away": 1700.0,
        "elo_source": "hardcoded_eloratings/hardcoded_eloratings",
        "form_home": 0.60,
        "form_away": 0.40,
    },
    "market": {},
    "player": {},
    "environment": {},
    "general": {},
    "custom": {},
}


def _predict(custom: dict):
    """Run the real feature builder and engine, as production does."""
    raw = copy.deepcopy(_BASE_RAW)
    raw["custom"] = dict(custom)
    features = FootballFeatureBuilder().build(_match(), raw)
    return FootballMultiFactorEngine().predict(features, _match())


def _xg_item(result):
    return next((c for c in result.explanation if c.factor == "xg"), None)


class ResolveXgProvenanceTests(unittest.TestCase):
    """The classifier itself."""

    def test_live_provider_is_the_only_measured_source(self):
        self.assertEqual(MEASURED_XG_SOURCES, frozenset({"live_provider"}))
        got = resolve_xg_provenance({"xg_source": "live_provider"})
        self.assertEqual(got.source, "live_provider")
        self.assertTrue(got.measured)

    def test_goals_proxy_is_not_measured(self):
        got = resolve_xg_provenance({"xg_source": XG_SOURCE_GOALS_PROXY})
        self.assertEqual(got.source, "goals_proxy")
        self.assertFalse(
            got.measured,
            "goals per game is goals, not expected goals",
        )

    def test_static_table_is_not_measured(self):
        # The table's own docstring says "Soft static attack xG/90 ... Not a live
        # season snapshot", so it is xG-shaped but hand-typed.
        got = resolve_xg_provenance({"xg_source": "static_table"})
        self.assertEqual(got.source, "static_table")
        self.assertFalse(got.measured)

    def test_absent_or_blank_source_reads_as_unknown_not_measured(self):
        for custom in ({}, {"xg_source": None}, {"xg_source": ""},
                       {"xg_source": "   "}, None):
            with self.subTest(custom=custom):
                got = resolve_xg_provenance(custom)
                self.assertEqual(got.source, XG_SOURCE_UNKNOWN)
                self.assertFalse(
                    got.measured,
                    "an unstated origin must never read as a measured one",
                )

    def test_an_unrecognised_token_is_not_measured(self):
        # A future provider is measured only after it is added to the frozenset,
        # so an unknown token fails closed rather than open.
        for token in ("shots_proxy", "LIVE_PROVIDER", "live", "elo_derived"):
            with self.subTest(token=token):
                got = resolve_xg_provenance({"xg_source": token})
                self.assertEqual(got.source, token)
                self.assertFalse(got.measured)

    def test_surrounding_whitespace_does_not_hide_a_measured_source(self):
        got = resolve_xg_provenance({"xg_source": "  live_provider  "})
        self.assertEqual(got.source, "live_provider")
        self.assertTrue(got.measured)


class TheExplanationNamesTheQuantityTests(unittest.TestCase):
    """What an operator and a user actually read."""

    def test_goals_proxy_is_labelled_as_not_measured_xg(self):
        item = _xg_item(_predict({
            "xg_home": 2.3, "xg_away": 0.9, "xg_source": XG_SOURCE_GOALS_PROXY,
        }))
        assert item is not None
        self.assertIn("src=goals_proxy", item.detail)
        self.assertIn("not measured xG", item.detail)

    def test_an_unlabelled_pair_is_reported_as_unknown_not_as_xg(self):
        # This is the pre-fix production state for 99.0% of live club fixtures. It
        # must not read as measured just because no adapter said otherwise.
        item = _xg_item(_predict({"xg_home": 2.3, "xg_away": 0.9}))
        assert item is not None
        self.assertIn(f"src={XG_SOURCE_UNKNOWN}", item.detail)
        self.assertIn("not measured xG", item.detail)

    def test_static_table_is_named_and_flagged(self):
        item = _xg_item(_predict({
            "xg_home": 1.85, "xg_away": 1.20, "xg_source": "static_table",
        }))
        assert item is not None
        self.assertIn("src=static_table", item.detail)
        self.assertIn("not measured xG", item.detail)

    def test_a_measured_provider_carries_no_disclaimer(self):
        item = _xg_item(_predict({
            "xg_home": 1.6, "xg_away": 1.1, "xg_source": "live_provider",
        }))
        assert item is not None
        self.assertIn("src=live_provider", item.detail)
        self.assertNotIn(
            "not measured",
            item.detail,
            "a real xG feed must not be disclaimed, or the note means nothing",
        )

    def test_an_unavailable_factor_says_unavailable_and_names_nothing(self):
        item = _xg_item(_predict({}))
        assert item is not None
        self.assertFalse(item.available)
        self.assertEqual(item.detail, "xg unavailable")
        self.assertNotIn("src=", item.detail)

    def test_the_probability_numbers_are_still_in_the_detail(self):
        # The provenance note is appended, not substituted: the three shares an
        # operator reads the factor by must survive.
        item = _xg_item(_predict({
            "xg_home": 2.3, "xg_away": 0.9, "xg_source": XG_SOURCE_GOALS_PROXY,
        }))
        assert item is not None
        for token in ("H=", "D=", "A="):
            self.assertIn(token, item.detail)


class TheLabelChangesNothingButTheWordsTests(unittest.TestCase):
    """The fix is a naming fix. No weight, formula, or availability may move.

    Stated as **exact equality** across every provenance token rather than as a
    threshold, because a threshold cannot tell "the label is inert" from "the
    label happens to be small".
    """

    _PAIR = {"xg_home": 2.3, "xg_away": 0.9}

    def test_every_provenance_token_yields_identical_numbers(self):
        baseline = _predict(self._PAIR)
        for token in (XG_SOURCE_GOALS_PROXY, "static_table", "live_provider",
                      "some_future_provider"):
            with self.subTest(token=token):
                got = _predict({**self._PAIR, "xg_source": token})
                self.assertEqual(got.confidence, baseline.confidence)
                self.assertEqual(
                    got.outcome_probabilities, baseline.outcome_probabilities,
                )
                self.assertEqual(
                    got.betting_analysis["confidence_breakdown"],
                    baseline.betting_analysis["confidence_breakdown"],
                )

    def test_the_weight_and_availability_are_untouched_by_provenance(self):
        baseline = _xg_item(_predict(self._PAIR))
        assert baseline is not None
        for token in (XG_SOURCE_GOALS_PROXY, "static_table", "live_provider"):
            with self.subTest(token=token):
                item = _xg_item(_predict({**self._PAIR, "xg_source": token}))
                assert item is not None
                self.assertEqual(item.weight, baseline.weight)
                self.assertTrue(item.available)
                self.assertEqual(item.direction, "support")
                self.assertEqual(item.predicted_outcome, baseline.predicted_outcome)

    def test_a_goals_proxy_still_votes_rather_than_being_dropped(self):
        """The opposite guard: this fix must not quietly delete real evidence.

        Goals per game is a real measurement of a real thing, so unlike an invented
        Elo rating it is kept.  Without this the fix could "pass" by dropping the
        pair, which would take the factor away from 99.0% of live club fixtures.
        """
        proxy = _predict({**self._PAIR, "xg_source": XG_SOURCE_GOALS_PROXY})
        absent = _predict({})
        self.assertNotEqual(
            proxy.confidence, absent.confidence,
            "a labelled proxy must still be counted, not dropped",
        )
        proxy_item, absent_item = _xg_item(proxy), _xg_item(absent)
        assert proxy_item is not None and absent_item is not None
        self.assertTrue(proxy_item.available)
        self.assertFalse(absent_item.available)
        breakdown = proxy.betting_analysis["confidence_breakdown"]
        absent_breakdown = absent.betting_analysis["confidence_breakdown"]
        self.assertGreater(
            breakdown["data_completeness"], absent_breakdown["data_completeness"],
        )

    def test_a_measured_feed_is_not_privileged_over_a_proxy_in_the_math(self):
        """Stated so a future "damp the proxy" change cannot land silently.

        Damping would be a formula change, and this increment deliberately makes
        none: the two differ only in what the explanation says.  HockeyEngine does
        damp its weaker sources, so if that policy is ever wanted here it must
        arrive as its own change with its own measurement.
        """
        proxy = _predict({**self._PAIR, "xg_source": XG_SOURCE_GOALS_PROXY})
        measured = _predict({**self._PAIR, "xg_source": "live_provider"})
        self.assertEqual(proxy.confidence, measured.confidence)
        self.assertEqual(proxy.outcome_probabilities, measured.outcome_probabilities)


class EveryWriteSiteLabelsItselfTests(unittest.TestCase):
    """A structural partition over the write sites.

    The defect was one branch of three writing the value and not the token. This
    scans the writer for assignments to ``xg_home``/``xg_away`` and requires each
    enclosing block to also assign ``xg_source``, so a fourth branch cannot repeat
    it.  Paired with the behavioural class below, because a shape assertion cannot
    see a *wrong* token (see ``test_elo_provenance.py`` and the shape-9 lesson).
    """

    @staticmethod
    def _writer_tree() -> tuple[ast.Module, str]:
        path = _repo_root() / _XG_WRITER
        src = path.read_text(encoding="utf-8-sig")
        return ast.parse(src), src

    def test_the_writer_file_exists_and_parses(self):
        # Guards the denominator: a scan over a file that failed to parse would
        # find zero write sites and pass forever.
        tree, src = self._writer_tree()
        self.assertIsInstance(tree, ast.Module)
        self.assertIn("xg_home", src)

    def test_every_xg_value_assignment_is_accompanied_by_a_source_assignment(self):
        tree, _ = self._writer_tree()
        target = next(
            (
                n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "enrich_situational_features"
            ),
            None,
        )
        self.assertIsNotNone(
            target, "enrich_situational_features is the declared write site",
        )
        assert target is not None

        value_writes, source_writes = [], []
        for node in ast.walk(target):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if not isinstance(tgt, ast.Subscript):
                    continue
                key = tgt.slice
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                if key.value in ("xg_home", "xg_away"):
                    value_writes.append(node.lineno)
                elif key.value == "xg_source":
                    source_writes.append(node.lineno)

        self.assertGreaterEqual(
            len(value_writes), 4,
            "expected the goals-proxy pair plus the live and static overwrites",
        )
        self.assertGreaterEqual(
            len(source_writes), 3,
            "each of the three branches must write its own provenance token; "
            f"found value writes at {value_writes} but source writes at "
            f"{source_writes}",
        )
        # Every value write must have a token written within a few lines of it --
        # i.e. in the same branch, not merely somewhere in the function.
        for line in value_writes:
            self.assertTrue(
                any(abs(line - s) <= 4 for s in source_writes),
                f"the xG value written at {_XG_WRITER}:{line} has no provenance "
                f"token near it (tokens at {source_writes})",
            )

    def test_the_reader_consults_the_resolver_rather_than_assuming(self):
        path = _repo_root() / _XG_READER
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "resolve_xg_provenance"
        ]
        self.assertEqual(
            len(calls), 1,
            f"{_XG_READER} must resolve the provenance exactly once",
        )
        # Pin the argument. A perfectly-shaped call on the wrong object silently
        # reports "unknown" for every match and satisfies every assertion above.
        self.assertEqual(
            [ast.unparse(a) for a in calls[0].args],
            ["custom"],
            "resolve_xg_provenance must be called on the engine's own custom dict",
        )


class TheRealEnricherLabelsTheProxyTests(unittest.TestCase):
    """The behavioural companion: run ``enrich_situational_features`` for real.

    The structural class above proves a token is written *somewhere near* each
    value.  Only this one proves the token is the **right** one for the branch that
    actually ran.
    """

    _HIST = {
        "wins": 5, "draws": 2, "losses": 3, "played": 10,
        "goals_per_game": 1.35, "last_match_date": "2025-09-01",
    }

    @staticmethod
    def _raw() -> dict:
        return {
            "team": {}, "general": {}, "market": {},
            "player": {}, "environment": {}, "custom": {},
        }

    def _enrich(self, match: MatchIdentity, hist: dict | None) -> dict:
        from app.sports.football.adapters._shared import (
            enrich_situational_features,
        )

        raw = self._raw()
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=hist,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)
        return raw

    def test_two_clubs_absent_from_the_static_table_get_the_proxy_token(self):
        # "Arsenal FC" is the name Football-Data.org actually supplies and is the
        # reason EPL scored 0/760: the table holds "arsenal".
        raw = self._enrich(_match(), self._HIST)
        self.assertEqual(raw["custom"]["xg_home"], 1.35)
        self.assertEqual(raw["custom"]["xg_away"], 1.35)
        self.assertEqual(
            raw["custom"]["xg_source"], XG_SOURCE_GOALS_PROXY,
            "the goals-per-game fallback must name itself",
        )

    def test_a_both_sides_static_hit_still_wins_and_relabels(self):
        ucl = CompetitionIdentity(code="ucl", name="UCL", sport=_FOOTBALL)
        match = MatchIdentity(
            match_id="ucl-xg-static",
            season=SeasonIdentity(competition=ucl, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid", competition=ucl),
            away=TeamIdentity(code="BAR", name="Barcelona", competition=ucl),
            kickoff_utc=datetime.datetime(
                2025, 9, 16, 20, 0, tzinfo=datetime.timezone.utc,
            ),
        )
        raw = self._enrich(match, self._HIST)
        self.assertEqual(
            raw["custom"]["xg_source"], "static_table",
            "the static overwrite must replace the proxy token, not sit beside it",
        )
        self.assertNotEqual(
            raw["custom"]["xg_home"], 1.35,
            "a static hit overwrites the goals proxy value too",
        )

    def test_no_stats_writes_neither_a_value_nor_a_token(self):
        raw = self._enrich(_match(), None)
        self.assertNotIn("xg_home", raw["custom"])
        self.assertNotIn("xg_away", raw["custom"])
        self.assertNotIn(
            "xg_source", raw["custom"],
            "a token with no value would claim a provenance for nothing",
        )

    def test_the_enriched_pair_survives_into_the_engine_explanation(self):
        """End to end: adapter -> feature builder -> engine -> explanation."""
        raw = self._enrich(_match(), self._HIST)
        full = copy.deepcopy(_BASE_RAW)
        full["custom"] = raw["custom"]
        features = FootballFeatureBuilder().build(_match(), full)
        self.assertEqual(features.custom["xg_source"], XG_SOURCE_GOALS_PROXY)
        item = _xg_item(FootballMultiFactorEngine().predict(features, _match()))
        assert item is not None
        self.assertIn("src=goals_proxy", item.detail)
        self.assertIn("not measured xG", item.detail)


if __name__ == "__main__":
    unittest.main()
