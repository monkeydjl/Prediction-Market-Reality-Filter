# backend/tests/test_unknown_match_is_refused.py
"""A match id with no fixture must not yield a persisted prediction.

Every adapter's ``get_match_identity`` is declared ``-> MatchIdentity`` and
substitutes a placeholder when the fixture is missing, so ``is None`` cannot
detect an unknown match. Measured against a copy of the live kernel DB before
this guard existed: ``POST /predictions/matches/<unknown>/predict`` answered 200
with confidence 0.5475, an Elo factor reported ``available: true`` on the
neutral 1500 the Elo service substitutes for an unknown team, and one row landed
in each of ``kernel_predictions`` and ``kernel_prediction_history``.
``GET /predictions/matches/<unknown>`` answered 200 describing "Home vs Away".

Three shapes are pinned here, because closing only one leaves the defect
reachable by another route:

1. every adapter that builds a placeholder marks it ``is_stub=True`` -- asserted
   as an exact partition against an AST scan of ``app/sports/``, so a ninth
   adapter added later fails this file rather than silently reintroducing the
   hole (the hand-maintained-list failure this project has hit nine times);
2. ``PredictionKernel.predict`` raises and writes *nothing* -- asserted by
   counting calls on the learning service, not by inspecting a store, since a
   store assertion passes for free when the write was skipped for the wrong
   reason;
3. a real fixture still predicts, so the guard cannot be satisfied by refusing
   everything.
"""
from __future__ import annotations

import ast
import functools
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app.kernel.domain import (
    CompetitionIdentity,
    MatchIdentity,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
    UnknownMatchError,
)
from app.kernel.prediction_kernel import PredictionKernel

_SPORTS_DIR = Path(__file__).resolve().parents[1] / "app" / "sports"

# Declared as data: the adapters known to substitute a placeholder identity.
# The scan below asserts this set exactly, in both directions.
_EXPECTED_STUB_BUILDERS: frozenset[str] = frozenset({
    "app/sports/baseball/mlb_adapter.py",
    "app/sports/basketball/nba_adapter.py",
    "app/sports/football/adapters/epl_adapter.py",
    "app/sports/football/adapters/league_adapter.py",
    "app/sports/football/adapters/ucl_adapter.py",
    "app/sports/football/adapters/world_cup_adapter.py",
    "app/sports/hockey/nhl_adapter.py",
    "app/sports/lol/lol_adapter.py",
})


@functools.lru_cache(maxsize=1)
def _scan_stub_builders() -> tuple[dict[str, int], dict[str, int]]:
    """Return (placeholder identities per file, flagged ones per file).

    A placeholder is a ``MatchIdentity(...)`` call that either sits in a function
    named ``_stub_identity``, or whose ``home=`` argument resolves to
    ``TeamIdentity(code="HOME", ...)`` -- the bare literal, not a code computed
    from a fixture row.

    ``home=`` is resolved to its *nearest preceding binding in the same
    function*, which took two corrections to get right. A file-wide
    ``ast.walk`` is scope-blind and reported the real-fixture construction in
    five adapters as an unflagged placeholder, because both branches bind a
    variable named ``home``. Scope-awareness alone still failed on
    ``world_cup_adapter``, which builds both branches in one function and
    rebinds ``home`` (placeholder at line 91 used at 97, real at 108 used at
    118), so *any* binding in scope matched both. The real branch computes its
    code from the fixture (``(fixture.home_team or "HOME")[:3].upper()``), which
    is the discriminator.

    A file that will not parse raises rather than being skipped -- a census that
    drops rows on error is how this project has previously lost the very rows it
    existed to count.
    """
    built: dict[str, int] = {}
    flagged: dict[str, int] = {}

    for path in sorted(_SPORTS_DIR.rglob("*.py")):
        rel = path.relative_to(_SPORTS_DIR.parents[1]).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))

        # Innermost enclosing function for every node, so a MatchIdentity call
        # is resolved against the bindings of its own scope.
        enclosing: dict[ast.AST, ast.AST | None] = {}
        for parent in ast.walk(tree):
            is_fn = isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
            for child in ast.iter_child_nodes(parent):
                enclosing[child] = parent if is_fn else enclosing.get(parent)

        def _scope_of(node: ast.AST) -> ast.AST | None:
            cur: ast.AST | None = enclosing.get(node)
            while cur is not None and not isinstance(
                cur, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                cur = enclosing.get(cur)
            return cur

        # (scope, name) -> [(lineno, binds_a_literal_HOME), ...]
        bindings: dict[tuple[int, str], list[tuple[int, bool]]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if not (isinstance(call.func, ast.Name) and call.func.id == "TeamIdentity"):
                continue
            literal_home = any(
                kw.arg == "code"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "HOME"
                for kw in call.keywords
            )
            scope = _scope_of(node)
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    key = (id(scope), tgt.id)
                    bindings.setdefault(key, []).append((node.lineno, literal_home))

        def _nearest_binding_is_placeholder(
            scope: ast.AST | None, name: str, before: int
        ) -> bool:
            """Resolve ``name`` to the last binding above line ``before``."""
            candidates = [
                (lineno, lit)
                for lineno, lit in bindings.get((id(scope), name), [])
                if lineno < before
            ]
            if not candidates:
                return False
            return max(candidates, key=lambda pair: pair[0])[1]

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "MatchIdentity"):
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            scope = _scope_of(node)
            in_stub_fn = (
                isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                and scope.name == "_stub_identity"
            )
            home = kwargs.get("home")
            is_placeholder = in_stub_fn or (
                isinstance(home, ast.Name)
                and _nearest_binding_is_placeholder(scope, home.id, node.lineno)
            )
            if not is_placeholder:
                continue
            built[rel] = built.get(rel, 0) + 1
            flag = kwargs.get("is_stub")
            if isinstance(flag, ast.Constant) and flag.value is True:
                flagged[rel] = flagged.get(rel, 0) + 1

    return built, flagged


def _real_identity(match_id: str = "real-1") -> MatchIdentity:
    """An identity of the shape a real fixture row produces (``is_stub`` False)."""
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="epl", name="Premier League", sport=sport)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=comp, season_key="2025"),
        stage="regular_season",
        round=None,
        home=TeamIdentity(code="ARS", name="Arsenal FC", competition=comp),
        away=TeamIdentity(code="CHE", name="Chelsea FC", competition=comp),
        kickoff_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )


def _kernel_for(identity: MatchIdentity) -> tuple[PredictionKernel, MagicMock]:
    """A kernel whose adapter returns ``identity``; returns (kernel, learning)."""
    adapter = MagicMock()
    adapter.get_match_identity.return_value = identity
    adapter.fetch_all_data.return_value = {
        "team": {}, "market": {}, "player": {}, "environment": {}, "general": {},
    }
    learning = MagicMock()
    engine_registry = MagicMock()
    engine_registry.select.return_value.predict.return_value = MagicMock(
        engine_name="elo_odds",
    )
    kernel = PredictionKernel(
        adapter=adapter,
        feature_builder=MagicMock(),
        engine_registry=engine_registry,
        factor_registry=MagicMock(),
        feature_registry=MagicMock(),
        learning=learning,
    )
    return kernel, learning


class DetectorTests(unittest.TestCase):
    """The scan is a measuring instrument, so it is measured.

    Includes the false positive the first version of this file actually had: a
    scope-blind scan counted a real-fixture construction as an unflagged
    placeholder in five of the eight adapters, because both branches bind a
    variable named ``home``.
    """

    _REAL_AND_STUB_IN_ONE_FILE = '''
def _stub_identity(match_id):
    home = TeamIdentity(code="HOME", name="Home", competition=C)
    away = TeamIdentity(code="AWAY", name="Away", competition=C)
    return MatchIdentity(match_id=match_id, home=home, away=away, is_stub=True)

def get_match_identity(self, match_id):
    fixture = query(match_id)
    if fixture is None:
        return self._stub_identity(match_id)
    home = TeamIdentity(code=(fixture.home or "HOME")[:3], name=fixture.home, competition=C)
    away = TeamIdentity(code=(fixture.away or "AWAY")[:3], name=fixture.away, competition=C)
    return MatchIdentity(match_id=match_id, home=home, away=away)
'''

    def _scan_source(self, source: str, tmpname: str = "probe_adapter.py"):
        """Run the real scan over one synthetic file."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "sports" / tmpname
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            global _SPORTS_DIR
            original = _SPORTS_DIR
            _SPORTS_DIR = target.parent
            try:
                _scan_stub_builders.cache_clear()
                return _scan_stub_builders()
            finally:
                _SPORTS_DIR = original
                _scan_stub_builders.cache_clear()

    def test_a_real_fixture_branch_is_not_counted_as_a_placeholder(self):
        built, flagged = self._scan_source(self._REAL_AND_STUB_IN_ONE_FILE)
        self.assertEqual(sum(built.values()), 1, f"built={built}")
        self.assertEqual(sum(flagged.values()), 1, f"flagged={flagged}")

    def test_an_unflagged_placeholder_is_detected(self):
        source = self._REAL_AND_STUB_IN_ONE_FILE.replace(", is_stub=True", "")
        built, flagged = self._scan_source(source)
        self.assertEqual(sum(built.values()), 1, f"built={built}")
        self.assertEqual(sum(flagged.values()), 0, f"flagged={flagged}")

    def test_a_placeholder_outside_a_stub_named_function_is_still_detected(self):
        """world_cup_adapter builds its placeholder inline, not in _stub_identity."""
        source = '''
def get_match_identity(self, match_id):
    if fixture is None:
        home = TeamIdentity(code="HOME", name="Home", competition=C)
        away = TeamIdentity(code="AWAY", name="Away", competition=C)
        return MatchIdentity(match_id=match_id, home=home, away=away)
    return MatchIdentity(match_id=match_id, home=real_home, away=real_away)
'''
        built, flagged = self._scan_source(source)
        self.assertEqual(sum(built.values()), 1, f"built={built}")
        self.assertEqual(sum(flagged.values()), 0, f"flagged={flagged}")

    def test_both_branches_in_one_function_rebinding_home_are_separated(self):
        """The exact shape of world_cup_adapter, and the second false positive.

        One function, ``home`` bound twice: literal placeholder first, fixture
        second. A scan that accepts *any* binding in scope counts both
        constructions as placeholders and demands ``is_stub=True`` on the real
        one. Only the first is a placeholder.
        """
        source = '''
def get_match_identity(self, match_id):
    if fixture is None:
        home = TeamIdentity(code="HOME", name="Home", competition=C)
        away = TeamIdentity(code="AWAY", name="Away", competition=C)
        return MatchIdentity(match_id=match_id, home=home, away=away, is_stub=True)
    home = TeamIdentity(code=fixture.home[:3], name=fixture.home, competition=C)
    away = TeamIdentity(code=fixture.away[:3], name=fixture.away, competition=C)
    return MatchIdentity(match_id=match_id, home=home, away=away)
'''
        built, flagged = self._scan_source(source)
        self.assertEqual(sum(built.values()), 1, f"built={built}")
        self.assertEqual(sum(flagged.values()), 1, f"flagged={flagged}")

    def test_an_unparseable_file_raises_rather_than_being_skipped(self):
        with self.assertRaises(SyntaxError):
            self._scan_source("def broken(:\n    pass\n")


class StubIdentityIsFlaggedEverywhereTests(unittest.TestCase):
    """The partition, both directions, against a scan of app/sports/."""

    def test_the_scan_actually_found_the_adapters(self):
        built, _ = _scan_stub_builders()
        self.assertGreaterEqual(
            len(built), 8,
            "the scan found fewer placeholder builders than the 8 measured; a "
            "census that silently finds nothing passes every test below",
        )

    def test_every_placeholder_builder_is_declared(self):
        built, _ = _scan_stub_builders()
        undeclared = sorted(set(built) - _EXPECTED_STUB_BUILDERS)
        self.assertEqual(
            undeclared, [],
            "these files build a placeholder MatchIdentity but are not in "
            f"_EXPECTED_STUB_BUILDERS: {undeclared}. Add is_stub=True and list "
            "the file, or the unknown-match guard does not cover this sport.",
        )

    def test_no_declared_builder_has_disappeared(self):
        built, _ = _scan_stub_builders()
        missing = sorted(_EXPECTED_STUB_BUILDERS - set(built))
        self.assertEqual(
            missing, [],
            f"declared placeholder builders no longer found: {missing}. If an "
            "adapter stopped substituting a placeholder, drop it from the set.",
        )

    def test_every_placeholder_is_flagged(self):
        built, flagged = _scan_stub_builders()
        for rel, n in sorted(built.items()):
            with self.subTest(file=rel):
                self.assertEqual(
                    flagged.get(rel, 0), n,
                    f"{rel} builds {n} placeholder identities but only "
                    f"{flagged.get(rel, 0)} pass is_stub=True; an unflagged "
                    "placeholder is indistinguishable from a real fixture and "
                    "predict() will invent a prediction for it",
                )


class PredictRefusesAnUnknownMatchTests(unittest.TestCase):
    """The kernel guard, and that it writes nothing."""

    def test_predict_raises_for_a_stub_identity(self):
        kernel, _ = _kernel_for(
            MatchIdentity(**{**_real_identity().__dict__, "is_stub": True})
        )
        with self.assertRaises(UnknownMatchError) as ctx:
            kernel.predict("no-such-match")
        self.assertEqual(ctx.exception.match_id, "no-such-match")

    def test_predict_persists_nothing_for_a_stub_identity(self):
        """Counted on the learning service, not on a store.

        ``record_prediction`` is the only write ``predict`` performs, so a
        not-called assertion here is the write assertion. Checking a store's row
        count instead would also pass if the write were skipped for an unrelated
        reason -- and the raise must happen *before* the engine runs, which the
        second assertion pins.
        """
        identity = MatchIdentity(**{**_real_identity().__dict__, "is_stub": True})
        kernel, learning = _kernel_for(identity)
        with self.assertRaises(UnknownMatchError):
            kernel.predict("no-such-match")
        learning.record_prediction.assert_not_called()
        kernel._adapter.fetch_all_data.assert_not_called()

    def test_a_real_identity_still_predicts_and_records(self):
        """Without this, refusing every id would satisfy the tests above."""
        kernel, learning = _kernel_for(_real_identity())
        result = kernel.predict("real-1")
        self.assertEqual(result.engine_name, "elo_odds")
        learning.record_prediction.assert_called_once()

    def test_batch_predict_drops_the_unknown_and_keeps_the_rest(self):
        """batch_predict catches per match, so one bad id must not abort a run."""
        real = _real_identity("real-1")
        stub = MatchIdentity(**{**_real_identity("bad-1").__dict__, "is_stub": True})
        kernel, learning = _kernel_for(real)
        kernel._adapter.get_match_identity.side_effect = (
            lambda mid: stub if mid == "bad-1" else real
        )
        results = kernel.batch_predict(["bad-1", "real-1"])
        self.assertEqual(len(results), 1)
        self.assertEqual(learning.record_prediction.call_count, 1)


class ProcessOutcomeSkipsLearningForAStubTests(unittest.TestCase):
    """A real outcome is still recorded; only the competition-keyed step stops."""

    def _kernel_with_outcome(self, identity: MatchIdentity):
        kernel, learning = _kernel_for(identity)
        kernel._adapter.fetch_outcome.return_value = MagicMock(match_id="m-1")
        learning.compute_error.return_value = MagicMock(engine="elo_odds")
        return kernel, learning

    def test_outcome_is_recorded_but_learning_is_skipped(self):
        from unittest.mock import patch

        identity = MatchIdentity(**{**_real_identity("m-1").__dict__, "is_stub": True})
        kernel, learning = self._kernel_with_outcome(identity)
        with patch("app.kernel.prediction_kernel.config") as cfg:
            cfg.settings.PHASE3_LEARNING_ENABLED = True
            kernel.process_outcome("m-1")
        learning.record_outcome.assert_called_once()
        learning.update_calibration.assert_not_called()
        learning.update_weights.assert_not_called()
        learning.engine_score.assert_not_called()

    def test_a_real_identity_still_reaches_learning(self):
        from unittest.mock import patch

        kernel, learning = self._kernel_with_outcome(_real_identity("m-1"))
        with patch("app.kernel.prediction_kernel.config") as cfg:
            cfg.settings.PHASE3_LEARNING_ENABLED = True
            kernel.process_outcome("m-1")
        learning.update_calibration.assert_called_once()
        learning.update_weights.assert_called_once()
        learning.engine_score.assert_called_once()


if __name__ == "__main__":
    unittest.main()
