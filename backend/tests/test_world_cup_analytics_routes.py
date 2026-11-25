import tempfile
import unittest
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import world_cup_analytics
from app.core.config import settings
from app.memory import loop_run_store
from app.models.world_cup_prediction import (
    Base,
    MatchFixture,
    MatchPrediction,
    MatchResult,
    PredictionHistory,
)
from app.services.odds_cache_service import OddsCache
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
    load_sports_facts,
)
from app.utils import sqlite_db
from app.utils.prediction_db import get_prediction_session_dep


AUTH_HEADERS = {"X-API-Key": "secret"}


class WorldCupAnalyticsRouteAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fact_patch = patch.object(
            settings,
            "SPORTS_FACT_FILE",
            str(Path(self.tmp.name) / "sports_facts.json"),
        )
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.fact_patch.start()
        self.loop_db_patch.start()

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

        app = FastAPI()

        def override_session():
            yield self.session

        app.dependency_overrides[get_prediction_session_dep] = override_session
        app.include_router(world_cup_analytics.router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.session.close()
        self.engine.dispose()
        self.loop_db_patch.stop()
        self.fact_patch.stop()
        self.tmp.cleanup()

    def _add_finished_fixture(self):
        self.session.add(
            MatchFixture(
                match_id="m1",
                fixture_id="m1",
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=datetime(2026, 6, 20, 18, 0, 0),
                venue="Test Stadium",
                stage="group_stage",
                status="finished",
                home_score=2,
                away_score=1,
            )
        )
        self.session.commit()

    def test_odds_cache_stats_handles_naive_cached_at(self):
        """SQLite hands cached_at back naive; the freshness window subtracts it
        from an aware ``now``. That combination raised TypeError and 500'd the
        route as soon as odds_cache had a single row.
        """
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        self.session.add_all([
            OddsCache(
                match_key="wc_team_a_vs_team_b",
                home_odds=2.1,
                draw_odds=3.2,
                away_odds=3.4,
                source="test",
                bookmakers_count=3,
                cached_at=now_naive,
            ),
            OddsCache(
                match_key="wc_team_c_vs_team_d",
                home_odds=1.8,
                draw_odds=3.5,
                away_odds=4.2,
                source="test",
                bookmakers_count=2,
                cached_at=now_naive - timedelta(hours=3),
            ),
        ])
        self.session.commit()

        resp = self.client.get("/analytics/odds-cache-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_entries"], 2)
        self.assertEqual(body["fresh_count"], 1)
        self.assertEqual(body["stale_count"], 1)
        # Criterion 5: the non-empty branch must carry the explicit switch too.
        self.assertIs(body["has_entries"], True)
        self.assertIn("cache_hit_rate", body)

    # ------------------------------------------------------------------
    # The odds-cache envelope became a published contract in audit 53.168.
    # These three reuse the route harness above; they are about the schema,
    # not about behaviour, so they sit next to the endpoint they guard.
    # ------------------------------------------------------------------
    ODDS_CACHE_KEYS = {
        "total_entries",
        "fresh_count",
        "stale_count",
        "estimated_api_calls_saved",
        "cache_hit_rate",
        "has_entries",
    }

    def _odds_cache_path_schema(self) -> dict:
        """The published schema for this route, with the ``$ref`` resolved.

        FastAPI emits ``{"$ref": "#/components/schemas/<Model>"}`` for a bound
        response model, so reading ``properties`` straight off the path entry
        raises KeyError -- the model's own schema lives under ``components``.
        """
        spec = self.app.openapi()
        entry = spec["paths"]["/analytics/odds-cache-stats"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        ref = entry.get("$ref")
        if not ref:
            return entry
        name = ref.rsplit("/", 1)[-1]
        return spec["components"]["schemas"][name]

    def test_odds_cache_stats_publishes_exactly_its_six_keys(self) -> None:
        """The published schema is the contract the dashboard reads.

        Comparing the wire keys to ``model_fields`` would be an empty
        assertion: the response base is ``extra="allow"``, so a field the model
        stops declaring is passed through as an extra and both sides stay
        equal. The OpenAPI schema is the other side that does move -- a field
        dropped from the model disappears from ``properties`` -- so one
        comparison catches model shrinkage *and* producer drift.
        """
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        self.session.add(
            OddsCache(
                match_key="wc_team_a_vs_team_b",
                home_odds=2.1,
                draw_odds=3.2,
                away_odds=3.4,
                source="test",
                bookmakers_count=3,
                cached_at=now_naive,
            )
        )
        self.session.commit()

        resp = self.client.get("/analytics/odds-cache-stats")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()), self.ODDS_CACHE_KEYS)
        self.assertEqual(set(self._odds_cache_path_schema()["properties"]), self.ODDS_CACHE_KEYS)

    def test_odds_cache_stats_empty_branch_publishes_the_same_six_keys(self) -> None:
        """Both return paths write the same keys.

        The empty branch is where a schema quietly diverges in practice: it is
        written by hand, returns early, and no field is read from a column that
        would make the difference visible.
        """
        resp = self.client.get("/analytics/odds-cache-stats")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()), self.ODDS_CACHE_KEYS)

    def test_odds_cache_stats_route_binds_the_named_model(self) -> None:
        """Binding the model is the half that is easy to lose silently.

        Without ``response_model`` the endpoint still returns 200 and the body
        still has six keys; only the published schema disappears. So the route
        itself is asserted, not the response.
        """
        from app.models.event import OddsCacheStatsResponse

        route = next(
            r
            for r in self.app.routes
            if getattr(r, "path", "") == "/analytics/odds-cache-stats"
        )
        self.assertIs(route.response_model, OddsCacheStatsResponse)

    def test_odds_cache_stats_empty_table_reports_explicit_switch(self):
        """Criterion 5 (audit 53.102.7): zeros alone cannot distinguish "never
        fetched any odds" from "odds fetching disabled/failed". The empty
        response carries has_entries=False and mirrors the non-empty branch's
        cache_hit_rate key.
        """
        resp = self.client.get("/analytics/odds-cache-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIs(body["has_entries"], False)
        self.assertIn("cache_hit_rate", body)
        self.assertEqual(body["total_entries"], 0)

    # ------------------------------------------------------------------
    # The quality-loop report became a published contract in audit 53.169.
    # It is the endpoint that owns the confidence buckets, so the bucket
    # schema is guarded separately: the dashboard's hand-written copy
    # declared six keys and silently dropped `lower` / `upper`.
    # ------------------------------------------------------------------
    QUALITY_LOOP_KEYS = {
        "status",
        "generated_at",
        "sample_policy",
        "counters",
        "overall",
        "by_engine",
        "trends",
        "consistency_issues",
        "integrated_weight_suggestion",
        "recommendations",
    }

    def _component_schema(self, name: str) -> dict:
        """One ``components.schemas`` entry, fetched by model name.

        The path entry only carries ``{"$ref": ...}`` for a bound model, and
        nested models never appear on the path at all -- so these assertions
        have to read ``components`` directly.
        """
        return self.app.openapi()["components"]["schemas"][name]

    def test_quality_loop_publishes_exactly_its_ten_keys(self) -> None:
        """The wire and the schema must name the same ten keys.

        ``model_fields`` would be an empty comparison here: the base is
        ``extra="allow"``, so a field the model stops declaring still reaches
        the wire and both sides stay equal. The schema is the side that moves.
        """
        resp = self.client.get("/analytics/quality-loop")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()), self.QUALITY_LOOP_KEYS)
        entry = self.app.openapi()["paths"]["/analytics/quality-loop"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        ref = entry["$ref"].rsplit("/", 1)[-1]
        self.assertEqual(set(self._component_schema(ref)["properties"]), self.QUALITY_LOOP_KEYS)

    def test_quality_loop_bucket_declares_all_eight_keys(self) -> None:
        """Eight, not six: ``lower`` / ``upper`` are the bucket's identity.

        The hand-written interface in the dashboard declared ``label``,
        ``count``, ``avg_confidence``, ``accuracy``, ``gap`` and ``is_usable``
        -- it dropped the two boundaries. Nothing noticed, because a narrower
        type still compiles: the extra keys were simply never reachable.
        """
        bucket = self._component_schema("QualityCalibrationBucket")

        self.assertEqual(
            set(bucket["properties"]),
            {
                "label",
                "lower",
                "upper",
                "count",
                "avg_confidence",
                "accuracy",
                "gap",
                "is_usable",
            },
        )
        self.assertIn("lower", bucket["required"])
        self.assertIn("upper", bucket["required"])

        # The wire, not just the schema. The bucket model is open, so a key the
        # producer adds would survive validation and change this count -- which
        # is the only way producer growth can be seen at all. A closed model
        # would have dropped it and left both sides at eight.
        resp = self.client.get("/analytics/quality-loop")
        self.assertEqual(resp.status_code, 200)
        buckets = resp.json()["overall"]["calibration_buckets"]
        self.assertTrue(buckets, "the empty report still writes every bucket")
        self.assertEqual(set(buckets[0]), set(bucket["properties"]))

    def test_quality_loop_summary_declares_all_eleven_keys(self) -> None:
        """Both ``_summarize`` paths write all eleven keys, so none is optional.

        The empty path is the one worth pinning: it writes the same dict with
        ``None`` / ``0`` / ``False`` values instead of returning a short one.
        """
        summary = self._component_schema("QualitySummary")

        self.assertEqual(len(summary["properties"]), 11)
        self.assertEqual(set(summary["required"]), set(summary["properties"]))

        resp = self.client.get("/analytics/quality-loop")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()["overall"]), set(summary["properties"]))

    def test_quality_loop_route_binds_the_named_model(self) -> None:
        """Without the binding the body is unchanged and only the schema goes.

        ``response_model_exclude_unset`` is part of the same contract: the
        integrated-weight block writes ``reason`` on one branch and
        ``learned_elo_weight`` / ``blend`` on the other, so a route that
        serialized defaults would add two nulls to the wire.
        """
        from app.models.event import QualityLoopReportResponse

        route = next(
            r for r in self.app.routes if getattr(r, "path", "") == "/analytics/quality-loop"
        )
        self.assertIs(route.response_model, QualityLoopReportResponse)
        self.assertTrue(route.response_model_exclude_unset)

    def test_quality_loop_empty_report_omits_the_unset_weight_keys(self) -> None:
        """The conditional three are absent, not null, on the insufficient path.

        An empty history gives every engine zero samples, so
        ``_integrated_weight_payload`` takes its rule-default branch: it writes
        ``reason`` and never writes ``learned_elo_weight`` / ``blend``. With
        defaults declared on the model, ``exclude_unset`` is what keeps those
        two off the wire -- without it the model would be describing a contract
        the producer does not write.
        """
        resp = self.client.get("/analytics/quality-loop")

        self.assertEqual(resp.status_code, 200)
        suggestion = resp.json()["integrated_weight_suggestion"]
        self.assertEqual(suggestion["source"], "rule_default")
        self.assertIn("reason", suggestion)
        self.assertNotIn("learned_elo_weight", suggestion)
        self.assertNotIn("blend", suggestion)

    def test_accuracy_stats_empty_table_reports_explicit_switch(self):
        """Criterion 5: an empty MatchResult table must be distinguishable from
        a refresh failure with empty stale data. The all-zero response carries
        has_results=False and writes the same eight keys as the populated
        branch -- the docstring here used to repeat the route's claim that the
        empty branch "adds predicted_matches/exact_score_correct, drops the dead
        by_engine key", which described an older shape (audit 53.170).
        """
        resp = self.client.get("/analytics/accuracy-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIs(body["has_results"], False)
        self.assertIn("predicted_matches", body)
        self.assertIn("exact_score_correct", body)
        self.assertNotIn("by_engine", body)
        self.assertEqual(body["total_matches"], 0)
        self.assertEqual(set(body), self.ACCURACY_KEYS)

    # ------------------------------------------------------------------
    # The accuracy envelope became a published contract in audit 53.170.
    # Both return paths write the same eight keys; the second test is the one
    # that would have failed against the shape the route's own comment
    # described.
    # ------------------------------------------------------------------
    ACCURACY_KEYS = {
        "total_matches",
        "predicted_matches",
        "outcome_accuracy",
        "avg_score_mae",
        "avg_brier_score",
        "exact_score_correct",
        "by_stage",
        "has_results",
    }

    def _accuracy_path_schema(self) -> dict:
        spec = self.app.openapi()
        entry = spec["paths"]["/analytics/accuracy-stats"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        return self._component_schema(entry["$ref"].rsplit("/", 1)[-1])

    def _add_finished_result_for_accuracy(self) -> None:
        self._add_finished_fixture()
        self.session.add(
            MatchResult(
                match_id="m1",
                final_home_score=2,
                final_away_score=1,
                outcome="home_win",
                finished_at=datetime(2026, 6, 20, 20, 0, 0),
                predicted_home_score=2.0,
                predicted_away_score=1.0,
                outcome_correct=1,
                # `refresh_prediction_accuracy` only aggregates results whose
                # brier_score is set -- without it the by_stage table stays
                # empty and the route silently reports no stage rows.
                brier_score=0.15,
                score_mae=0.5,
                home_error=0.0,
                away_error=0.0,
            )
        )
        self.session.commit()

    def test_accuracy_stats_publishes_exactly_its_eight_keys(self) -> None:
        """The wire and the schema must name the same eight keys.

        ``model_fields`` would be an empty comparison (the base is open); the
        published schema moves when the model shrinks, the wire moves when the
        producer grows.
        """
        resp = self.client.get("/analytics/accuracy-stats")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()), self.ACCURACY_KEYS)
        self.assertEqual(set(self._accuracy_path_schema()["properties"]), self.ACCURACY_KEYS)

    def test_accuracy_stats_populated_branch_writes_the_same_eight_keys(self) -> None:
        """The two branches are one contract, not two shapes.

        This is the assertion the stale comment would have made fail: it said
        the empty branch "adds predicted_matches/exact_score_correct, drops the
        dead by_engine key". `by_engine` is in neither branch, and both write
        all eight keys -- so the comment had been describing a shape that no
        longer existed, and nothing compared the two branches until now.
        """
        self._add_finished_result_for_accuracy()

        resp = self.client.get("/analytics/accuracy-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIs(body["has_results"], True)
        self.assertEqual(body["total_matches"], 1)
        self.assertEqual(set(body), self.ACCURACY_KEYS)

    def test_accuracy_stats_by_stage_rows_publish_six_keys(self) -> None:
        """``by_stage`` rows are the endpoint's only nested shape.

        ``refresh_prediction_accuracy`` runs inside the route, so a finished
        fixture with a result is enough to produce one row. The wire and the
        schema are compared together: six keys each, all required.
        """
        self._add_finished_result_for_accuracy()

        resp = self.client.get("/analytics/accuracy-stats")

        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["by_stage"]
        self.assertTrue(rows, "a finished group-stage fixture should produce a row")

        schema = self._component_schema("AccuracyByStage")
        self.assertEqual(set(rows[0]), set(schema["properties"]))
        self.assertEqual(len(rows[0]), 6)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_accuracy_stats_route_binds_the_named_model(self) -> None:
        """Without the binding the body is unchanged and only the schema goes."""
        from app.models.event import AccuracyStatsResponse

        route = next(
            r for r in self.app.routes if getattr(r, "path", "") == "/analytics/accuracy-stats"
        )
        self.assertIs(route.response_model, AccuracyStatsResponse)

    def test_accuracy_stats_with_results_reports_has_results_true(self):
        self._add_finished_fixture()
        self.session.add(MatchResult(
            match_id="m1",
            final_home_score=2,
            final_away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 20, 20, 0, 0),
            predicted_home_score=2.0,
            predicted_away_score=1.0,
            outcome_correct=1,
        ))
        self.session.commit()

        resp = self.client.get("/analytics/accuracy-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIs(body["has_results"], True)
        self.assertEqual(body["total_matches"], 1)

    # ------------------------------------------------------------------
    # The health banner became a published contract in audit 53.171, and the
    # timestamp it carries stopped being timezone-naive in the same commit.
    # ------------------------------------------------------------------
    SYSTEM_HEALTH_KEYS = {
        "status",
        "recent_predictions_24h",
        "cache_entries",
        "data_freshness_hours",
        "last_update",
    }

    def _system_health_path_schema(self) -> dict:
        spec = self.app.openapi()
        entry = spec["paths"]["/analytics/system-health"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        return self._component_schema(entry["$ref"].rsplit("/", 1)[-1])

    def _add_recent_prediction(self) -> None:
        self.session.add(
            MatchPrediction(
                match_id="health-1",
                predicted_home_score=1.1,
                predicted_away_score=0.9,
                home_win_prob=0.4,
                draw_prob=0.3,
                away_win_prob=0.3,
                confidence=0.6,
                prediction_method="elo_only",
            )
        )
        self.session.commit()

    def test_system_health_publishes_exactly_its_five_keys(self) -> None:
        """Five keys on the wire and in the schema, on the empty path.

        The empty path is the whole path this time: there is one return
        statement, so the only variability is the values, not the key set.
        """
        resp = self.client.get("/analytics/system-health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body), self.SYSTEM_HEALTH_KEYS)
        self.assertEqual(set(self._system_health_path_schema()["properties"]), self.SYSTEM_HEALTH_KEYS)
        self.assertEqual(body["status"], "stale")
        self.assertIsNone(body["last_update"])
        self.assertEqual(body["data_freshness_hours"], 0.0)

    def test_system_health_status_is_published_as_a_two_value_enum(self) -> None:
        """The producer writes one conditional with two arms; the schema says so.

        Without the ``Literal`` a typo in the third value would type-check on
        the client and render neither banner colour.
        """
        schema = self._system_health_path_schema()
        self.assertEqual(schema["properties"]["status"]["enum"], ["healthy", "stale"])
        self.assertIn("status", schema["required"])

    def test_system_health_timestamp_carries_an_offset(self) -> None:
        """`last_update` must be unambiguous, not merely well formatted.

        SQLite hands the column back naive, so `.isoformat()` used to emit
        `2026-06-20T12:00:00` with no marker. The client renders it with
        `new Date(...)`, which reads a marker-less string as *local* time -- in
        UTC+8 that is an eight-hour error in the "last updated" line. The
        dashboard's own test fixture had always fed `...Z`, i.e. the mock was
        more correct than the producer and hid the difference.
        """
        self._add_recent_prediction()

        resp = self.client.get("/analytics/system-health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "healthy")
        self.assertIsNotNone(body["last_update"])
        self.assertTrue(
            body["last_update"].endswith("+00:00"),
            f"expected an explicit UTC offset, got {body['last_update']!r}",
        )
        self.assertIsNotNone(datetime.fromisoformat(body["last_update"]).tzinfo)

    def test_system_health_route_binds_the_named_model(self) -> None:
        """Without the binding the body is unchanged and only the schema goes."""
        from app.models.event import SystemHealthResponse

        route = next(
            r for r in self.app.routes if getattr(r, "path", "") == "/analytics/system-health"
        )
        self.assertIs(route.response_model, SystemHealthResponse)

    def test_prediction_coverage_stale_entry_carries_an_offset(self) -> None:
        """The stale branch is the only one that emits `last_updated`.

        That is also why nothing noticed the missing zone: an empty table, a
        fresh prediction and a missing prediction all skip this line. The
        fixture therefore has to be a *scheduled* fixture whose prediction is
        older than the cutoff (audit 53.172).
        """
        self.session.add(
            MatchFixture(
                match_id="cov-1",
                fixture_id="cov-1",
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=datetime(2026, 7, 1, 18, 0, 0),
                venue="Test Stadium",
                stage="group_stage",
                status="scheduled",
            )
        )
        self.session.add(
            MatchPrediction(
                match_id="cov-1",
                predicted_home_score=1.0,
                predicted_away_score=0.5,
                home_win_prob=0.5,
                draw_prob=0.3,
                away_win_prob=0.2,
                confidence=0.6,
                prediction_method="elo_only",
                last_updated=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2),
            )
        )
        self.session.commit()

        resp = self.client.get("/analytics/prediction-coverage")

        self.assertEqual(resp.status_code, 200)
        stale = resp.json()["stale_predictions"]
        self.assertTrue(stale, "a two-day-old prediction must be reported as stale")
        self.assertTrue(
            stale[0]["last_updated"].endswith("+00:00"),
            f"expected an explicit UTC offset, got {stale[0]['last_updated']!r}",
        )

    TIMELINE_SNAPSHOT_KEYS = {
        "timestamp",
        "predicted_score",
        "outcome_probabilities",
        "confidence",
        "trigger",
        "match_minute",
        "actual_score",
    }

    def _timeline_path_schema(self) -> dict:
        spec = self.app.openapi()
        entry = spec["paths"]["/analytics/prediction-timeline"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        return self._component_schema(entry["$ref"].rsplit("/", 1)[-1])

    def _add_history(self, match_id: str = "tl-1") -> None:
        """Two snapshots, an hour apart, exercising the nullable columns.

        The first row leaves ``trigger`` and ``match_minute`` at ``NULL`` and
        carries no actual score; the second has all three. That is the only way
        to tell "the model mirrors the nullable column" from "the model declared
        what today's writer happens to produce".
        """
        self.session.add_all(
            [
                PredictionHistory(
                    match_id=match_id,
                    timestamp=datetime(2026, 6, 20, 13, 0, 0),
                    predicted_home_score=1.0,
                    predicted_away_score=0.5,
                    home_win_prob=0.5,
                    draw_prob=0.3,
                    away_win_prob=0.2,
                    confidence=0.6,
                ),
                PredictionHistory(
                    match_id=match_id,
                    timestamp=datetime(2026, 6, 20, 14, 0, 0),
                    predicted_home_score=1.5,
                    predicted_away_score=1.0,
                    home_win_prob=0.4,
                    draw_prob=0.35,
                    away_win_prob=0.25,
                    confidence=0.7,
                    trigger="goal_event",
                    match_minute=63,
                    actual_home_score=1,
                    actual_away_score=1,
                ),
            ]
        )
        self.session.commit()

    def test_prediction_timeline_publishes_seven_snapshot_keys(self) -> None:
        """The wire, key by key, including the two conditional shapes.

        ``total_updates`` is written only on this path -- the empty branch
        returns before counting anything, so it omits the key (asserted
        separately below, which is the only test that can tell the two
        serializations apart).
        """
        self._add_history()

        resp = self.client.get("/analytics/prediction-timeline", params={"match_id": "tl-1"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body), {"match_id", "snapshots", "total_updates"})
        self.assertEqual(body["total_updates"], 2)
        self.assertEqual(len(body["snapshots"]), 2)
        for snapshot in body["snapshots"]:
            self.assertEqual(set(snapshot), self.TIMELINE_SNAPSHOT_KEYS)

        first, second = body["snapshots"]
        self.assertEqual(first["predicted_score"], {"home": 1.0, "away": 0.5})
        self.assertEqual(
            first["outcome_probabilities"], {"home_win": 0.5, "draw": 0.3, "away_win": 0.2}
        )
        self.assertIsNone(first["trigger"])
        self.assertIsNone(first["match_minute"])
        self.assertIsNone(first["actual_score"])
        self.assertEqual(second["actual_score"], {"home": 1, "away": 1})

    def test_prediction_timeline_empty_branch_does_not_gain_total_updates(self) -> None:
        """Two keys, not three. This is what `exclude_unset` is buying.

        Declaring the field with a default and serializing normally would add
        ``"total_updates": null`` to a response that has never carried it --
        a contract that changes the wire while claiming to describe it.
        """
        resp = self.client.get("/analytics/prediction-timeline", params={"match_id": "absent"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body), {"match_id", "snapshots"})
        self.assertEqual(body["snapshots"], [])
        self.assertEqual(body["match_id"], "absent")

    def test_prediction_timeline_schema_marks_total_updates_optional(self) -> None:
        schema = self._timeline_path_schema()

        self.assertEqual(set(schema["properties"]), {"match_id", "snapshots", "total_updates"})
        self.assertEqual(set(schema["required"]), {"match_id", "snapshots"})

        snapshot = self._component_schema("PredictionTimelineSnapshot")
        self.assertEqual(set(snapshot["properties"]), self.TIMELINE_SNAPSHOT_KEYS)
        self.assertEqual(
            set(snapshot["required"]),
            self.TIMELINE_SNAPSHOT_KEYS,
            "every snapshot key is written unconditionally by the producer",
        )

        actual = self._component_schema("PredictionTimelineActualScore")
        self.assertEqual(set(actual["properties"]), {"home", "away"})
        self.assertEqual(set(actual["required"]), {"home", "away"})

    def test_prediction_timeline_timestamp_carries_an_offset(self) -> None:
        """`timestamp` was the fourth bare `.isoformat()` found in 53.172's sweep.

        The column is declared `DateTime(timezone=True)` and SQLite ignores that,
        so the string came out marker-less -- `new Date(...)` reads it as local
        time, eight hours off in UTC+8, exactly like `last_update` in 53.171.
        Nothing in the frontend consumes this route, which is why no rendering
        ever disagreed with it.
        """
        self._add_history()

        resp = self.client.get("/analytics/prediction-timeline", params={"match_id": "tl-1"})

        self.assertEqual(resp.status_code, 200)
        for snapshot in resp.json()["snapshots"]:
            self.assertTrue(
                snapshot["timestamp"].endswith("+00:00"),
                f"expected an explicit UTC offset, got {snapshot['timestamp']!r}",
            )

    def test_prediction_timeline_route_binds_the_named_model(self) -> None:
        from app.models.event import PredictionTimelineResponse

        route = next(
            r
            for r in self.app.routes
            if getattr(r, "path", "") == "/analytics/prediction-timeline"
        )
        self.assertIs(route.response_model, PredictionTimelineResponse)

    ENGINE_STATS_KEYS = {"total_predictions", "by_engine"}
    ENGINE_STATS_ENTRY_KEYS = {"count", "percentage", "avg_confidence"}

    def _engine_stats_path_schema(self) -> dict:
        spec = self.app.openapi()
        entry = spec["paths"]["/analytics/engine-stats"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        return self._component_schema(entry["$ref"].rsplit("/", 1)[-1])

    def _add_engine_predictions(self) -> None:
        """One prediction per bucket, plus a method no bucket is named after.

        ``bucket_engine`` maps by substring and falls through to ``hybrid``, so
        ``"mystery"`` has to land there -- if it ever landed nowhere, the
        percentages would stop summing to 100 while ``total_predictions`` still
        counted the row.
        """
        methods = ["elo_odds_fusion", "hybrid", "gbm", "integrated", "mystery"]
        for index, method in enumerate(methods):
            self.session.add(
                MatchPrediction(
                    match_id=f"engine-{index}",
                    predicted_home_score=1.0,
                    predicted_away_score=0.5,
                    home_win_prob=0.5,
                    draw_prob=0.3,
                    away_win_prob=0.2,
                    confidence=0.6,
                    prediction_method=method,
                )
            )
        self.session.commit()

    def test_engine_stats_counts_every_row_into_a_bucket(self) -> None:
        """Totality, not the key set -- the key set already has a better owner.

        ``test_engine_stats_serves_one_bucket_per_runnable_engine`` compares the
        buckets against the dispatch registry, which is the oracle this file
        should use for membership. Re-asserting membership here against
        ``ENGINE_NAMES`` would be the weaker copy of an existing guard.

        What that test cannot see is a row counted into ``total_predictions``
        and into no bucket: the keys stay correct, the total stays correct, and
        the percentages quietly stop summing to 100. ``bucket_engine`` falls
        through to ``hybrid`` for anything it does not recognise, so ``mystery``
        has to land there.
        """
        self._add_engine_predictions()

        resp = self.client.get("/analytics/engine-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body), self.ENGINE_STATS_KEYS)
        self.assertEqual(body["total_predictions"], 5)
        for entry in body["by_engine"].values():
            self.assertEqual(set(entry), self.ENGINE_STATS_ENTRY_KEYS)

        self.assertEqual(body["by_engine"]["hybrid"]["count"], 2)
        self.assertEqual(body["by_engine"]["gbm"]["count"], 1)
        self.assertEqual(body["by_engine"]["hybrid"]["percentage"], 40.0)
        total_percentage = sum(e["percentage"] for e in body["by_engine"].values())
        self.assertAlmostEqual(total_percentage, 100.0, places=6)

    def test_engine_stats_empty_table_reports_float_zeroes(self) -> None:
        """`0` and `0.0` are the same JSON number and not the same Python value.

        The producer used to write a bare ``0`` on this path. Declaring the
        field as ``float`` makes pydantic coerce it, which changes the bytes
        whether or not anyone intended it -- so the producer now writes ``0.0``
        and this test pins it. A client using ``isinstance(x, int)`` is the only
        thing that could ever tell the difference.
        """
        resp = self.client.get("/analytics/engine-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_predictions"], 0)
        for entry in body["by_engine"].values():
            self.assertEqual(entry["count"], 0)
            self.assertEqual(entry["avg_confidence"], 0.0)
            self.assertIsInstance(
                entry["percentage"],
                float,
                "the empty path must publish 0.0, not the int 0 it used to emit",
            )

    def test_engine_stats_schema_publishes_an_open_map(self) -> None:
        """``additionalProperties`` and no ``properties`` -- that is the decision.

        If this ever becomes a four-field model, the schema gains ``properties``
        and loses ``additionalProperties``, and the fifth engine becomes a 500.
        """
        schema = self._engine_stats_path_schema()
        self.assertEqual(set(schema["properties"]), self.ENGINE_STATS_KEYS)

        by_engine = schema["properties"]["by_engine"]
        self.assertNotIn("properties", by_engine)
        self.assertIn("additionalProperties", by_engine)

        entry = self._component_schema("EngineStatsEntry")
        self.assertEqual(set(entry["properties"]), self.ENGINE_STATS_ENTRY_KEYS)
        self.assertEqual(set(entry["required"]), self.ENGINE_STATS_ENTRY_KEYS)

    def test_engine_stats_route_binds_the_named_model(self) -> None:
        from app.models.event import EngineStatsResponse

        route = next(
            r for r in self.app.routes if getattr(r, "path", "") == "/analytics/engine-stats"
        )
        self.assertIs(route.response_model, EngineStatsResponse)

    def test_analytics_post_routes_require_write_key(self):
        paths = [
            "/analytics/result-fact-backfill?dry_run=true",
            "/analytics/consistency-repair?history_ids=1&dry_run=true",
            "/analytics/reconcile-scoring",
            "/analytics/post-match-backfill?dry_run=true",
            "/analytics/verified-result-correction",
        ]

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            for path in paths:
                with self.subTest(path=path):
                    resp = self.client.post(path, json={})
                    self.assertEqual(resp.status_code, 401)

    def test_consistency_repair_failure_is_safe_in_ledger_and_public_anomalies(self):
        from app.api.routes import quality_metrics as quality_metrics_routes
        from app.services import world_cup_quality_service

        sensitive = (
            "SELECT secret FROM C:/private/repair.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        self.client.app.include_router(quality_metrics_routes.router)
        client = TestClient(self.client.app, raise_server_exceptions=False)
        try:
            with (
                patch.object(settings, "API_WRITE_KEY", "secret"),
                patch.object(settings, "SCHEDULER_ENABLED", False),
                patch.object(quality_metrics_routes, "list_all_events", return_value=[]),
                patch.object(quality_metrics_routes, "calibration_summary", return_value={}),
                patch.object(
                    world_cup_quality_service,
                    "preview_consistency_history_repair",
                    side_effect=RuntimeError(sensitive),
                ),
            ):
                response = client.post(
                    "/analytics/consistency-repair?history_ids=1&dry_run=true",
                    headers=AUTH_HEADERS,
                )
                run = loop_run_store.last_run("world_cup_consistency_repair")
                anomalies = client.get("/quality-metrics/anomalies")
        finally:
            client.close()

        self.assertIsNotNone(run)
        exported = str({"response": response.text, "run": run, "anomalies": anomalies.json()})
        for fragment in (
            "SELECT secret", "C:/private", "fake-api-key", "fake-ticket",
            "fake-subprotocol", "upstream.example", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        safe_error = "Consistency repair failed: RuntimeError"
        self.assertEqual(response.status_code, 500)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], safe_error)
        failed = [a for a in anomalies.json()["anomalies"] if a["code"] == "scheduler_job_failed"]
        self.assertEqual(failed[0]["detail"]["error"], safe_error)

    def test_verified_result_correction_failure_hides_and_does_not_persist_sql(self):
        self._add_finished_fixture()
        sensitive_failure = (
            "SELECT secret FROM C:/private/correction.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        log_output = io.StringIO()
        log_handler = logging.StreamHandler(log_output)
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        correction_logger = logging.getLogger(
            "app.services.world_cup_verified_result_correction_service"
        )
        correction_logger.addHandler(log_handler)

        try:
            with (
                patch.object(settings, "API_WRITE_KEY", "secret"),
                patch(
                    "app.services.world_cup_verified_result_correction_service.import_sports_facts",
                    side_effect=RuntimeError(sensitive_failure),
                ),
            ):
                response = self.client.post(
                    "/analytics/verified-result-correction",
                    headers=AUTH_HEADERS,
                    json={
                        "match_id": "m1",
                        "home_score": 2,
                        "away_score": 1,
                        "source": "FIFA match centre",
                        "confirmed": True,
                    },
                )
        finally:
            correction_logger.removeHandler(log_handler)
            log_handler.close()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "Verified result correction failed")
        run = loop_run_store.last_run("world_cup_verified_result_correction")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "Verified result correction failed")
        output = log_output.getvalue()
        self.assertIn("RuntimeError", output)
        for fragment in (
            "SELECT secret",
            "C:/private",
            "fake-api-key",
            "fake-ticket",
            "fake-subprotocol",
            "https://upstream.example/private",
            "Traceback",
        ):
            self.assertNotIn(fragment, response.text)
            self.assertNotIn(fragment, output)
            self.assertNotIn(fragment, str(run))

    def test_verified_result_correction_requires_confirmation_and_source(self):
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchFixture(
                match_id="verify-group-a",
                fixture_id="verify-group-a",
                home_team="Switzerland",
                away_team="Team A2",
                kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_A",
                status="finished",
                home_score=1,
                away_score=0,
            ),
            MatchFixture(
                match_id="verify-group-b",
                fixture_id="verify-group-b",
                home_team="Colombia",
                away_team="Team B2",
                kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_B",
                status="finished",
                home_score=1,
                away_score=0,
            ),
        ])
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            unconfirmed = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": False,
                },
            )
            missing_source = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "confirmed": True,
                },
            )

        self.assertEqual(unconfirmed.status_code, 200)
        self.assertEqual(unconfirmed.json()["status"], "protected")
        self.assertEqual(missing_source.status_code, 422)
        fixture = self.session.query(MatchFixture).filter_by(match_id="verify-r16").first()
        self.assertEqual(fixture.status, "scheduled")
        self.assertIsNone(fixture.home_score)
        self.assertIsNone(fixture.away_score)

    def test_verified_result_correction_updates_fixture_scores_result_and_fact(self):
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchPrediction(
                match_id="verify-r16",
                predicted_home_score=1.2,
                predicted_away_score=0.8,
                home_win_prob=0.45,
                draw_prob=0.30,
                away_win_prob=0.25,
                confidence=0.62,
                prediction_method="elo_odds_fusion",
            ),
        ])
        self.session.commit()

        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "alice",
        }
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=headers,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "notes": "Final score verified from official match centre.",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["fixture"]["status"], "finished")
        self.assertEqual(body["fixture"]["score"], {"home": 1, "away": 0})
        self.assertEqual(body["scoring"]["status"], "scored")
        self.assertEqual(body["fact_import"]["imported"], 1)

        fixture = self.session.query(MatchFixture).filter_by(match_id="verify-r16").first()
        self.assertEqual(fixture.status, "finished")
        self.assertEqual(fixture.home_score, 1)
        self.assertEqual(fixture.away_score, 0)

        result = self.session.query(MatchResult).filter_by(match_id="verify-r16").first()
        self.assertIsNotNone(result)
        self.assertEqual(result.final_home_score, 1)
        self.assertEqual(result.final_away_score, 0)
        self.assertEqual(result.outcome, "home_win")

        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_id"], "wc2026:verified-result:verify-r16")
        self.assertEqual(facts[0]["match_id"], "verify-r16")
        self.assertEqual(facts[0]["score"], {"home": 1, "away": 0})
        self.assertEqual(facts[0]["source"], "FIFA match centre")
        self.assertEqual(facts[0]["source_url"], "https://www.fifa.com/en/match-centre/test")
        self.assertIn("operator=alice", facts[0]["notes"])

    def test_verified_result_correction_records_penalty_winner_fact(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "penalty_score": {"home": 4, "away": 3},
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "notes": "0-0 after extra time; Switzerland won 4-3 on penalties.",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["fixture"]["score"], {"home": 0, "away": 0})
        self.assertEqual(body["fact"]["winner"], "Switzerland")
        self.assertEqual(body["fact"]["penalty_score"], {"home": 4, "away": 3})

        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["score"], {"home": 0, "away": 0})
        self.assertEqual(facts[0]["winner"], "Switzerland")
        self.assertEqual(facts[0]["penalty_score"], {"home": 4, "away": 3})

    def test_verified_result_correction_removes_stale_fixture_readiness_blocker(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchFixture(
                match_id="verify-group-a",
                fixture_id="verify-group-a",
                home_team="Switzerland",
                away_team="Team A2",
                kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_A",
                status="finished",
                home_score=1,
                away_score=0,
            ),
            MatchFixture(
                match_id="verify-group-b",
                fixture_id="verify-group-b",
                home_team="Colombia",
                away_team="Team B2",
                kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_B",
                status="finished",
                home_score=1,
                away_score=0,
            ),
        ])
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            correction = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(correction.status_code, 200)

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "win_probability": {"Switzerland": 1.0},
                "reach_final": {"Switzerland": 1.0},
                "reach_semifinal": {"Switzerland": 1.0},
                "most_likely_winner": "Switzerland",
                "most_likely_winner_prob": 1.0,
            }

        with (
            patch(
                "app.api.routes.world_cup_analytics._utcnow",
                return_value=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            ),
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                return_value={
                    "real_data_readiness": {
                        "ok": True,
                        "issues": [],
                        "issue_details": [],
                    }
                },
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        readiness = resp.json()["real_data_readiness"]
        self.assertTrue(readiness["ok"])
        self.assertNotIn("stale_unfinished_knockout_fixture", readiness["issues"])

    def test_tournament_simulation_uses_verified_penalty_winner_fact(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchFixture(
                match_id="verify-group-a",
                fixture_id="verify-group-a",
                home_team="Switzerland",
                away_team="Team A2",
                kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_A",
                status="finished",
                home_score=1,
                away_score=0,
            ),
            MatchFixture(
                match_id="verify-group-b",
                fixture_id="verify-group-b",
                home_team="Colombia",
                away_team="Team B2",
                kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_B",
                status="finished",
                home_score=1,
                away_score=0,
            ),
        ])
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            correction = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "penalty_score": {"home": 4, "away": 3},
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )
        self.assertEqual(correction.status_code, 200)

        seen_fixtures = []

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            seen_fixtures.extend(fixtures)
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "win_probability": {"Switzerland": 1.0},
                "reach_final": {"Switzerland": 1.0},
                "reach_semifinal": {"Switzerland": 1.0},
                "most_likely_winner": "Switzerland",
                "most_likely_winner_prob": 1.0,
            }

        with (
            patch(
                "app.api.routes.world_cup_analytics._utcnow",
                return_value=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            ),
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                return_value={
                    "real_data_readiness": {
                        "ok": True,
                        "issues": [],
                        "issue_details": [],
                    }
                },
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["real_data_readiness"]["ok"])
        self.assertEqual(seen_fixtures[0]["winner"], "Switzerland")
        self.assertEqual(seen_fixtures[0]["penalty_score"], {"home": 4, "away": 3})

    def test_tied_finished_knockout_without_winner_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 0,
                        "away_score": 0,
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("ambiguous_finished_knockout_fixture", readiness["issues"])
        self.assertEqual(
            readiness["issue_details"][0]["match_id"],
            "verify-r16",
        )

    def test_tied_finished_knockout_without_penalty_score_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 0,
                        "away_score": 0,
                        "winner": "Switzerland",
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("ambiguous_finished_knockout_fixture", readiness["issues"])

    def test_verified_result_correction_rejects_tied_knockout_without_penalty_score(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "knockout_draw_requires_penalty_score")

    def test_tied_finished_knockout_with_mismatched_penalty_winner_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 0,
                        "away_score": 0,
                        "winner": "Switzerland",
                        "penalty_score": {"home": 3, "away": 4},
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("ambiguous_finished_knockout_fixture", readiness["issues"])

    def test_verified_result_correction_rejects_mismatched_penalty_winner(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "penalty_score": {"home": 3, "away": 4},
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "winner_must_match_penalty_score")

    def test_finished_knockout_with_winner_mismatching_score_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 2,
                        "away_score": 0,
                        "winner": "Colombia",
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("inconsistent_finished_knockout_fixture", readiness["issues"])

    def test_verified_result_correction_rejects_winner_mismatching_score(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 2,
                    "away_score": 0,
                    "winner": "Colombia",
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "winner_must_match_score")

    def test_result_fact_backfill_accepts_valid_write_key_for_dry_run(self):
        self._add_finished_fixture()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/result-fact-backfill?dry_run=true&limit=5",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["candidate_count"], 1)
        self.assertEqual(body["imported"], 0)

    def test_result_fact_backfill_success_hides_configured_fact_path(self):
        sensitive_path = (
            "C:/private/fake-api-key-ticket-fake-subprotocol-facts.json"
        )

        with (
            patch.object(settings, "API_WRITE_KEY", "secret"),
            patch.object(settings, "SPORTS_FACT_FILE", sensitive_path),
        ):
            response = self.client.post(
                "/analytics/result-fact-backfill?dry_run=true&limit=5",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["fact_store"]["configured_path"],
            "redacted",
        )
        for fragment in (
            "C:/private",
            "fake-api-key",
            "ticket",
            "fake-subprotocol",
        ):
            self.assertNotIn(fragment, response.text)

    def test_result_consistency_does_not_expose_configured_fact_path(self):
        sensitive_path = (
            "C:/private/fake-api-key-ticket-fake-subprotocol-facts.json"
        )
        with patch.object(settings, "SPORTS_FACT_FILE", sensitive_path):
            response = self.client.get("/analytics/result-consistency")

        self.assertEqual(response.status_code, 200)
        fact_store = response.json()["fact_store"]
        self.assertEqual(fact_store["configured_path"], "redacted")
        for fragment in (
            "C:/private",
            "fake-api-key",
            "ticket",
            "fake-subprotocol",
        ):
            self.assertNotIn(fragment, response.text)

    def test_result_fact_backfill_failure_hides_and_does_not_persist_path(self):
        self._add_finished_fixture()
        sensitive_failure = (
            "write failed for C:/private/fake-api-key-ticket-"
            "fake-subprotocol-facts.json"
        )

        with (
            patch.object(settings, "API_WRITE_KEY", "secret"),
            patch(
                "app.services.world_cup_result_fact_backfill_service.import_sports_facts",
                side_effect=OSError(sensitive_failure),
            ),
            TestClient(
                self.client.app,
                raise_server_exceptions=False,
            ) as response_client,
        ):
            response = response_client.post(
                "/analytics/result-fact-backfill?dry_run=false&confirm=true&limit=5",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(sensitive_failure, response.text)
        runs = self.client.get("/analytics/result-fact-backfill/runs")
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(runs.json()["runs"][0]["error"], "Result fact backfill failed")
        for fragment in (
            "C:/private",
            "fake-api-key",
            "ticket",
            "fake-subprotocol",
        ):
            self.assertNotIn(fragment, runs.text)

    def test_confirmed_result_fact_backfill_records_audit_metadata(self):
        self._add_finished_fixture()
        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "alice",
        }

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/result-fact-backfill?dry_run=false&confirm=true&limit=5",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["imported"], 1)
        run = loop_run_store.get_run(body["run_id"])
        self.assertEqual(run["result"]["audit_metadata"]["trigger_source"], "world-cup-dashboard")
        self.assertEqual(run["result"]["audit_metadata"]["operator"], "alice")
        self.assertEqual(
            run["result"]["audit_metadata"]["request_path"],
            "/analytics/result-fact-backfill",
        )

    def test_reconcile_scoring_records_audit_metadata(self):
        self._add_finished_fixture()
        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "charlie",
        }

        with (
            patch.object(settings, "API_WRITE_KEY", "secret"),
            patch(
                "app.api.routes.world_cup_analytics.score_all_finished_matches",
                return_value={
                    "status": "ok",
                    "total_finished": 1,
                    "scored": 0,
                    "skipped": 1,
                    "errors": 0,
                    "duration_ms": 5,
                    "run_id": "fake-run-id",
                    "audit_metadata": {
                        "trigger_source": "world-cup-dashboard",
                        "operator": "charlie",
                    },
                },
            ) as mock_score,
        ):
            resp = self.client.post(
                "/analytics/reconcile-scoring",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        mock_score.assert_called_once()
        passed_meta = mock_score.call_args.kwargs.get("audit_metadata") or mock_score.call_args[1].get("audit_metadata")
        self.assertIsNotNone(passed_meta)
        self.assertEqual(passed_meta.get("trigger_source"), "world-cup-dashboard")
        self.assertEqual(passed_meta.get("operator"), "charlie")

    def test_scoring_reconcile_failure_hides_and_does_not_persist_sql(self):
        sensitive_failure = (
            "SELECT secret FROM C:/private/scoring.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        log_output = io.StringIO()
        log_handler = logging.StreamHandler(log_output)
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        scoring_logger = logging.getLogger("app.services.world_cup_scoring_service")
        scoring_logger.addHandler(log_handler)

        try:
            with (
                patch.object(settings, "API_WRITE_KEY", "secret"),
                patch(
                    "app.services.world_cup_scoring_service.get_prediction_session",
                    return_value=self.session,
                ),
                patch.object(
                    self.session,
                    "query",
                    side_effect=RuntimeError(sensitive_failure),
                ),
                patch(
                    "app.services.world_cup_scoring_service.close_prediction_session",
                ),
                TestClient(
                    self.client.app,
                    raise_server_exceptions=False,
                ) as response_client,
            ):
                response = response_client.post(
                    "/analytics/reconcile-scoring",
                    headers=AUTH_HEADERS,
                )
        finally:
            scoring_logger.removeHandler(log_handler)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(sensitive_failure, response.text)
        runs = self.client.get("/analytics/reconcile-scoring/runs")
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(runs.json()["runs"][0]["error"], "Scoring reconciliation failed")
        rendered_logs = log_output.getvalue()
        for fragment in (
            "SELECT secret",
            "C:/private",
            "fake-api-key",
            "fake-ticket",
            "fake-subprotocol",
            "https://upstream.example/private",
            "Traceback",
        ):
            self.assertNotIn(fragment, runs.text)
            self.assertNotIn(fragment, rendered_logs)

    def test_post_match_backfill_exception_is_safe_in_public_history_and_anomalies(self):
        from app.api.routes import quality_metrics as quality_metrics_routes

        sensitive = (
            "SELECT secret FROM C:/private/backfill.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        self.client.app.include_router(quality_metrics_routes.router)
        client = TestClient(self.client.app, raise_server_exceptions=False)
        try:
            with (
                patch.object(settings, "API_WRITE_KEY", "secret"),
                patch.object(settings, "SCHEDULER_ENABLED", False),
                patch.object(quality_metrics_routes, "list_all_events", return_value=[]),
                patch.object(quality_metrics_routes, "calibration_summary", return_value={}),
                patch(
                    "app.services.world_cup_post_match_backfill_service.get_prediction_session",
                    return_value=self.session,
                ),
                patch(
                    "app.services.world_cup_post_match_backfill_service.close_prediction_session",
                ),
                patch(
                    "app.services.world_cup_post_match_backfill_service.sync_world_cup_fixtures",
                    side_effect=RuntimeError(sensitive),
                ),
            ):
                response = client.post(
                    "/analytics/post-match-backfill?dry_run=false",
                    headers=AUTH_HEADERS,
                )
                history = client.get("/analytics/post-match-backfill/runs")
                anomalies = client.get("/quality-metrics/anomalies")
                run = loop_run_store.last_run("world_cup_post_match_backfill")
        finally:
            client.close()

        self.assertIsNotNone(run)
        exported = str({
            "response": response.text,
            "history": history.json(),
            "anomalies": anomalies.json(),
            "run": run,
        })
        for fragment in (
            "SELECT secret", "C:/private", "fake-api-key", "fake-ticket",
            "fake-subprotocol", "upstream.example", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        safe_error = "Post-match backfill failed: RuntimeError"
        self.assertEqual(response.status_code, 500)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], safe_error)
        self.assertEqual(history.json()["runs"][0]["error"], safe_error)
        failed = [a for a in anomalies.json()["anomalies"] if a["code"] == "scheduler_job_failed"]
        self.assertEqual(failed[0]["detail"]["error"], safe_error)

    def test_post_match_backfill_response_includes_result_fact_backfill(self):
        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "dana",
        }
        service_result = {
            "status": "ok",
            "dry_run": False,
            "source": "football-data",
            "candidate_count": 1,
            "candidates": [],
            "scoring": {"scored": 1, "skipped": 0, "errors": 0},
            "quality": {"samples": 1},
            "result_fact_backfill": {
                "status": "ok",
                "dry_run": False,
                "candidate_count": 1,
                "imported": 1,
            },
        }

        with (
            patch.object(settings, "API_WRITE_KEY", "secret"),
            patch(
                "app.api.routes.world_cup_analytics.run_post_match_backfill",
                return_value=service_result,
            ) as mock_backfill,
        ):
            resp = self.client.post(
                "/analytics/post-match-backfill?dry_run=false",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["result_fact_backfill"]["imported"], 1)
        mock_backfill.assert_called_once()
        self.assertFalse(mock_backfill.call_args.kwargs["dry_run"])
        self.assertTrue(mock_backfill.call_args.kwargs["sync_first"])
        self.assertEqual(
            mock_backfill.call_args.kwargs["audit_metadata"]["operator"],
            "dana",
        )

    def test_post_match_backfill_does_not_expose_or_persist_provider_error_body(self):
        from app.services import football_data_source

        sensitive_error = (
            "SELECT secret C:/private/provider.db Authorization=Bearer fake-api-key "
            "ticket=fake-ticket subprotocol=fake-subprotocol "
            "https://upstream.example/private sensitive-input"
        )
        provider_response = type(
            "ProviderResponse",
            (),
            {"status_code": 500, "text": sensitive_error},
        )()
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(
                    football_data_source,
                    "FOOTBALL_DATA_API_KEY",
                    "provider-key",
                ), patch.object(
                    football_data_source,
                    "FOOTBALL_DATA_BASE_URL",
                    "https://provider.example/v4",
                ), patch(
                    "app.services.football_data_source.httpx.get",
                    return_value=provider_response,
                ), patch(
                    "app.services.world_cup_post_match_backfill_service."
                    "get_prediction_session",
                    return_value=self.session,
                ), patch(
                    "app.services.world_cup_post_match_backfill_service."
                    "close_prediction_session",
                ):
            post_response = self.client.post(
                "/analytics/post-match-backfill?dry_run=false",
                headers=AUTH_HEADERS,
            )
            history_response = self.client.get(
                "/analytics/post-match-backfill/runs"
            )

        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(post_response.json()["status"], "error")
        # §53.109: the job's own sentence plus a code. Until then this field was
        # the producer's message relayed verbatim, which is why the fragments
        # below had to be checked at all.
        self.assertEqual(
            post_response.json()["error"],
            "World Cup fixture sync failed",
        )
        self.assertEqual(post_response.json()["reason"], "http_error")
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()["runs"][0]["status"], "failed")
        self.assertEqual(
            history_response.json()["runs"][0]["error"],
            "World Cup fixture sync failed",
        )
        self.assertEqual(
            history_response.json()["runs"][0]["sync_reason"], "http_error"
        )
        exported = post_response.text + history_response.text
        for fragment in (
            "SELECT secret",
            "C:/private",
            "fake-api-key",
            "fake-ticket",
            "fake-subprotocol",
            "https://upstream.example/private",
            "sensitive-input",
        ):
            self.assertNotIn(fragment, exported)

    def test_engine_stats_includes_gbm_bucket(self):
        self.session.add(
            MatchPrediction(
                match_id="m-gbm",
                predicted_home_score=1.4,
                predicted_away_score=0.9,
                home_win_prob=0.55,
                draw_prob=0.25,
                away_win_prob=0.20,
                confidence=0.72,
                prediction_method="gbm_lightgbm",
            )
        )
        self.session.commit()

        resp = self.client.get("/analytics/engine-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_predictions"], 1)
        self.assertEqual(body["by_engine"]["gbm"]["count"], 1)
        self.assertEqual(body["by_engine"]["gbm"]["avg_confidence"], 0.72)

    def test_engine_stats_serves_one_bucket_per_runnable_engine(self):
        """This endpoint's bucket set must come from the engine registry.

        It used to carry its own ``engine_keys`` tuple -- a third copy of the
        engine vocabulary, in a different order, that no test named, so a fourth
        engine could be registered and reported by the quality loop while this
        endpoint kept serving the old set.  It now derives from ``ENGINE_NAMES``,
        and the oracle here is the dispatch registry: comparing the response with
        ``ENGINE_NAMES`` would be a set compared with itself, which stays green
        even when a member goes missing.
        """
        from app.services.world_cup_engines import ENGINES

        resp = self.client.get("/analytics/engine-stats")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(resp.json()["by_engine"]), set(ENGINES) | {"integrated"}
        )

    def test_prediction_coverage_reports_scheduled_matches_missing_predictions(self):
        self.session.add_all([
            MatchFixture(
                match_id="m-covered",
                fixture_id="m-covered",
                home_team="Covered A",
                away_team="Covered B",
                kickoff_utc=datetime(2026, 7, 10, 18, 0, 0),
                venue="Test Stadium",
                stage="quarterfinal",
                status="scheduled",
            ),
            MatchFixture(
                match_id="m-missing",
                fixture_id="m-missing",
                home_team="Missing A",
                away_team="Missing B",
                kickoff_utc=datetime(2026, 7, 11, 18, 0, 0),
                venue="Test Stadium",
                stage="quarterfinal",
                status="scheduled",
            ),
            MatchPrediction(
                match_id="m-covered",
                predicted_home_score=1.4,
                predicted_away_score=0.9,
                home_win_prob=0.55,
                draw_prob=0.25,
                away_win_prob=0.20,
                confidence=0.72,
                prediction_method="elo_only",
            ),
        ])
        self.session.commit()

        resp = self.client.get("/analytics/prediction-coverage")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["coverage_ok"])
        self.assertEqual(body["scheduled_count"], 2)
        self.assertEqual(body["predicted_count"], 1)
        self.assertEqual(body["missing_count"], 1)
        self.assertEqual(body["missing_predictions"][0]["match_id"], "m-missing")
        self.assertEqual(body["missing_predictions"][0]["home_team"], "Missing A")

    def test_tournament_simulation_cache_is_keyed_by_num_simulations(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="a1",
                    fixture_id="a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="b1",
                    fixture_id="b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="B",
                    status="scheduled",
                ),
            ]
        )
        self.session.commit()

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {"status": "ok", "num_simulations": num_simulations}

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ) as get_elo,
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ) as simulate,
        ):
            first = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            second = self.client.get("/analytics/tournament-simulation?num_simulations=8000")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["num_simulations"], 5000)
        self.assertEqual(second.json()["num_simulations"], 8000)
        self.assertEqual(simulate.call_count, 2)
        self.assertGreaterEqual(get_elo.await_count, 4)

    def test_tournament_simulation_does_not_expose_configured_fact_path(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="path-a1",
                    fixture_id="path-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="path-b1",
                    fixture_id="path-b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="B",
                    status="scheduled",
                ),
            ]
        )
        self.session.commit()
        sensitive_path = (
            "C:/private/fake-api-key-ticket-fake-subprotocol-facts.json"
        )

        with (
            patch.object(settings, "SPORTS_FACT_FILE", sensitive_path),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                return_value={"status": "ok"},
            ),
        ):
            response = self.client.get(
                "/analytics/tournament-simulation?force_refresh=true"
            )

        self.assertEqual(response.status_code, 200)
        fact_status = response.json()["sports_fact_status"]
        self.assertEqual(fact_status["configured_path"], "redacted")
        for fragment in (
            "C:/private",
            "fake-api-key",
            "ticket",
            "fake-subprotocol",
        ):
            self.assertNotIn(fragment, response.text)

    def test_tournament_simulation_force_refresh_bypasses_cached_result(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="force-a1",
                    fixture_id="force-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="force-b1",
                    fixture_id="force-b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="B",
                    status="scheduled",
                ),
            ]
        )
        self.session.commit()

        calls = []

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            calls.append(num_simulations)
            return {"status": "ok", "call_index": len(calls)}

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ) as simulate,
        ):
            first = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            second = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            refreshed = self.client.get(
                "/analytics/tournament-simulation?num_simulations=5000&force_refresh=true"
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(first.json()["call_index"], 1)
        self.assertEqual(second.json()["call_index"], 1)
        self.assertTrue(second.json()["cached"])
        self.assertEqual(refreshed.json()["call_index"], 2)
        self.assertFalse(refreshed.json()["cached"])
        self.assertEqual(simulate.call_count, 2)

    def test_tournament_simulation_cached_result_refreshes_real_data_readiness(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="cache-ready-a1",
                    fixture_id="cache-ready-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="cache-ready-b1",
                    fixture_id="cache-ready-b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="B",
                    status="scheduled",
                ),
            ]
        )
        self.session.commit()

        readiness_values = [
            {
                "real_data_readiness": {
                    "ok": False,
                    "issues": ["last_import_failed"],
                    "issue_details": [{"code": "last_import_failed"}],
                }
            },
            {
                "real_data_readiness": {
                    "ok": True,
                    "issues": [],
                    "issue_details": [],
                }
            },
        ]

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {"status": "ok", "call_index": 1}

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ) as simulate,
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                side_effect=readiness_values,
            ),
        ):
            first = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            second = self.client.get("/analytics/tournament-simulation?num_simulations=5000")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["real_data_readiness"]["ok"])
        self.assertTrue(second.json()["cached"])
        self.assertTrue(second.json()["real_data_readiness"]["ok"])
        self.assertEqual(simulate.call_count, 1)

    def test_tournament_simulation_accepts_uppercase_group_stage_fixtures(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="upper-a1",
                    fixture_id="upper-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=1,
                    away_score=0,
                ),
                MatchFixture(
                    match_id="upper-b1",
                    fixture_id="upper-b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=2,
                    away_score=1,
                ),
            ]
        )
        self.session.commit()

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {
                "status": "ok",
                "groups_seen": groups,
                "teams_seen": sorted(elo_cache),
                "num_simulations": num_simulations,
            }

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("error", body)
        self.assertEqual(set(body["groups_seen"]), {"GROUP_A", "GROUP_B"})
        self.assertEqual(body["groups_seen"]["GROUP_A"], ["Team A1", "Team A2"])
        self.assertEqual(body["groups_seen"]["GROUP_B"], ["Team B1", "Team B2"])

    def test_tournament_simulation_includes_real_data_readiness(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="ready-a1",
                    fixture_id="ready-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="ready-b1",
                    fixture_id="ready-b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="B",
                    status="scheduled",
                ),
            ]
        )
        self.session.commit()

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {"status": "ok", "win_probability": {"Team A1": 0.5}}

        with (
            patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""),
            patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
            patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
            patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
            patch(
                "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                return_value={"status": "failed", "error": "not configured"},
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000")

        self.assertEqual(resp.status_code, 200)
        readiness = resp.json()["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertIn("qualification_facts_missing", readiness["issues"])
        self.assertEqual(
            readiness["issue_details"][0]["message"],
            "尚未配置真实积分榜/出线数据源",
        )

    def test_tournament_simulation_excludes_knockout_match_result_losers(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="real-a1",
                    fixture_id="real-a1",
                    home_team="Germany",
                    away_team="Paraguay",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
                MatchFixture(
                    match_id="real-b1",
                    fixture_id="real-b1",
                    home_team="Canada",
                    away_team="Morocco",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
            ]
        )
        self.session.commit()
        import_sports_facts(
            [
                {
                    "kind": "match_result",
                    "stage": "ROUND_OF_32",
                    "home_team": "Germany",
                    "away_team": "Paraguay",
                    "score": {"home": 4, "away": 5},
                    "observed_at": "2026-06-30T02:26:08Z",
                },
                {
                    "kind": "match_result",
                    "stage": "ROUND_OF_16",
                    "home_team": "Canada",
                    "away_team": "Morocco",
                    "score": {"home": 0, "away": 3},
                    "observed_at": "2026-07-04T23:33:28Z",
                },
            ],
            replace=True,
        )

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {
                "status": "ok",
                "eliminated_seen": sorted(eliminated_teams or []),
                "teams_seen": sorted(elo_cache),
            }

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["eliminated_seen"], ["Canada", "Germany"])
        self.assertEqual(body["qualification_state"]["eliminated_teams"], ["Canada", "Germany"])
        self.assertEqual(body["qualification_state"]["knockout_result_fact_count"], 2)
        self.assertNotIn("Canada", body["teams_seen"])
        self.assertNotIn("Germany", body["teams_seen"])

    def test_tournament_simulation_uses_knockout_fixture_bracket_after_round_of_16_exists(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="r16-1",
                    fixture_id="r16-1",
                    home_team="Canada",
                    away_team="Morocco",
                    kickoff_utc=datetime(2026, 7, 4, 17, 0, 0),
                    venue="Test Stadium",
                    stage="ROUND_OF_16",
                    status="finished",
                    home_score=0,
                    away_score=3,
                ),
                MatchFixture(
                    match_id="r16-2",
                    fixture_id="r16-2",
                    home_team="Portugal",
                    away_team="Spain",
                    kickoff_utc=datetime(2026, 7, 6, 19, 0, 0),
                    venue="Test Stadium",
                    stage="ROUND_OF_16",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="group-a",
                    fixture_id="group-a",
                    home_team="Canada",
                    away_team="Spain",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
                MatchFixture(
                    match_id="group-b",
                    fixture_id="group-b",
                    home_team="Portugal",
                    away_team="Morocco",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
            ]
        )
        self.session.commit()

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "fixture_teams": [
                    (fixture["home_team"], fixture["away_team"], fixture["status"])
                    for fixture in fixtures
                ],
                "num_simulations": num_simulations,
                "win_probability": {"Morocco": 1.0},
                "reach_final": {"Morocco": 1.0},
                "reach_semifinal": {"Morocco": 1.0},
                "most_likely_winner": "Morocco",
                "most_likely_winner_prob": 1.0,
                "excluded_teams": ["Canada"],
            }

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ) as knockout_simulate,
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                return_value={"status": "wrong_path"},
            ) as group_simulate,
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["simulation_basis"], "knockout_fixtures")
        self.assertEqual(body["fixture_teams"], [
            ["Canada", "Morocco", "finished"],
            ["Portugal", "Spain", "scheduled"],
        ])
        knockout_simulate.assert_called_once()
        group_simulate.assert_not_called()

    def test_tournament_simulation_marks_past_unfinished_knockout_fixture_not_ready(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="stale-r16",
                    fixture_id="stale-r16",
                    home_team="Switzerland",
                    away_team="Colombia",
                    kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                    venue="Test Stadium",
                    stage="ROUND_OF_16",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="stale-group-a",
                    fixture_id="stale-group-a",
                    home_team="Switzerland",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=1,
                    away_score=0,
                ),
                MatchFixture(
                    match_id="stale-group-b",
                    fixture_id="stale-group-b",
                    home_team="Colombia",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=1,
                    away_score=0,
                ),
            ]
        )
        self.session.commit()

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "win_probability": {"Switzerland": 0.5, "Colombia": 0.5},
                "reach_final": {"Switzerland": 0.5, "Colombia": 0.5},
                "reach_semifinal": {"Switzerland": 0.5, "Colombia": 0.5},
                "most_likely_winner": "Switzerland",
                "most_likely_winner_prob": 0.5,
                "simulated_fixtures": fixtures,
            }

        with (
            patch(
                "app.api.routes.world_cup_analytics._utcnow",
                return_value=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            ),
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                return_value={
                    "real_data_readiness": {
                        "ok": True,
                        "issues": [],
                        "issue_details": [],
                    }
                },
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        readiness = resp.json()["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertIn("stale_unfinished_knockout_fixture", readiness["issues"])
        self.assertEqual(readiness["issue_details"][0]["match_id"], "stale-r16")
        self.assertIn("Switzerland", readiness["issue_details"][0]["message"])


if __name__ == "__main__":
    unittest.main()
