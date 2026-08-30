"""E15: a fixture can kick off and never get a result, and nothing said so.

``fetch_outcome`` joins ``kernel_match_results``. A fixture with no result row
therefore cannot settle a prediction -- no calibration, no ``direction_accuracy``,
no engine score. P1-E9 (#73) fixed the *writer* for football. Nothing reported the
other way the same dead state arises: **the kickoff passes and the result never
lands**.

Measured on the live kernel DB on 2026-08-30, before this module existed:

    competition   past-due, no result row
    mlb           511
    laliga         25
    ligue1         17
    epl            15
    seriea         15
    nhl             1
    TOTAL         584   (96 of them more than 30 days overdue)

The oldest was ``mlb-746577``, 699 days past kickoff, ``status="finished"``, with
**no score at all** -- so ``backfill_results_from_fixtures``, which filters on
scores present, skips it forever. One real prediction was riding on a stale
fixture, so the cost was not hypothetical.

The two halves of this suite pull in different directions on purpose:

* the behavioural half drives the real census over a temp kernel DB and asserts
  the *counts*, so a summary that reports a plausible-looking shape with the
  wrong arithmetic is red;
* the partition half asserts that every competition the backfill understands is
  **named in the response even when it holds nothing**, because the failure this
  monitor exists to catch is a competition going silent -- and a competition
  missing from the response looks identical to a healthy one.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from app.services.fixture_freshness_service import (
    AGE_BUCKETS,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_STALE,
    fixture_freshness_summary,
)

_NOW = dt.datetime(2026, 8, 30, 12, 0, tzinfo=dt.timezone.utc)


class _IsolatedKernelDb(unittest.TestCase):
    """Every assertion runs against a temp kernel DB.

    ``init_kernel_db`` is handed the path explicitly rather than through a
    setting, and the resolved engine path is asserted to sit under the temp dir
    before anything writes -- a hermeticity guard whose absence let tests write
    into the production kernel DB for 43 days.
    """

    def setUp(self) -> None:
        from app.kernel import kernel_db

        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "kernel_test.db")
        kernel_db.close_kernel_db()
        kernel_db.init_kernel_db(self.db_path)
        engine = kernel_db._engine
        self.assertIsNotNone(engine, "init_kernel_db left the engine unset")
        resolved = Path(str(engine.url.database)).resolve()  # type: ignore[union-attr]
        self.assertEqual(
            resolved,
            Path(self.db_path).resolve(),
            f"kernel engine is not on the temp DB: {resolved}",
        )

    def tearDown(self) -> None:
        from app.kernel import kernel_db

        kernel_db.close_kernel_db()
        self._tmp.cleanup()

    def add_fixture(
        self,
        match_id: str,
        competition: str,
        *,
        days_ago: float,
        home_score: int | None = None,
        away_score: int | None = None,
        status: str = "scheduled",
        no_kickoff: bool = False,
        with_result: bool = False,
    ) -> None:
        from app.kernel import kernel_db
        from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult

        session = kernel_db.get_kernel_session()
        # A separate flag rather than ``kickoff=None``: a default of None cannot
        # tell "not supplied" from "supplied as null", and the null case is one
        # of the states under test.
        ko = None if no_kickoff else _NOW - dt.timedelta(days=days_ago)
        session.add(
            KernelMatchFixture(
                match_id=match_id,
                competition=competition,
                season="2026-27",
                home_team=f"{match_id}-home",
                away_team=f"{match_id}-away",
                kickoff_utc=ko.replace(tzinfo=None) if ko is not None else None,
                status=status,
                home_score=home_score,
                away_score=away_score,
            )
        )
        if with_result:
            session.add(
                KernelMatchResult(
                    match_id=match_id,
                    home_score=home_score if home_score is not None else 0,
                    away_score=away_score if away_score is not None else 0,
                    outcome="home_win",
                )
            )
        session.commit()
        # Closed here, not in a cleanup: unittest runs addCleanup callbacks
        # *after* tearDown, so the temp dir would be removed while this session
        # still held the file open -- which is a PermissionError on Windows.
        session.close()


class ThePastDueCensusTests(_IsolatedKernelDb):
    """The counts, driven through the real census."""

    def test_an_empty_kernel_reports_nothing_past_due(self) -> None:
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["total_fixtures"], 0)
        self.assertEqual(summary["past_due_unsettled"], 0)
        self.assertIsNone(summary["oldest_overdue_days"])

    def test_a_future_fixture_is_not_past_due(self) -> None:
        self.add_fixture("epl-future", "epl", days_ago=-3)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["past_due_unsettled"], 0)
        self.assertEqual(summary["competitions"]["epl"]["future"], 1)
        self.assertEqual(summary["competitions"]["epl"]["status"], STATUS_OK)

    def test_a_settled_fixture_is_not_past_due(self) -> None:
        self.add_fixture(
            "epl-done", "epl", days_ago=5, home_score=2, away_score=1,
            status="finished", with_result=True,
        )
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["past_due_unsettled"], 0)
        self.assertEqual(summary["competitions"]["epl"]["settled"], 1)
        self.assertEqual(summary["competitions"]["epl"]["status"], STATUS_OK)

    def test_a_kicked_off_fixture_with_no_result_is_past_due(self) -> None:
        self.add_fixture("epl-stale", "epl", days_ago=9)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["past_due_unsettled"], 1)
        self.assertEqual(summary["oldest_overdue_days"], 9)
        entry = summary["competitions"]["epl"]
        self.assertEqual(entry["past_due_unsettled"], 1)
        self.assertEqual(entry["oldest_overdue_days"], 9)
        self.assertEqual(entry["status"], STATUS_STALE)

    def test_the_scored_subset_is_counted_separately(self) -> None:
        """A score with no result row is a different operator action.

        The backfill can fix that one. A fixture with no score at all cannot be
        fixed by the backfill -- it needs the feed -- so collapsing the two into
        one number would send the operator to the wrong command.
        """
        self.add_fixture("epl-scored", "epl", days_ago=4, home_score=1, away_score=0,
                         status="finished")
        self.add_fixture("epl-blank", "epl", days_ago=4)
        summary = fixture_freshness_summary(now=_NOW)
        entry = summary["competitions"]["epl"]
        self.assertEqual(entry["past_due_unsettled"], 2)
        self.assertEqual(
            entry["scored_but_unsettled"], 1,
            "the scored fixture is the backfillable half and must be separable",
        )

    def test_a_finished_status_with_no_score_still_counts(self) -> None:
        """The 699-day live case: finished, unscored, invisible to the backfill."""
        self.add_fixture("mlb-old", "mlb", days_ago=699, status="finished")
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["past_due_unsettled"], 1)
        self.assertEqual(summary["oldest_overdue_days"], 699)
        self.assertEqual(summary["competitions"]["mlb"]["scored_but_unsettled"], 0)

    def test_a_fixture_with_no_kickoff_is_not_guessed_at(self) -> None:
        self.add_fixture("epl-nokick", "epl", days_ago=0, no_kickoff=True)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["past_due_unsettled"], 0)
        self.assertEqual(summary["competitions"]["epl"]["no_kickoff"], 1)

    def test_the_oldest_overdue_age_is_a_maximum_not_the_first_row(self) -> None:
        """``oldest_overdue_days`` must survive a younger row arriving first.

        This is the field that tells an operator whether a competition is a day
        behind or two years behind, and it is the only aggregate here that is
        not a count -- so "whichever row the scan reached first" has to be a
        wrong answer, not an accidentally-right one. Seeded youngest-first
        because a plain ``SELECT`` over a rowid table scans in insertion order,
        which is exactly the order that makes a first-wins shortcut look fine.
        """
        for index, days in enumerate((3, 40, 12)):
            self.add_fixture(f"epl-age{index}", "epl", days_ago=days)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["oldest_overdue_days"], 40)
        self.assertEqual(summary["competitions"]["epl"]["oldest_overdue_days"], 40)

    def test_the_oldest_overdue_age_is_a_maximum_not_the_last_row(self) -> None:
        """The other direction: oldest-first, so a last-wins rule is also wrong.

        Paired with its sibling above on purpose. Either test alone leaves one
        single-row shortcut looking correct, and the two differ only in seeding
        order, so between them no "pick the row at a fixed position" rule can
        report 40.
        """
        for index, days in enumerate((40, 3, 12)):
            self.add_fixture(f"epl-age{index}", "epl", days_ago=days)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["oldest_overdue_days"], 40)
        self.assertEqual(summary["competitions"]["epl"]["oldest_overdue_days"], 40)

    def test_the_live_shape_reproduces_its_measured_counts(self) -> None:
        """Six competitions at the measured live proportions, scaled down.

        The point is discrimination: the per-competition numbers must differ
        from each other and from the total, so an implementation that reports
        the same figure everywhere cannot pass.
        """
        seeded = {"mlb": 5, "laliga": 4, "ligue1": 3, "epl": 2, "seriea": 1}
        for competition, count in seeded.items():
            for index in range(count):
                self.add_fixture(
                    f"{competition}-s{index}", competition, days_ago=10 + index,
                )
        # one healthy competition, and one settled row inside a stale one
        self.add_fixture("nhl-ok", "nhl", days_ago=2, home_score=3, away_score=2,
                         status="finished", with_result=True)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["past_due_unsettled"], sum(seeded.values()))
        for competition, count in seeded.items():
            with self.subTest(competition=competition):
                self.assertEqual(
                    summary["competitions"][competition]["past_due_unsettled"],
                    count,
                )
                self.assertEqual(
                    summary["competitions"][competition]["status"], STATUS_STALE
                )
        self.assertEqual(summary["competitions"]["nhl"]["status"], STATUS_OK)
        self.assertEqual(summary["competitions"]["nhl"]["past_due_unsettled"], 0)


class TheAgeBucketsTests(_IsolatedKernelDb):
    """Buckets, because "584 overdue" and "584 overdue by a day" differ."""

    def test_every_bucket_is_reachable_and_lands_where_it_should(self) -> None:
        cases = {
            "under_1d": 0.5,
            "1_2d": 2.0,
            "3_7d": 5.0,
            "8_30d": 20.0,
            "over_30d": 200.0,
        }
        for name, days in cases.items():
            self.add_fixture(f"epl-{name}", "epl", days_ago=days)
        summary = fixture_freshness_summary(now=_NOW)
        for name in cases:
            with self.subTest(bucket=name):
                self.assertEqual(
                    summary["buckets"][name], 1,
                    f"bucket {name} did not receive its fixture: "
                    f"{summary['buckets']}",
                )
        self.assertEqual(sum(summary["buckets"].values()), len(cases))

    def test_the_bucket_names_are_exactly_the_declared_ones(self) -> None:
        self.add_fixture("epl-one", "epl", days_ago=4)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(
            sorted(summary["buckets"]),
            sorted(name for name, _ in AGE_BUCKETS),
        )

    def test_the_last_bucket_is_open_ended(self) -> None:
        """Otherwise the oldest fixtures fall out of the census silently."""
        self.assertIsNone(
            AGE_BUCKETS[-1][1],
            "the final bucket must have no upper bound, or a sufficiently old "
            "fixture is counted in past_due_unsettled and in no bucket",
        )
        self.add_fixture("mlb-ancient", "mlb", days_ago=5000)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(summary["buckets"]["over_30d"], 1)
        self.assertEqual(
            sum(summary["buckets"].values()), summary["past_due_unsettled"],
            "every past-due fixture must land in exactly one bucket",
        )

    def test_the_bucket_bounds_are_strictly_increasing(self) -> None:
        bounds = [upper for _name, upper in AGE_BUCKETS if upper is not None]
        self.assertEqual(bounds, sorted(set(bounds)))
        self.assertEqual(len(bounds), len(AGE_BUCKETS) - 1)


class EveryCompetitionIsNamedTests(_IsolatedKernelDb):
    """The partition half: a competition going silent must be visible.

    The failure this monitor exists to catch is a feed stopping. If the summary
    only lists competitions it found rows for, a competition whose ingestion
    died is **absent** from the response -- and absent reads exactly like
    healthy to anything consuming it. So the scope is asserted against the
    declared source (``BACKFILLABLE_COMPETITIONS``), with a denominator guard,
    and the empty case gets its own status rather than ``ok``.
    """

    def _declared(self) -> frozenset[str]:
        from app.services.historical_data_ingestor import BACKFILLABLE_COMPETITIONS

        return BACKFILLABLE_COMPETITIONS

    def test_the_declared_scope_is_not_trivially_small(self) -> None:
        self.assertGreaterEqual(
            len(self._declared()), 6,
            "denominator guard: an emptied scope would make the partition "
            "assertions below vacuously true",
        )

    def test_an_empty_kernel_still_names_every_declared_competition(self) -> None:
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(
            set(summary["competitions"]),
            set(self._declared()),
            "a competition missing from the response is indistinguishable from "
            "a healthy one",
        )

    def test_a_competition_with_no_fixtures_is_no_data_not_ok(self) -> None:
        """``ok`` means "checked and clean"; this one was never checkable."""
        self.add_fixture("epl-x", "epl", days_ago=1)
        summary = fixture_freshness_summary(now=_NOW)
        for code in sorted(self._declared()):
            with self.subTest(competition=code):
                entry = summary["competitions"][code]
                if entry["total"] == 0:
                    self.assertEqual(
                        entry["status"], STATUS_NO_DATA,
                        f"{code} holds no fixtures and must not report ok",
                    )
        self.assertEqual(summary["competitions"]["epl"]["status"], STATUS_STALE)

    def test_the_three_statuses_are_distinct(self) -> None:
        self.assertEqual(len({STATUS_NO_DATA, STATUS_OK, STATUS_STALE}), 3)

    def test_an_unknown_competition_in_the_kernel_is_still_reported(self) -> None:
        """A code the backfill does not know about must not vanish.

        The declared scope drives which competitions are *guaranteed* present;
        it must not become a filter that hides a competition the kernel really
        holds rows for.
        """
        self.add_fixture("zzz-1", "zzzleague", days_ago=6)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertIn("zzzleague", summary["competitions"])
        self.assertEqual(
            summary["competitions"]["zzzleague"]["past_due_unsettled"], 1
        )
        self.assertEqual(summary["past_due_unsettled"], 1)

    def test_the_totals_reconcile_with_the_per_competition_rows(self) -> None:
        for index, code in enumerate(sorted(self._declared())):
            self.add_fixture(f"{code}-r{index}", code, days_ago=index + 1)
        summary = fixture_freshness_summary(now=_NOW)
        self.assertEqual(
            summary["past_due_unsettled"],
            sum(e["past_due_unsettled"] for e in summary["competitions"].values()),
        )
        self.assertEqual(
            summary["total_fixtures"],
            sum(e["total"] for e in summary["competitions"].values()),
        )


class TheReadOnlyRouteTests(_IsolatedKernelDb):
    """The route is reachable, unauthenticated, and leaks no per-match detail."""

    def _get(self) -> dict:
        """Mount only this router on a bare app, as tests/test_quality_metrics does.

        ``app.main.app``'s lifespan re-initialises the kernel DB from settings,
        which would silently move the read off the temp DB this class asserts
        against.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.routes import quality_metrics as quality_metrics_routes

        app = FastAPI()
        app.include_router(quality_metrics_routes.router, prefix="/api")
        client = TestClient(app)
        response = client.get("/api/quality-metrics/fixture-freshness")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_route_answers_without_a_write_key(self) -> None:
        self.add_fixture("epl-route", "epl", days_ago=12)
        body = self._get()
        self.assertEqual(body["past_due_unsettled"], 1)
        self.assertEqual(body["competitions"]["epl"]["status"], STATUS_STALE)

    def test_the_response_carries_no_match_ids_or_team_names(self) -> None:
        """This module is unauthenticated, so it stays aggregate-only."""
        self.add_fixture("epl-secret", "epl", days_ago=3)
        serialized = repr(self._get())
        self.assertNotIn("epl-secret", serialized)
        self.assertNotIn("-home", serialized)
        self.assertNotIn("-away", serialized)

    def test_the_path_exists_on_the_production_app(self) -> None:
        """The bare-app mount above cannot see an unmounted router.

        Every other test in this class builds its own ``FastAPI()``, so all of
        them would keep passing if this router were never included in
        ``app.main`` -- a working service with no way in, which is the failure
        this repo keeps rediscovering. Asserted off the route table rather than
        through a client so the production lifespan never runs.
        """
        from app.main import app as production_app

        paths = {getattr(route, "path", "") for route in production_app.routes}
        self.assertIn("/api/quality-metrics/fixture-freshness", paths)
