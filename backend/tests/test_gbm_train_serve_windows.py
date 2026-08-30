"""The GBM serving path must build features the way training built them.

``world_cup_gbm_features.derive_gbm_features`` exists to prevent train/serve
skew, but it only ever guaranteed the *order* of the vector: both callers built
``home_stats`` / ``away_stats`` / ``h2h`` themselves. Training used a 10-match
form window, a 10-meeting h2h window, and a cutoff at the fixture's own date.
Serving called the producers with no arguments, which meant a **20**-meeting h2h
window (that function's default, kept for its other consumers) over the most
recent rows in the CSV rather than the rows before the fixture.

So these tests pin three separate things:

1. :func:`resolve_windows` reads the windows the artifact records, and falls
   back loudly rather than silently on a missing or unusable declaration.
2. The engine passes the *resolved* windows and the caller's ``before_date`` to
   all three historical producers.
3. Both call sites forward the kickoff they already hold.

and then one behavioural test that ties them together: the vector served for a
finished fixture equals the vector training would have built for it, which is
the only assertion that can tell the fix from a coincidence.
"""

import ast
import inspect
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.kernel.domain import (
    CompetitionIdentity,
    EnvironmentFeatures,
    FeatureSet,
    GeneralFeatures,
    MarketFeatures,
    MatchIdentity,
    PlayerFeatures,
    SeasonIdentity,
    SportIdentity,
    TeamFeatures,
    TeamIdentity,
)
from app.services import world_cup_historical_results as historical
from app.services.world_cup_engines import world_cup_gbm_engine as engine_module
from app.services.world_cup_engines.world_cup_gbm_features import (
    FEATURE_NAMES,
    H2H_WINDOW,
    RECENT_WINDOW,
    derive_gbm_features,
    resolve_windows,
)


def _identity(*, kickoff: datetime) -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="wc", name="World Cup", sport=sport)
    return MatchIdentity(
        match_id="gbm-window-probe",
        season=SeasonIdentity(competition=comp, season_key="2026"),
        stage="group",
        round=None,
        home=TeamIdentity(code="POR", name="Portugal", competition=comp),
        away=TeamIdentity(code="ESP", name="Spain", competition=comp),
        kickoff_utc=kickoff,
    )


def _features() -> FeatureSet:
    """A minimal FeatureSet carrying the Elo pair the adapter requires."""
    match = _identity(kickoff=datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc))
    return FeatureSet(
        match,
        GeneralFeatures(5, 5, None, None),
        TeamFeatures(2000.0, 1980.0, 0.6, 0.5, 0.4, 0.3, None, None, "csv/csv"),
        MarketFeatures(None, None, None, None, False),
        PlayerFeatures(None, None, None, None),
        EnvironmentFeatures(None, None, None, True),
        {},
        "real",
        [],
        "1",
    )


class ResolveWindowsTests(unittest.TestCase):
    """The artifact's declaration wins; anything unusable falls back loudly."""

    def test_the_artifact_windows_win_over_the_module_constants(self) -> None:
        # Deliberately different from both constants *and* from
        # get_historical_h2h's own default of 20, so a pass cannot come from
        # any of the three values already in the code.
        meta = {"training_config": {"recent_window": 7, "h2h_window": 13}}
        self.assertEqual(resolve_windows(meta), (7, 13))

    def test_a_missing_artifact_falls_back_to_the_declared_constants(self) -> None:
        for meta in (None, {}, {"training_config": None}, {"training_config": "10"}):
            with self.subTest(meta=meta):
                self.assertEqual(resolve_windows(meta), (RECENT_WINDOW, H2H_WINDOW))

    def test_a_partial_artifact_falls_back_only_for_the_missing_window(self) -> None:
        resolved = resolve_windows({"training_config": {"recent_window": 6}})
        self.assertEqual(resolved, (6, H2H_WINDOW))
        resolved = resolve_windows({"training_config": {"h2h_window": 6}})
        self.assertEqual(resolved, (RECENT_WINDOW, 6))

    def test_a_numeric_string_is_accepted(self) -> None:
        meta = {"training_config": {"recent_window": "7", "h2h_window": 13.0}}
        self.assertEqual(resolve_windows(meta), (7, 13))

    def test_an_unusable_window_falls_back_and_warns(self) -> None:
        cases = (
            ("non-numeric", "ten", "non-numeric"),
            ("zero", 0, "cannot select any match"),
            ("negative", -5, "cannot select any match"),
        )
        for label, value, expected_log in cases:
            with self.subTest(case=label):
                meta = {"training_config": {"recent_window": value, "h2h_window": value}}
                with self.assertLogs(
                    "app.services.world_cup_engines.world_cup_gbm_features",
                    level="WARNING",
                ) as captured:
                    resolved = resolve_windows(meta)
                self.assertEqual(resolved, (RECENT_WINDOW, H2H_WINDOW))
                # Both windows are unusable, so both must be reported: a single
                # warning would mean one of them fell back in silence.
                self.assertEqual(len(captured.records), 2)
                joined = " ".join(captured.output)
                self.assertIn(expected_log, joined)
                self.assertIn("recent_window", joined)
                self.assertIn("h2h_window", joined)

    def test_the_shipped_artifact_declares_both_windows(self) -> None:
        """The fallback must stay a fallback, not the path production takes."""
        import json

        meta_path = (
            Path(engine_module.__file__).resolve().parents[3]
            / "data"
            / "gbm_features.json"
        )
        if not meta_path.exists():  # pragma: no cover - artifact ships with the repo
            self.skipTest("gbm_features.json is not present")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        config = meta.get("training_config")
        self.assertIsInstance(config, dict)
        self.assertIn("recent_window", config)
        self.assertIn("h2h_window", config)
        # If the shipped model was fitted on other windows, the constants are
        # what is stale -- and resolve_windows already prefers the artifact.
        self.assertEqual(
            resolve_windows(meta),
            (config["recent_window"], config["h2h_window"]),
        )


class GbmEngineProducerArgumentTests(unittest.TestCase):
    """The engine must hand the resolved windows and the cutoff to the CSV."""

    def _capture(self, *, meta, before_date):
        stats_calls: list[dict] = []
        h2h_calls: list[dict] = []

        def fake_stats(team, *, before_date=None, max_matches=10):
            stats_calls.append(
                {"team": team, "before_date": before_date, "max_matches": max_matches}
            )
            return None

        def fake_h2h(home, away, *, before_date=None, max_matches=20):
            h2h_calls.append(
                {
                    "home": home,
                    "away": away,
                    "before_date": before_date,
                    "max_matches": max_matches,
                }
            )
            return None

        with patch.object(engine_module, "_load_models", return_value=(None, None, meta)), \
                patch.object(engine_module, "get_historical_team_stats", fake_stats), \
                patch.object(engine_module, "get_historical_h2h", fake_h2h):
            engine_module.predict_match_gbm(
                "Brazil", "Argentina", 2100.0, 2050.0, before_date=before_date,
            )
        return stats_calls, h2h_calls

    def test_both_producers_receive_the_windows_the_artifact_declares(self) -> None:
        meta = {"training_config": {"recent_window": 7, "h2h_window": 13}}
        stats_calls, h2h_calls = self._capture(meta=meta, before_date="2026-07-01")

        self.assertEqual(len(stats_calls), 2)
        self.assertEqual([call["max_matches"] for call in stats_calls], [7, 7])
        self.assertEqual(len(h2h_calls), 1)
        self.assertEqual(h2h_calls[0]["max_matches"], 13)

    def test_the_h2h_window_is_not_that_functions_own_default(self) -> None:
        """The original defect in one assertion: serving used 20, training 10."""
        _, h2h_calls = self._capture(meta=None, before_date="2026-07-01")
        default_window = inspect.signature(
            historical.get_historical_h2h
        ).parameters["max_matches"].default
        self.assertEqual(default_window, 20, "the sibling consumers' default moved")
        self.assertEqual(h2h_calls[0]["max_matches"], H2H_WINDOW)
        self.assertNotEqual(h2h_calls[0]["max_matches"], default_window)

    def test_the_cutoff_reaches_all_three_producers(self) -> None:
        kickoff = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        stats_calls, h2h_calls = self._capture(meta=None, before_date=kickoff)
        for call in stats_calls:
            self.assertEqual(call["before_date"], kickoff)
        self.assertEqual(h2h_calls[0]["before_date"], kickoff)

    def test_omitting_the_cutoff_still_reaches_the_producers_as_none(self) -> None:
        """An unplayed fixture legitimately has no cutoff; that must be explicit."""
        stats_calls, h2h_calls = self._capture(meta=None, before_date=None)
        for call in stats_calls:
            self.assertIsNone(call["before_date"])
        self.assertIsNone(h2h_calls[0]["before_date"])


class GbmCallSiteForwardingTests(unittest.TestCase):
    """Every caller that holds a kickoff must forward it.

    Both are pinned structurally *and* by argument value, because a call that
    passes ``before_date`` is worthless if it passes the wrong thing.
    """

    def test_the_kernel_adapter_forwards_the_identity_kickoff(self) -> None:
        from app.kernel.engines.gbm_engine import GbmEngine

        captured: dict = {}

        def fake_predict(*args, **kwargs):
            captured.update(kwargs)
            return {
                "outcome_probabilities": {"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
                "predicted_score": {"home": 1.6, "away": 1.1},
                "confidence": 0.6,
                "model_loaded": True,
                "prediction_method": "gbm_lightgbm",
            }

        kickoff = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        match = _identity(kickoff=kickoff)
        # The adapter imports predict_match_gbm inside predict(), so the name it
        # binds is the one in the legacy module, not one in its own namespace.
        with patch.object(engine_module, "predict_match_gbm", fake_predict):
            GbmEngine().predict(_features(), match)

        self.assertEqual(captured.get("before_date"), kickoff)

    def test_the_legacy_pipeline_branch_forwards_the_match_kickoff(self) -> None:
        """A wiring assertion: the query-param route has no feature flag.

        ``GBM_ENGINE_ENABLED`` gates the kernel adapter only -- the legacy
        pipeline dispatches on the route's ``engine`` parameter, so this branch
        is reachable in production and cannot be left to drift.
        """
        from app.services import world_cup_prediction_pipeline as pipeline

        source = Path(pipeline.__file__).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)

        forwarded: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # get_engine("gbm")(...)
            if not (isinstance(func, ast.Call) and isinstance(func.func, ast.Name)):
                continue
            if func.func.id != "get_engine":
                continue
            if not (
                len(func.args) == 1
                and isinstance(func.args[0], ast.Constant)
                and func.args[0].value == "gbm"
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg == "before_date":
                    forwarded.append(ast.unparse(keyword.value))

        self.assertEqual(
            forwarded,
            ["match.kickoff_utc"],
            "the GBM branch of the pipeline must forward the fixture's kickoff",
        )


class GbmServedVectorMatchesTrainingTests(unittest.TestCase):
    """The vector served for a finished fixture equals the trained-window one.

    Built as an equality check against a vector derived from the pre-fixture
    rows only. A "both are small" style assertion could not distinguish the
    defect from the fix: the skew this replaces was 0.088 xG.
    """

    HOME = "Portugal"
    AWAY = "Spain"
    KICKOFF = date(2026, 7, 1)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results_path = Path(self.tmp.name) / "results.csv"

        rows = [
            "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral",
        ]
        # 12 prior meetings: more than the 10-meeting training window and fewer
        # than get_historical_h2h's 20, so the two windows select different sets
        # and the vector can tell them apart.
        for index in range(12):
            month = 1 + index
            year = 2024 + month // 13
            rows.append(
                f"{year:04d}-{(month % 12) + 1:02d}-05,{self.HOME},{self.AWAY},"
                f"{1 if index % 2 else 3},{0 if index % 3 else 2},Friendly,Lisbon,Portugal,FALSE"
            )
        # The fixture itself, plus a meeting after it. Neither may enter the
        # vector used to predict the fixture.
        rows.append(
            f"{self.KICKOFF.isoformat()},{self.HOME},{self.AWAY},5,0,"
            "FIFA World Cup,Dallas,USA,TRUE"
        )
        rows.append(
            f"2026-08-01,{self.AWAY},{self.HOME},4,0,Friendly,Madrid,Spain,FALSE"
        )
        self.results_path.write_text("\n".join(rows), encoding="utf-8")

        historical._load_results.cache_clear()
        self.addCleanup(historical._load_results.cache_clear)
        self.env = patch.dict(
            os.environ,
            {"WORLD_CUP_HISTORICAL_RESULTS_FILE": str(self.results_path)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _served_vector(self, meta) -> list[float]:
        """The vector the engine actually derived, captured at the seam.

        Two stub models stand in for the LightGBM artifacts so the engine takes
        its model branch and *does* call ``derive_gbm_features``; if it fell
        through to the Elo fallback instead there would be no vector to compare
        and the assertion below would fail rather than silently pass.
        """
        captured: list[dict] = []
        real_derive = engine_module.derive_gbm_features

        def spy(**kwargs):
            captured.append(dict(kwargs))
            return real_derive(**kwargs)

        class _StubModel:
            def predict(self, rows):
                return [1.5]

        with patch.object(
            engine_module, "_load_models",
            return_value=(_StubModel(), _StubModel(), meta),
        ), patch.object(engine_module, "derive_gbm_features", spy):
            engine_module.predict_match_gbm(
                self.HOME,
                self.AWAY,
                2000.0,
                1980.0,
                is_neutral=True,
                is_world_cup=True,
                before_date=self.KICKOFF,
            )

        self.assertEqual(
            len(captured), 1,
            "the engine did not derive exactly one feature vector",
        )
        return real_derive(**captured[0])

    def _trained_vector(self, *, recent_window: int, h2h_window: int) -> list[float]:
        """What training would have built: last N rows strictly before kickoff."""
        return derive_gbm_features(
            elo_home=2000.0,
            elo_away=1980.0,
            home_stats=historical.get_historical_team_stats(
                self.HOME, before_date=self.KICKOFF, max_matches=recent_window,
            ),
            away_stats=historical.get_historical_team_stats(
                self.AWAY, before_date=self.KICKOFF, max_matches=recent_window,
            ),
            h2h=historical.get_historical_h2h(
                self.HOME, self.AWAY, before_date=self.KICKOFF, max_matches=h2h_window,
            ),
            is_neutral=True,
            is_world_cup=True,
        )

    def test_the_served_vector_equals_the_trained_window_vector(self) -> None:
        served = self._served_vector({"training_config": {"recent_window": 10, "h2h_window": 10}})
        trained = self._trained_vector(recent_window=10, h2h_window=10)
        self.assertEqual(len(served), len(FEATURE_NAMES))
        for name, got, want in zip(FEATURE_NAMES, served, trained):
            with self.subTest(feature=name):
                self.assertAlmostEqual(got, want, places=9)

    def test_the_trained_and_default_windows_are_distinguishable(self) -> None:
        """Guards the test above: 10 and 20 must disagree on this fixture.

        Without this, an engine that still used 20 could pass by coincidence.
        """
        ten = self._trained_vector(recent_window=10, h2h_window=10)
        twenty = self._trained_vector(recent_window=10, h2h_window=20)
        self.assertNotEqual(ten, twenty)

    def test_the_vector_excludes_the_fixtures_own_result(self) -> None:
        """The 5-0 on kickoff day and the 0-4 after it must not be visible."""
        with_cutoff = self._trained_vector(recent_window=10, h2h_window=10)
        without_cutoff = derive_gbm_features(
            elo_home=2000.0,
            elo_away=1980.0,
            home_stats=historical.get_historical_team_stats(
                self.HOME, max_matches=RECENT_WINDOW,
            ),
            away_stats=historical.get_historical_team_stats(
                self.AWAY, max_matches=RECENT_WINDOW,
            ),
            h2h=historical.get_historical_h2h(
                self.HOME, self.AWAY, max_matches=H2H_WINDOW,
            ),
            is_neutral=True,
            is_world_cup=True,
        )
        goal_diff = FEATURE_NAMES.index("h2h_avg_goal_diff")
        self.assertNotAlmostEqual(
            with_cutoff[goal_diff], without_cutoff[goal_diff], places=6
        )
        served = self._served_vector({"training_config": {"recent_window": 10, "h2h_window": 10}})
        self.assertAlmostEqual(served[goal_diff], with_cutoff[goal_diff], places=9)


class GbmWindowDeclarationTests(unittest.TestCase):
    """No script may re-declare a window the feature module owns."""

    def test_the_scripts_import_the_windows_rather_than_copying_them(self) -> None:
        scripts_dir = Path(engine_module.__file__).resolve().parents[3] / "scripts"
        for name in ("train_gbm_model.py", "backtest_gbm.py"):
            path = scripts_dir / name
            with self.subTest(script=name):
                self.assertTrue(path.exists(), f"{name} is missing")
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                assigned = {
                    target.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
                self.assertNotIn("RECENT_WINDOW", assigned)
                self.assertNotIn("H2H_WINDOW", assigned)
                imported = {
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.endswith("world_cup_gbm_features")
                    for alias in node.names
                }
                self.assertIn("RECENT_WINDOW", imported)
                self.assertIn("H2H_WINDOW", imported)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
