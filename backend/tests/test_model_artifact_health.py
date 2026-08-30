"""Model artifact health census + GBM feature identity (E17).

Five production engines read a coefficient out of one of three fitted JSON
artifacts, and before this increment nothing reported the state of any of them:
the shipped Dixon-Coles fit carries ``optimizer_success: false`` while its ``rho``
moves every served draw probability by 1.3-2.1 points.

The tests are grouped by the failure each one is meant to catch, and several exist
specifically because a weaker version of them would pass against the defect:

* ``MissingArtifactIsNamedTests`` -- a directory listing would omit a never-fitted
  artifact and the report would read as healthy. Asserts the roster is declared.
* ``UnavailableIsNotOkTests`` -- the first draft of ``_feature_identity`` returned
  ``None`` both when the artifact was clean and when the check could not import,
  so an unrunnable check printed ``feature_identity: "ok"``.
* ``TrainerDeclaresFeatureNamesTests`` -- perturbs ``FEATURE_NAMES`` rather than
  asserting a hand-typed copy, because four copies of the list is exactly how the
  away booster ended up shipping ``Column_0..Column_16``.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from app.services import model_artifact_health_service as health
from app.services.world_cup_engines.world_cup_gbm_features import (
    FEATURE_NAMES,
    feature_identity_problem,
)

_TODAY = dt.date(2026, 8, 30)

#: A converged Dixon-Coles artifact, i.e. what the shipped one is not.
_DC_OK = {
    "rho": -0.05,
    "home_advantage": 0.29,
    "mu": 1.12,
    "half_life_days": 730.0,
    "since_year": 2018,
    "min_team_matches": 5,
    "sample_count": 8000,
    "team_count": 250,
    "ref_date": "2026-08-20",
    "fitted_at": "2026-08-25T00:00:00+00:00",
    "optimizer_success": True,
}


def _write(directory: pathlib.Path, name: str, payload: object) -> pathlib.Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class InspectArtifactStatusTests(unittest.TestCase):
    """One status per real artifact state, each pinned to its own value."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_missing_file_is_missing_not_ok(self) -> None:
        entry = health.inspect_artifact(
            "dixon_coles", self.dir / "absent.json", now=_TODAY,
        )
        self.assertEqual(entry["status"], health.STATUS_MISSING)
        self.assertFalse(entry["exists"])
        self.assertIsNotNone(entry["detail"])

    def test_unparseable_file_is_unreadable(self) -> None:
        path = self.dir / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)
        self.assertEqual(entry["status"], health.STATUS_UNREADABLE)
        self.assertTrue(entry["exists"])

    def test_json_list_is_unreadable_not_ok(self) -> None:
        """A valid JSON document of the wrong shape must not read as healthy."""
        path = _write(self.dir, "list.json", [1, 2, 3])
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)
        self.assertEqual(entry["status"], health.STATUS_UNREADABLE)

    def test_optimizer_failure_is_not_converged(self) -> None:
        payload = dict(_DC_OK, optimizer_success=False)
        path = _write(self.dir, "dc.json", payload)
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)
        self.assertEqual(entry["status"], health.STATUS_NOT_CONVERGED)
        self.assertIs(entry["optimizer_success"], False)

    def test_converged_recent_fit_is_ok(self) -> None:
        path = _write(self.dir, "dc.json", _DC_OK)
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)
        self.assertEqual(entry["status"], health.STATUS_OK)
        self.assertIs(entry["optimizer_success"], True)
        self.assertFalse(entry["stale"])
        self.assertIsNone(entry["detail"])

    def test_absent_convergence_flag_is_unknown_not_ok(self) -> None:
        """The GBM trainer records no flag; claiming ``ok`` would invent a check."""
        payload = {k: v for k, v in _DC_OK.items() if k != "optimizer_success"}
        path = _write(self.dir, "dc.json", payload)
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)
        self.assertEqual(entry["status"], health.STATUS_UNKNOWN)
        self.assertIsNone(entry["optimizer_success"])
        self.assertNotEqual(entry["status"], health.STATUS_OK)


class StalenessTests(unittest.TestCase):
    """``stale`` is a measured age, not a guess, and it survives a missing date."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_old_fit_is_stale_and_still_reports_its_age(self) -> None:
        old = _TODAY - dt.timedelta(days=health.STALE_AFTER_DAYS + 10)
        payload = dict(_DC_OK, fitted_at=f"{old.isoformat()}T00:00:00+00:00")
        entry = health.inspect_artifact(
            "dixon_coles", _write(self.dir, "dc.json", payload), now=_TODAY,
        )
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["fitted_age_days"], health.STALE_AFTER_DAYS + 10)

    def test_fresh_fit_is_not_stale(self) -> None:
        recent = _TODAY - dt.timedelta(days=health.STALE_AFTER_DAYS - 10)
        payload = dict(_DC_OK, fitted_at=f"{recent.isoformat()}T00:00:00+00:00")
        entry = health.inspect_artifact(
            "dixon_coles", _write(self.dir, "dc.json", payload), now=_TODAY,
        )
        self.assertFalse(entry["stale"])

    def test_unusable_fitted_at_leaves_staleness_unknown(self) -> None:
        """``None`` rather than ``False``: an unparseable date is not "fresh"."""
        for bad in ("", "not-a-date", "2026-13-45", 12345):
            with self.subTest(fitted_at=bad):
                payload = dict(_DC_OK, fitted_at=bad)
                entry = health.inspect_artifact(
                    "dixon_coles", _write(self.dir, "dc.json", payload), now=_TODAY,
                )
                self.assertIsNone(entry["fitted_age_days"])
                self.assertIsNone(entry["stale"])

    def test_stale_converged_fit_is_reported_as_a_problem(self) -> None:
        old = _TODAY - dt.timedelta(days=health.STALE_AFTER_DAYS + 1)
        payload = dict(_DC_OK, fitted_at=f"{old.isoformat()}T00:00:00+00:00")
        with patch.object(
            health, "artifact_slots",
            return_value={"dixon_coles": _write(self.dir, "dc.json", payload)},
        ):
            report = health.collect_model_artifact_health(now=_TODAY)
        self.assertEqual(report["healthy_models"], 0)
        self.assertEqual([p["model"] for p in report["problems"]], ["dixon_coles"])


class MissingArtifactIsNamedTests(unittest.TestCase):
    """A never-fitted artifact must be *named*, not merely absent.

    Seeding the census from a directory listing would drop it, and a report over
    the two survivors would read as fully healthy -- the shape that let a league
    whose sync died pass as green in #75.
    """

    def test_roster_names_all_three_models(self) -> None:
        self.assertEqual(
            set(health.artifact_slots()), {"dixon_coles", "btd", "gbm"},
        )

    def test_every_declared_slot_appears_in_the_report(self) -> None:
        """Exact partition: the report's keys equal the declared roster's keys."""
        report = health.collect_model_artifact_health(now=_TODAY)
        self.assertEqual(set(report["models"]), set(health.artifact_slots()))
        self.assertEqual(report["total_models"], len(health.artifact_slots()))

    def test_missing_artifact_is_present_with_missing_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = pathlib.Path(tmp)
            slots = {
                "dixon_coles": empty / "dixon_coles_params.json",
                "btd": empty / "btd_params.json",
                "gbm": empty / "gbm_features.json",
            }
            with patch.object(health, "artifact_slots", return_value=slots):
                report = health.collect_model_artifact_health(now=_TODAY)

        self.assertEqual(set(report["models"]), {"dixon_coles", "btd", "gbm"})
        for name in ("dixon_coles", "btd", "gbm"):
            with self.subTest(model=name):
                self.assertEqual(
                    report["models"][name]["status"], health.STATUS_MISSING,
                )
        self.assertEqual(report["healthy_models"], 0)
        self.assertEqual(report["status"], health.STATUS_MISSING)
        self.assertEqual(
            {p["model"] for p in report["problems"]},
            {"dixon_coles", "btd", "gbm"},
        )

    def test_one_broken_artifact_cannot_read_as_healthy_overall(self) -> None:
        """The overall status is the worst member, not the majority."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            slots = {
                "good": _write(directory, "good.json", _DC_OK),
                "bad": directory / "never_fitted.json",
            }
            with patch.object(health, "artifact_slots", return_value=slots):
                report = health.collect_model_artifact_health(now=_TODAY)

        self.assertEqual(report["models"]["good"]["status"], health.STATUS_OK)
        self.assertEqual(report["status"], health.STATUS_MISSING)
        self.assertEqual(report["healthy_models"], 1)
        self.assertEqual(report["total_models"], 2)


class UnavailableIsNotOkTests(unittest.TestCase):
    """A check that could not run must not report the verdict of a clean run.

    The first draft returned ``None`` for both, so on an interpreter without
    ``openai`` the report printed ``feature_identity: "ok"`` while the log line
    said the check was unavailable.
    """

    def test_import_failure_yields_unavailable_not_ok(self) -> None:
        with patch.dict(
            "sys.modules",
            {"app.services.world_cup_engines.world_cup_gbm_features": None},
        ):
            verdict, problem = health._feature_identity({})
        self.assertEqual(verdict, health.FEATURE_IDENTITY_UNAVAILABLE)
        self.assertNotEqual(verdict, "ok")
        self.assertIsNone(problem)

    def test_clean_artifact_yields_ok(self) -> None:
        verdict, problem = health._feature_identity(
            {"feature_names": list(FEATURE_NAMES)},
        )
        self.assertEqual(verdict, "ok")
        self.assertIsNone(problem)

    def test_disagreeing_artifact_yields_the_problem_twice(self) -> None:
        verdict, problem = health._feature_identity(
            {"feature_names": list(reversed(FEATURE_NAMES))},
        )
        self.assertNotIn(verdict, ("ok", health.FEATURE_IDENTITY_UNAVAILABLE))
        self.assertEqual(verdict, problem)

    def test_unavailable_does_not_condemn_the_artifact(self) -> None:
        """Unavailable is not a *failure* either -- status must not go unreadable."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                pathlib.Path(tmp), "gbm_features.json",
                {"feature_names": list(FEATURE_NAMES), "fitted_at": _DC_OK["fitted_at"]},
            )
            with patch.object(
                health, "_feature_identity",
                return_value=(health.FEATURE_IDENTITY_UNAVAILABLE, None),
            ):
                entry = health.inspect_artifact("gbm", path, now=_TODAY)

        self.assertEqual(
            entry["feature_identity"], health.FEATURE_IDENTITY_UNAVAILABLE,
        )
        self.assertNotEqual(entry["status"], health.STATUS_UNREADABLE)


class ReportedValueTests(unittest.TestCase):
    """The numbers an operator acts on, and the shapes that would corrupt them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_served_coefficients_are_reported_per_model(self) -> None:
        dc = health.inspect_artifact(
            "dixon_coles", _write(self.dir, "dc.json", _DC_OK), now=_TODAY,
        )
        self.assertEqual(dc["coefficients"]["rho"], -0.05)
        self.assertEqual(dc["coefficients"]["mu"], 1.12)

        # The fixture deliberately *carries* a `rho`. A BTD artifact without one
        # cannot distinguish a per-model coefficient map from a blanket "report
        # every key I recognise" -- `raw.get("rho")` would be None either way.
        btd = health.inspect_artifact(
            "btd",
            _write(
                self.dir, "btd.json",
                {"gamma": 0.67, "home_advantage": 0.83, "rho": -0.99, "mu": 9.9},
            ),
            now=_TODAY,
        )
        self.assertEqual(
            btd["coefficients"], {"gamma": 0.67, "home_advantage": 0.83},
        )
        # `rho` and `mu` are Dixon-Coles parameters; a BTD fit does not produce
        # them, so quoting them here would attribute a coefficient to a model
        # that never fitted it.
        self.assertNotIn("rho", btd["coefficients"])
        self.assertNotIn("mu", btd["coefficients"])
        # And the converse: the DC entry must not pick up BTD's `gamma`.
        self.assertNotIn("gamma", dc["coefficients"])

    def test_sample_count_read_from_either_layout(self) -> None:
        flat = health.inspect_artifact(
            "dixon_coles",
            _write(self.dir, "flat.json", dict(_DC_OK, sample_count=8111)),
            now=_TODAY,
        )
        self.assertEqual(flat["sample_count"], 8111)

        nested = health.inspect_artifact(
            "gbm",
            _write(
                self.dir, "nested.json",
                {"dataset_stats": {"total_samples": 15852}},
            ),
            now=_TODAY,
        )
        self.assertEqual(nested["sample_count"], 15852)

    def test_a_boolean_is_not_a_sample_count(self) -> None:
        """``bool`` subclasses ``int``; a flag must not be reported as a count."""
        entry = health.inspect_artifact(
            "dixon_coles",
            _write(self.dir, "bool.json", dict(_DC_OK, sample_count=True)),
            now=_TODAY,
        )
        self.assertIsNone(entry["sample_count"])

    def test_a_boolean_is_not_a_coefficient(self) -> None:
        entry = health.inspect_artifact(
            "dixon_coles",
            _write(self.dir, "boolcoef.json", dict(_DC_OK, rho=True)),
            now=_TODAY,
        )
        self.assertNotIn("rho", entry["coefficients"])

    def test_fit_quality_collected_from_nested_keys(self) -> None:
        """BTD parks these under ``diagnostics``, GBM under ``validation_metrics``."""
        btd = health.inspect_artifact(
            "btd",
            _write(
                self.dir, "btd.json",
                {
                    "optimizer_success": True,
                    "diagnostics": {
                        "empirical_draw_rate": 0.231784,
                        "boosted_draw_prob_neutral_off": 0.191611,
                    },
                },
            ),
            now=_TODAY,
        )
        self.assertEqual(btd["fit_quality"]["empirical_draw_rate"], 0.231784)
        self.assertEqual(
            btd["fit_quality"]["boosted_draw_prob_neutral_off"], 0.191611,
        )

        gbm = health.inspect_artifact(
            "gbm",
            _write(
                self.dir, "gbm.json",
                {
                    "feature_names": list(FEATURE_NAMES),
                    "validation_metrics": {"home_rmse": 1.39, "away_rmse": 1.15},
                },
            ),
            now=_TODAY,
        )
        self.assertEqual(gbm["fit_quality"]["home_rmse"], 1.39)
        self.assertEqual(gbm["fit_quality"]["away_rmse"], 1.15)

    def test_report_never_leaks_per_team_vectors(self) -> None:
        """A health report is not a model dump."""
        payload = dict(
            _DC_OK,
            attack={"Brazil": 0.4, "France": 0.3},
            defense={"Brazil": -0.2},
        )
        with patch.object(
            health, "artifact_slots",
            return_value={"dixon_coles": _write(self.dir, "dc.json", payload)},
        ):
            report = health.collect_model_artifact_health(now=_TODAY)
        serialized = json.dumps(report, default=str)
        self.assertNotIn("Brazil", serialized)
        self.assertNotIn("France", serialized)
        self.assertNotIn("attack", serialized)


class ShippedArtifactStateTests(unittest.TestCase):
    """Pins the measured live state that motivated this increment.

    ``dixon_coles_params.json`` is committed to the repository with
    ``optimizer_success: false``. If a future refit fixes it, this test should be
    updated to assert the new state -- but it must not be deleted, because the
    point is that the state is *reported* rather than silent.
    """

    def test_shipped_dixon_coles_reports_its_convergence_flag(self) -> None:
        path = health.artifact_slots()["dixon_coles"]
        if not path.exists():
            self.skipTest("dixon_coles_params.json is not present")
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)

        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(entry["optimizer_success"], raw["optimizer_success"])
        if raw["optimizer_success"] is False:
            self.assertEqual(entry["status"], health.STATUS_NOT_CONVERGED)
            report = health.collect_model_artifact_health(now=_TODAY)
            self.assertIn(
                "dixon_coles", {p["model"] for p in report["problems"]},
            )

    def test_shipped_rho_is_the_value_the_engines_load(self) -> None:
        """The report must quote the served coefficient, not a second copy."""
        from app.kernel.engines import dixon_coles_engine

        path = health.artifact_slots()["dixon_coles"]
        if not path.exists():
            self.skipTest("dixon_coles_params.json is not present")
        entry = health.inspect_artifact("dixon_coles", path, now=_TODAY)
        dixon_coles_engine._load_rho.cache_clear()
        self.addCleanup(dixon_coles_engine._load_rho.cache_clear)
        self.assertAlmostEqual(
            entry["coefficients"]["rho"], dixon_coles_engine._load_rho(), places=9,
        )


class RouteIsMountedTests(unittest.TestCase):
    """The census must be reachable on the real app.

    Asserted against ``app.main``'s route table rather than a locally built
    ``FastAPI()``: a self-contained app would pass with the router never mounted,
    which is exactly how 584 fixtures went unreported until #75. No client is
    constructed, so no lifespan runs.
    """

    PATH = "/api/quality-metrics/model-artifacts"

    def test_path_is_registered_on_app_main(self) -> None:
        from app.main import app

        self.assertIn(self.PATH, {route.path for route in app.routes})

    def test_route_is_read_only(self) -> None:
        from app.main import app

        methods: set[str] = set()
        for route in app.routes:
            if getattr(route, "path", None) == self.PATH:
                methods |= set(getattr(route, "methods", set()))
        self.assertEqual(methods & {"POST", "PUT", "PATCH", "DELETE"}, set())
        self.assertIn("GET", methods)

    def test_handler_returns_the_census(self) -> None:
        from app.api.routes.quality_metrics import get_model_artifact_health

        payload = get_model_artifact_health()
        self.assertEqual(set(payload["models"]), set(health.artifact_slots()))
        self.assertIn("problems", payload)
        self.assertIn("status", payload)


class TrainerDeclaresFeatureNamesTests(unittest.TestCase):
    """Both LightGBM training sets must name their columns, from one source.

    The away set declared none, so the shipped ``gbm_away_model.txt`` carries
    ``Column_0..Column_16`` -- harmless only because the column order happened to
    match. Four hand-typed copies of the 17 names is what made that possible.
    """

    @staticmethod
    def _trainer_source() -> str:
        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "scripts" / "train_gbm_model.py"
        )
        return path.read_text(encoding="utf-8")

    def _dataset_calls(self) -> list[ast.Call]:
        tree = ast.parse(self._trainer_source())
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Dataset"
        ]

    def test_both_training_sets_declare_feature_name(self) -> None:
        calls = self._dataset_calls()
        # Validation sets inherit names through `reference=`; only the two
        # training sets need their own declaration.
        train_calls = [
            call for call in calls
            if not any(kw.arg == "reference" for kw in call.keywords)
        ]
        self.assertEqual(len(train_calls), 2, "expected a home and an away train set")
        for call in train_calls:
            with self.subTest(lineno=call.lineno):
                names = {kw.arg for kw in call.keywords}
                self.assertIn("feature_name", names)

    def test_feature_name_comes_from_the_shared_constant(self) -> None:
        """A literal list would be a copy; the point is that there is one source."""
        for call in self._dataset_calls():
            for keyword in call.keywords:
                if keyword.arg != "feature_name":
                    continue
                with self.subTest(lineno=call.lineno):
                    self.assertNotIsInstance(
                        keyword.value, ast.List,
                        "feature_name must not be a hand-typed list literal",
                    )
                    self.assertIn(
                        "FEATURE_NAMES", ast.unparse(keyword.value),
                    )

    def test_artifact_metadata_declares_the_same_constant(self) -> None:
        """`feature_names` in the artifact is what the serve-time check reads."""
        source = self._trainer_source()
        self.assertIn('"feature_names": list(FEATURE_NAMES)', source)

    def test_no_hand_typed_copy_of_the_names_remains(self) -> None:
        source = self._trainer_source()
        for name in ("elo_diff_abs", "h2h_draw_rate", "days_since_last_match_away"):
            with self.subTest(feature=name):
                self.assertNotIn(f'"{name}"', source)

    def test_trainer_binds_the_serving_constant_itself(self) -> None:
        """The trainer's name must be the serving module's object, not a copy.

        An equal-but-separate list would satisfy every assertion above and still
        drift the moment one of the two was edited (shape 11: pinning a shared
        constant without proving anyone reads the shared one).
        """
        import scripts.train_gbm_model as trainer

        self.assertIs(trainer.FEATURE_NAMES, FEATURE_NAMES)

    def test_away_importances_are_labelled_with_the_away_set(self) -> None:
        """Both logs read `train_data_h`, so away gains carried home names."""
        source = self._trainer_source()
        self.assertIn("train_data_a.feature_name", source)


class _StubBooster:
    """Minimal LightGBM booster stand-in with a declared feature width."""

    def __init__(self, width: int) -> None:
        self._width = width

    def num_feature(self) -> int:
        return self._width

    def predict(self, rows: object) -> list[float]:
        return [1.5]


class FeatureIdentityProblemTests(unittest.TestCase):
    """Each verdict distinguished from the others, reordering included.

    ``derive_gbm_features`` returns a bare positional list, so a booster trained
    on a different column order does not raise -- it returns confident nonsense.
    Reordering is therefore the case that matters most and the one a length check
    cannot see.
    """

    def test_matching_artifact_has_no_problem(self) -> None:
        self.assertIsNone(
            feature_identity_problem({"feature_names": list(FEATURE_NAMES)}),
        )

    def test_absent_metadata_is_not_a_problem(self) -> None:
        """A fresh checkout with no artifact must not be called a mismatch."""
        for meta in (None, {}, {"feature_names": []}):
            with self.subTest(meta=meta):
                self.assertIsNone(feature_identity_problem(meta))

    def test_reordering_is_detected(self) -> None:
        problem = feature_identity_problem(
            {"feature_names": list(reversed(FEATURE_NAMES))},
        )
        self.assertIsNotNone(problem)
        assert problem is not None
        self.assertIn("different order", problem)

    def test_swapping_two_adjacent_names_is_detected(self) -> None:
        """The minimal reordering: same names, same length, two positions moved."""
        names = list(FEATURE_NAMES)
        names[0], names[1] = names[1], names[0]
        problem = feature_identity_problem({"feature_names": names})
        self.assertIsNotNone(problem)
        assert problem is not None
        self.assertIn("different order", problem)

    def test_renamed_feature_is_reported_with_both_sides(self) -> None:
        names = list(FEATURE_NAMES)
        dropped = names[-1]
        names[-1] = "brand_new"
        problem = feature_identity_problem({"feature_names": names})
        self.assertIsNotNone(problem)
        assert problem is not None
        self.assertIn(dropped, problem)
        self.assertIn("brand_new", problem)

    def test_truncated_feature_list_is_detected(self) -> None:
        problem = feature_identity_problem({"feature_names": list(FEATURE_NAMES[:5])})
        self.assertIsNotNone(problem)
        assert problem is not None
        self.assertIn(str(len(FEATURE_NAMES)), problem)

    def test_booster_width_mismatch_is_detected(self) -> None:
        narrow = len(FEATURE_NAMES) - 1
        problem = feature_identity_problem({}, _StubBooster(narrow))
        self.assertIsNotNone(problem)
        assert problem is not None
        # Both numbers, so the message tells the operator which side is wrong.
        self.assertIn(str(narrow), problem)
        self.assertIn(str(len(FEATURE_NAMES)), problem)

    def test_matching_booster_width_is_accepted(self) -> None:
        self.assertIsNone(
            feature_identity_problem({}, _StubBooster(len(FEATURE_NAMES))),
        )

    def test_none_boosters_are_skipped(self) -> None:
        self.assertIsNone(feature_identity_problem({}, None, None))


class GbmFailsClosedOnFeatureMismatchTests(unittest.TestCase):
    """A disagreeing artifact must degrade to Elo, not serve wrong-column xG.

    The behavioural companion to :class:`FeatureIdentityProblemTests`: the checks
    above prove the *detector* works, this proves the engine *acts* on it.
    """

    def setUp(self) -> None:
        import lightgbm

        from app.services.world_cup_engines import world_cup_gbm_engine

        self.engine = world_cup_gbm_engine
        self.lightgbm = lightgbm
        self.engine._load_models.cache_clear()
        self.addCleanup(self.engine._load_models.cache_clear)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        # Real files on disk: `_load_models` gates on `.exists()`, and patching
        # `Path.exists` globally would lie to every other path in the process.
        (self.dir / "gbm_home_model.txt").write_text("stub", encoding="utf-8")
        (self.dir / "gbm_away_model.txt").write_text("stub", encoding="utf-8")

    def _load_with(self, meta: dict, *, width: int) -> tuple:
        """Run the real ``_load_models`` against a chosen artifact."""
        (self.dir / "gbm_features.json").write_text(
            json.dumps(meta), encoding="utf-8",
        )
        with patch.object(
            self.engine, "_HOME_MODEL_PATH", self.dir / "gbm_home_model.txt",
        ), patch.object(
            self.engine, "_AWAY_MODEL_PATH", self.dir / "gbm_away_model.txt",
        ), patch.object(
            self.engine, "_META_PATH", self.dir / "gbm_features.json",
        ), patch.object(
            # Patched on `lightgbm` itself: `_load_models` does `import lightgbm
            # as lgb` inside the function body, so the engine module has no `lgb`
            # attribute to patch.
            self.lightgbm, "Booster",
            side_effect=lambda **_: _StubBooster(width),
        ):
            self.engine._load_models.cache_clear()
            return self.engine._load_models()

    def test_reordered_artifact_refuses_the_models(self) -> None:
        reordered = {"feature_names": list(reversed(FEATURE_NAMES))}
        home_model, away_model, meta = self._load_with(
            reordered, width=len(FEATURE_NAMES),
        )

        self.assertIsNone(home_model)
        self.assertIsNone(away_model)
        # The metadata is still returned: the caller needs it to report *why*.
        self.assertEqual(meta, reordered)

    def test_matching_artifact_keeps_the_models(self) -> None:
        """The control: same harness, agreeing artifact, models retained.

        Without this the test above would pass against a `_load_models` that
        returned ``(None, None, meta)`` unconditionally.
        """
        matching = {"feature_names": list(FEATURE_NAMES)}
        home_model, away_model, meta = self._load_with(
            matching, width=len(FEATURE_NAMES),
        )

        self.assertIsNotNone(home_model)
        self.assertIsNotNone(away_model)
        self.assertEqual(meta, matching)

    def test_reordered_artifact_serves_the_honest_elo_label(self) -> None:
        """End to end: the served payload says it did not use the models."""
        reordered = {"feature_names": list(reversed(FEATURE_NAMES))}
        (self.dir / "gbm_features.json").write_text(
            json.dumps(reordered), encoding="utf-8",
        )
        with patch.object(
            self.engine, "_HOME_MODEL_PATH", self.dir / "gbm_home_model.txt",
        ), patch.object(
            self.engine, "_AWAY_MODEL_PATH", self.dir / "gbm_away_model.txt",
        ), patch.object(
            self.engine, "_META_PATH", self.dir / "gbm_features.json",
        ), patch.object(
            self.lightgbm, "Booster",
            side_effect=lambda **_: _StubBooster(len(FEATURE_NAMES)),
        ):
            self.engine._load_models.cache_clear()
            result = self.engine.predict_match_gbm(
                "Brazil", "France", elo_home=1800.0, elo_away=1750.0,
            )

        self.assertEqual(result["prediction_method"], "gbm_fallback_elo")
        self.assertIs(result["model_loaded"], False)

    def test_mismatch_surfaces_in_the_census(self) -> None:
        """The operator-visible half: the report names the disagreement."""
        reordered = {"feature_names": list(reversed(FEATURE_NAMES))}
        path = _write(self.dir, "gbm_features.json", reordered)
        entry = health.inspect_artifact("gbm", path, now=_TODAY)

        self.assertEqual(entry["status"], health.STATUS_UNREADABLE)
        self.assertNotEqual(entry["feature_identity"], "ok")
        detail = entry["detail"]
        assert detail is not None
        self.assertIn("Elo baseline", detail)
