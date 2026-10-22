import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.api.routes.world_cup_predictions import _serialize_history_entry, _serialize_prediction
from app.api.routes import world_cup_predictions
from app.models.world_cup_prediction import MatchPrediction, PredictionHistory


AUTH_HEADERS = {"X-API-Key": "secret"}


def naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _prediction_client() -> TestClient:
    app = FastAPI()
    app.include_router(world_cup_predictions.router)
    return TestClient(app)


class WorldCupPredictionRoutesTests(unittest.TestCase):
    def test_prediction_write_routes_require_write_key(self):
        post_paths = [
            "/world-cup/predictions/init-db",
            "/world-cup/predictions/sync-fixtures",
            "/world-cup/predictions/matches/m1/predict",
            "/world-cup/predictions/matches/m1/analyze",
            "/world-cup/predictions/batch-predict",
            "/world-cup/predictions/batch-switch-engine?engine=elo_odds",
            "/world-cup/predictions/auto-tune/elo_odds?background=true",
            "/world-cup/predictions/batch-optimize",
            "/world-cup/predictions/matches/m1/optimize",
        ]
        client = _prediction_client()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            for path in post_paths:
                with self.subTest(path=path):
                    resp = client.post(path)
                    self.assertEqual(resp.status_code, 401)

            stream_resp = client.get(
                "/world-cup/predictions/batch-switch-engine-stream?engine=elo_odds"
            )
            self.assertEqual(stream_resp.status_code, 401)

    def test_prediction_write_route_accepts_valid_write_key(self):
        client = _prediction_client()

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.api.routes.world_cup_predictions.init_prediction_db") as init_mock:
            resp = client.post(
                "/world-cup/predictions/init-db",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        init_mock.assert_called_once_with()

    def test_prediction_db_failure_does_not_expose_exception_text(self):
        client = _prediction_client()
        sensitive_error = (
            "sqlite:////srv/private/predictions.db?token=fake-secret "
            "SELECT private_data"
        )

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.world_cup_predictions.init_prediction_db",
                    side_effect=RuntimeError(sensitive_error),
                ):
            resp = client.post(
                "/world-cup/predictions/init-db",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(
            resp.json(),
            {"detail": "Prediction database initialization failed"},
        )
        self.assertNotIn("fake-secret", resp.text)
        self.assertNotIn("/srv/private", resp.text)
        self.assertNotIn("SELECT private_data", resp.text)

    def test_fixture_sync_failure_does_not_expose_exception_text(self):
        client = _prediction_client()
        sensitive_error = (
            "https://upstream.example/private?api_key=fake-secret "
            "Authorization: Bearer fake-authorization"
        )

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.football_data_source.fetch_world_cup_fixtures",
                    side_effect=RuntimeError(sensitive_error),
                ):
            resp = client.post(
                "/world-cup/predictions/sync-fixtures",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"detail": "Fixture synchronization failed"})
        self.assertNotIn("fake-secret", resp.text)
        self.assertNotIn("fake-authorization", resp.text)
        self.assertNotIn("upstream.example", resp.text)

    def test_trigger_prediction_failure_does_not_expose_exception_text(self):
        client = _prediction_client()
        pipeline = AsyncMock(
            return_value={
                "status": "error",
                "error": (
                    "Prediction failed: postgresql://user:fake-secret@"
                    "private-db/predictions SELECT private_data"
                ),
            }
        )

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.run_prediction_pipeline",
                    pipeline,
                ):
            resp = client.post(
                "/world-cup/predictions/matches/m1/predict",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"detail": "Prediction generation failed"})
        self.assertNotIn("fake-secret", resp.text)
        self.assertNotIn("private-db", resp.text)
        self.assertNotIn("SELECT private_data", resp.text)

    def test_match_not_found_preserves_existing_error_semantics(self):
        client = _prediction_client()
        pipeline = AsyncMock(
            return_value={"status": "error", "error": "Match not found"}
        )

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.run_prediction_pipeline",
                    pipeline,
                ):
            resp = client.post(
                "/world-cup/predictions/matches/missing/predict",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"detail": "Match not found"})

    def test_batch_prediction_failure_does_not_expose_exception_text(self):
        client = _prediction_client()
        # The fixture is `batch_predict_matches`'s real shape: it builds its
        # summary literal with all ten keys before it runs anything, so the four
        # engine counters are present even when nothing succeeded. `POST
        # /batch-predict` now binds `BatchPredictResponse` (audit 53.74), whose
        # fields are all required for that reason -- a five-key fixture would be
        # rejected by the response model rather than by anything real.
        batch = AsyncMock(
            return_value={
                "status": "ok",
                "total": 1,
                "succeeded": 0,
                "failed": 1,
                "skipped": 0,
                "elo_odds_count": 0,
                "hybrid_count": 0,
                "integrated_count": 0,
                "gbm_count": 0,
                "predictions": [
                    {
                        "status": "error",
                        "match_id": "m1",
                        "error": (
                            "postgresql://user:fake-secret@private-db "
                            "SELECT private_data"
                        ),
                    }
                ],
            }
        )

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.batch_predict_matches",
                    batch,
                ):
            resp = client.post(
                "/world-cup/predictions/batch-predict",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        self.assertEqual(resp.json()["failed"], 1)
        self.assertEqual(
            resp.json()["predictions"][0],
            {
                "status": "error",
                "match_id": "m1",
                "error": "Prediction generation failed",
            },
        )
        self.assertNotIn("fake-secret", resp.text)
        self.assertNotIn("private-db", resp.text)
        self.assertNotIn("SELECT private_data", resp.text)

    def test_trigger_prediction_accepts_gbm_engine(self):
        """The frontend's engine-comparison card posts engine="gbm".

        The pipeline implements gbm (its Step 3 whitelist and `get_engine("gbm")`
        branch), so the request-model engine type has to permit it — a narrower
        Literal would reject a working request with 422.
        """
        client = _prediction_client()
        pipeline = AsyncMock(return_value={"status": "ok", "match_id": "m1"})

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.run_prediction_pipeline",
                    pipeline,
                ):
            resp = client.post(
                "/world-cup/predictions/matches/m1/predict",
                json={"engine": "gbm"},
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(pipeline.await_args.kwargs["engine"], "gbm")

    def test_batch_switch_engine_stream_reports_a_gbm_bucket(self):
        """The SSE summary buckets every engine, gbm included.

        This is a *second, independent copy* of the same aggregation as
        ``batch_predict_matches`` -- it used to have the same three buckets and
        the same hole. The final ``complete`` event is what the operator console
        renders, so a gbm run has to land in ``gbm_count`` here too; a guard on
        only the pipeline copy would leave this one free to regress.
        """
        client = _prediction_client()
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [
            SimpleNamespace(match_id="m1")
        ]
        pipeline = AsyncMock(return_value={"status": "ok", "engine_used": "gbm"})

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.world_cup_predictions.get_prediction_session",
                    return_value=session,
                ), \
                patch("app.api.routes.world_cup_predictions.close_prediction_session"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.run_prediction_pipeline",
                    pipeline,
                ):
            resp = client.get(
                "/world-cup/predictions/batch-switch-engine-stream?engine=gbm",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        complete = [
            line
            for line in resp.text.splitlines()
            if line.startswith("data: ") and '"gbm_count"' in line
        ]
        self.assertEqual(len(complete), 1, resp.text)
        payload = json.loads(complete[0][len("data: "):])
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["gbm_count"], 1)
        self.assertEqual(payload["elo_odds_count"], 0)
        self.assertEqual(payload["hybrid_count"], 0)
        self.assertEqual(payload["integrated_count"], 0)

    def test_trigger_prediction_rejects_unknown_engine(self):
        """An unsupported engine is a client error, not a 500.

        The pipeline validates the name at runtime too, but that path surfaces as
        HTTP 500; validating at the boundary returns 422 and never starts a run.
        """
        client = _prediction_client()
        pipeline = AsyncMock(return_value={"status": "ok", "match_id": "m1"})

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.run_prediction_pipeline",
                    pipeline,
                ):
            resp = client.post(
                "/world-cup/predictions/matches/m1/predict",
                json={"engine": "not_an_engine"},
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 422)
        pipeline.assert_not_awaited()

    def test_auto_tune_failure_is_safe_after_restart_and_public_poll(self):
        import asyncio
        import tempfile

        from app.memory import optimization_task_store
        from app.models.world_cup_prediction import Base, MatchFixture, MatchPrediction
        from app.services import engine_auto_tuning_async, optimization_task_manager
        from app.utils import prediction_db, sqlite_db

        sensitive = (
            "SELECT secret FROM C:/private/optimization.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        client = _prediction_client()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "loop.db")
        ), patch.object(
            settings,
            "WORLD_CUP_PREDICTION_DB_FILE",
            str(Path(tmp) / "predictions.db"),
        ):
            optimization_task_store._INITIALIZED.clear()
            prediction_db._engine = None
            prediction_db._SessionLocal = None
            Base.metadata.create_all(prediction_db.get_prediction_engine())
            session = prediction_db.get_prediction_session()
            session.add_all([
                MatchFixture(
                    match_id="m-sensitive",
                    fixture_id="m-sensitive",
                    home_team="Team A",
                    away_team="Team B",
                    kickoff_utc=naive(),
                    stage="group_stage",
                    status="scheduled",
                ),
                MatchPrediction(
                    match_id="m-sensitive",
                    predicted_home_score=1.0,
                    predicted_away_score=0.0,
                    home_win_prob=0.6,
                    draw_prob=0.25,
                    away_win_prob=0.15,
                    confidence=0.6,
                    prediction_method="elo_odds",
                ),
            ])
            session.commit()
            prediction_db.close_prediction_session(session)

            manager = optimization_task_manager.OptimizationTaskManager()
            task = asyncio.run(manager.create_task("elo_odds"))
            with patch.object(optimization_task_manager, "_task_manager", manager), patch.object(
                engine_auto_tuning_async,
                "optimize_prediction_with_ai",
                new=AsyncMock(side_effect=RuntimeError(sensitive)),
            ):
                asyncio.run(
                    engine_auto_tuning_async.run_async_optimization(
                        "elo_odds", task.task_id
                    )
                )

            stored = optimization_task_store.get_task(task.task_id)
            rehydrated = optimization_task_manager.OptimizationTaskManager()
            with patch.object(optimization_task_manager, "_task_manager", rehydrated):
                response = client.get(
                    f"/world-cup/predictions/auto-tune/status/{task.task_id}"
                )
            prediction_db._engine.dispose()
            prediction_db._engine = None
            prediction_db._SessionLocal = None

        self.assertIsNotNone(stored)
        exported = str(stored) + response.text
        for fragment in (
            "SELECT secret", "C:/private", "fake-api-key", "fake-ticket",
            "fake-subprotocol", "upstream.example", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        self.assertEqual(response.status_code, 200)
        task_payload = response.json()["task"]
        self.assertEqual(task_payload["status"], "completed")
        self.assertEqual(
            task_payload["result"]["optimization_summary"]["errors"][0]["error"],
            "Optimization failed: RuntimeError",
        )
        self.assertTrue(any(
            "Optimization failed: RuntimeError" in entry["message"]
            for entry in task_payload["logs"]
        ))

    def test_auto_tune_task_failure_is_safe_after_restart_and_public_poll(self):
        import asyncio
        import tempfile

        from sqlalchemy import text

        from app.memory import optimization_task_store
        from app.models.world_cup_prediction import Base
        from app.services import engine_auto_tuning_async, optimization_task_manager
        from app.utils import prediction_db, sqlite_db

        client = _prediction_client()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "loop.db")
        ), patch.object(
            settings,
            "WORLD_CUP_PREDICTION_DB_FILE",
            str(Path(tmp) / "predictions.db"),
        ):
            optimization_task_store._INITIALIZED.clear()
            prediction_db._engine = None
            prediction_db._SessionLocal = None
            engine = prediction_db.get_prediction_engine()
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE match_fixtures RENAME COLUMN home_team TO private_home_team"
                ))

            manager = optimization_task_manager.OptimizationTaskManager()
            task = asyncio.run(manager.create_task("elo_odds"))
            with patch.object(optimization_task_manager, "_task_manager", manager):
                asyncio.run(
                    engine_auto_tuning_async.run_async_optimization(
                        "elo_odds", task.task_id
                    )
                )

            stored = optimization_task_store.get_task(task.task_id)
            rehydrated = optimization_task_manager.OptimizationTaskManager()
            with patch.object(optimization_task_manager, "_task_manager", rehydrated):
                response = client.get(
                    f"/world-cup/predictions/auto-tune/status/{task.task_id}"
                )
            prediction_db._engine.dispose()
            prediction_db._engine = None
            prediction_db._SessionLocal = None

        self.assertIsNotNone(stored)
        exported = str(stored) + response.text
        for fragment in (
            "SELECT match_fixtures", "private_home_team", "predictions.db",
            "OperationalError)", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        self.assertEqual(response.status_code, 200)
        task_payload = response.json()["task"]
        self.assertEqual(task_payload["status"], "failed")
        self.assertEqual(task_payload["error"], "优化任务失败: OperationalError")

    def test_match_optimization_exception_is_safe_in_response_and_rendered_log(self):
        import tempfile

        from app.models.world_cup_prediction import Base, MatchFixture, MatchPrediction
        from app.services import world_cup_ai_optimization_service
        from app.utils import prediction_db

        sensitive = (
            "SELECT secret FROM C:/private/optimization.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        rendered_logs = []

        class RenderedLogHandler(logging.Handler):
            def emit(self, record):
                rendered_logs.append(self.format(record))

        log_handler = RenderedLogHandler()
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        world_cup_ai_optimization_service.logger.addHandler(log_handler)
        client = _prediction_client()

        try:
            with tempfile.TemporaryDirectory() as tmp, patch.object(
                settings,
                "WORLD_CUP_PREDICTION_DB_FILE",
                str(Path(tmp) / "predictions.db"),
            ):
                prediction_db._engine = None
                prediction_db._SessionLocal = None
                Base.metadata.create_all(prediction_db.get_prediction_engine())
                session = prediction_db.get_prediction_session()
                session.add_all([
                    MatchFixture(
                        match_id="m-sensitive",
                        fixture_id="m-sensitive",
                        home_team="Team A",
                        away_team="Team B",
                        kickoff_utc=naive(),
                        stage="group_stage",
                        status="scheduled",
                    ),
                    MatchPrediction(
                        match_id="m-sensitive",
                        predicted_home_score=1.0,
                        predicted_away_score=0.0,
                        home_win_prob=0.6,
                        draw_prob=0.25,
                        away_win_prob=0.15,
                        confidence=0.6,
                        prediction_method="elo_odds",
                    ),
                ])
                session.commit()
                prediction_db.close_prediction_session(session)

                with patch.object(settings, "API_WRITE_KEY", "secret"), patch.object(
                    world_cup_ai_optimization_service,
                    "has_configured_llm_route",
                    return_value=True,
                ), patch.object(
                    world_cup_ai_optimization_service,
                    "complete_json",
                    new=AsyncMock(side_effect=RuntimeError(sensitive)),
                ):
                    response = client.post(
                        "/world-cup/predictions/matches/m-sensitive/optimize",
                        headers=AUTH_HEADERS,
                    )

                prediction_db._engine.dispose()
                prediction_db._engine = None
                prediction_db._SessionLocal = None
        finally:
            client.close()
            world_cup_ai_optimization_service.logger.removeHandler(log_handler)

        exported = response.text + "\n" + "\n".join(rendered_logs)
        for fragment in (
            "SELECT secret", "C:/private", "fake-api-key", "fake-ticket",
            "fake-subprotocol", "upstream.example", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "AI optimization failed"})
        self.assertIn("RuntimeError", "\n".join(rendered_logs))

    def test_match_optimization_degraded_reason_is_safe_in_response_and_log(self):
        import tempfile

        from app.models.world_cup_prediction import Base, MatchFixture, MatchPrediction
        from app.services import world_cup_ai_optimization_service
        from app.services.llm_gateway_service import LLMResult
        from app.utils import prediction_db

        sensitive = (
            "SELECT secret FROM C:/private/optimization.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        rendered_logs = []

        class RenderedLogHandler(logging.Handler):
            def emit(self, record):
                rendered_logs.append(self.format(record))

        log_handler = RenderedLogHandler()
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        world_cup_ai_optimization_service.logger.addHandler(log_handler)
        client = _prediction_client()

        try:
            with tempfile.TemporaryDirectory() as tmp, patch.object(
                settings,
                "WORLD_CUP_PREDICTION_DB_FILE",
                str(Path(tmp) / "predictions.db"),
            ):
                prediction_db._engine = None
                prediction_db._SessionLocal = None
                Base.metadata.create_all(prediction_db.get_prediction_engine())
                session = prediction_db.get_prediction_session()
                session.add_all([
                    MatchFixture(
                        match_id="m-degraded",
                        fixture_id="m-degraded",
                        home_team="Team A",
                        away_team="Team B",
                        kickoff_utc=naive(),
                        stage="group_stage",
                        status="scheduled",
                    ),
                    MatchPrediction(
                        match_id="m-degraded",
                        predicted_home_score=1.0,
                        predicted_away_score=0.0,
                        home_win_prob=0.6,
                        draw_prob=0.25,
                        away_win_prob=0.15,
                        confidence=0.6,
                        prediction_method="elo_odds",
                    ),
                ])
                session.commit()
                prediction_db.close_prediction_session(session)

                with patch.object(settings, "API_WRITE_KEY", "secret"), patch.object(
                    world_cup_ai_optimization_service,
                    "has_configured_llm_route",
                    return_value=True,
                ), patch.object(
                    world_cup_ai_optimization_service,
                    "complete_json",
                    new=AsyncMock(return_value=LLMResult(
                        ok=False,
                        degraded_reason=sensitive,
                    )),
                ):
                    response = client.post(
                        "/world-cup/predictions/matches/m-degraded/optimize",
                        headers=AUTH_HEADERS,
                    )

                prediction_db._engine.dispose()
                prediction_db._engine = None
                prediction_db._SessionLocal = None
        finally:
            client.close()
            world_cup_ai_optimization_service.logger.removeHandler(log_handler)

        exported = response.text + "\n" + "\n".join(rendered_logs)
        for fragment in (
            "SELECT secret", "C:/private", "fake-api-key", "fake-ticket",
            "fake-subprotocol", "upstream.example", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "AI optimization failed"})
        self.assertIn("all_routes_failed", "\n".join(rendered_logs))

    def test_batch_optimization_nested_error_is_safe_in_success_response(self):
        import tempfile

        from app.models.world_cup_prediction import Base, MatchFixture, MatchPrediction
        from app.services import engine_auto_tuning_service
        from app.utils import prediction_db

        sensitive = (
            "SELECT secret FROM C:/private/optimization.db "
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "subprotocol=fake-subprotocol https://upstream.example/private"
        )
        client = _prediction_client()

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            settings,
            "WORLD_CUP_PREDICTION_DB_FILE",
            str(Path(tmp) / "predictions.db"),
        ):
            prediction_db._engine = None
            prediction_db._SessionLocal = None
            Base.metadata.create_all(prediction_db.get_prediction_engine())
            session = prediction_db.get_prediction_session()
            session.add_all([
                MatchFixture(
                    match_id="m-batch",
                    fixture_id="m-batch",
                    home_team="Team A",
                    away_team="Team B",
                    kickoff_utc=naive(),
                    stage="group_stage",
                    status="scheduled",
                ),
                MatchPrediction(
                    match_id="m-batch",
                    predicted_home_score=1.0,
                    predicted_away_score=0.0,
                    home_win_prob=0.6,
                    draw_prob=0.25,
                    away_win_prob=0.15,
                    confidence=0.6,
                    prediction_method="elo_odds",
                ),
            ])
            session.commit()
            prediction_db.close_prediction_session(session)

            with patch.object(settings, "API_WRITE_KEY", "secret"), patch.object(
                engine_auto_tuning_service,
                "optimize_prediction_with_ai",
                new=AsyncMock(side_effect=RuntimeError(sensitive)),
            ):
                response = client.post(
                    "/world-cup/predictions/batch-optimize?engine=elo_odds&limit=1",
                    headers=AUTH_HEADERS,
                )

            prediction_db._engine.dispose()
            prediction_db._engine = None
            prediction_db._SessionLocal = None

        exported = response.text
        for fragment in (
            "SELECT secret", "C:/private", "fake-api-key", "fake-ticket",
            "fake-subprotocol", "upstream.example", "Traceback",
        ):
            self.assertNotIn(fragment, exported)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_processed"], 0)
        self.assertEqual(response.json()["optimizations_generated"], 0)
        self.assertEqual(
            response.json()["errors"],
            [{"match_id": "m-batch", "error": "Optimization failed: RuntimeError"}],
        )

    def test_batch_switch_failure_result_does_not_expose_exception_text(self):
        client = _prediction_client()
        batch = AsyncMock(
            return_value={
                "status": "ok",
                "total": 1,
                "succeeded": 0,
                "failed": 1,
                "skipped": 0,
                "predictions": [
                    {
                        "status": "error",
                        "match_id": "m1",
                        "error": "D:/private/runtime.db?token=fake-secret",
                    }
                ],
            }
        )
        session = unittest.mock.MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [
            unittest.mock.MagicMock(match_id="m1")
        ]

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.world_cup_predictions.get_prediction_session",
                    return_value=session,
                ), \
                patch("app.api.routes.world_cup_predictions.close_prediction_session"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.batch_predict_matches",
                    batch,
                ):
            resp = client.post(
                "/world-cup/predictions/batch-switch-engine?engine=elo_odds",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["failed"], 1)
        self.assertEqual(
            resp.json()["predictions"][0],
            {
                "status": "error",
                "match_id": "m1",
                "error": "Prediction generation failed",
            },
        )
        self.assertNotIn("fake-secret", resp.text)
        self.assertNotIn("D:/private", resp.text)

    def test_batch_switch_exception_does_not_expose_exception_text(self):
        client = _prediction_client()
        session = unittest.mock.MagicMock()
        session.query.return_value.filter.return_value.all.side_effect = RuntimeError(
            "SELECT private_data FROM D:/private/runtime.db?token=fake-secret"
        )
        rendered_logs = []

        class RenderedLogHandler(logging.Handler):
            def emit(self, record):
                rendered_logs.append(self.format(record))

        log_handler = RenderedLogHandler()
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        world_cup_predictions.logger.addHandler(log_handler)

        try:
            with patch.object(settings, "API_WRITE_KEY", "secret"), \
                    patch(
                        "app.api.routes.world_cup_predictions.get_prediction_session",
                        return_value=session,
                    ), \
                    patch("app.api.routes.world_cup_predictions.close_prediction_session"):
                resp = client.post(
                    "/world-cup/predictions/batch-switch-engine?engine=elo_odds",
                    headers=AUTH_HEADERS,
                )
        finally:
            world_cup_predictions.logger.removeHandler(log_handler)

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"detail": "Batch engine switch failed"})
        exported = resp.text + "\n" + "\n".join(rendered_logs)
        self.assertNotIn("fake-secret", exported)
        self.assertNotIn("D:/private", exported)
        self.assertNotIn("SELECT private_data", exported)
        self.assertNotIn("Traceback", exported)
        self.assertIn("RuntimeError", "\n".join(rendered_logs))

    def test_batch_switch_stream_failure_does_not_expose_exception_text(self):
        client = _prediction_client()
        pipeline = AsyncMock(
            side_effect=RuntimeError(
                "Authorization: Bearer fake-authorization "
                "D:/private/runtime.db?ticket=fake-ticket SELECT private_data"
            )
        )
        session = unittest.mock.MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [
            unittest.mock.MagicMock(match_id="m1")
        ]
        rendered_logs = []

        class RenderedLogHandler(logging.Handler):
            def emit(self, record):
                rendered_logs.append(self.format(record))

        log_handler = RenderedLogHandler()
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        world_cup_predictions.logger.addHandler(log_handler)

        try:
            with patch.object(settings, "API_WRITE_KEY", "secret"), \
                    patch(
                        "app.api.routes.world_cup_predictions.get_prediction_session",
                        return_value=session,
                    ), \
                    patch("app.api.routes.world_cup_predictions.close_prediction_session"), \
                    patch(
                        "app.services.world_cup_prediction_pipeline.run_prediction_pipeline",
                        pipeline,
                    ):
                with client.stream(
                    "GET",
                    "/world-cup/predictions/batch-switch-engine-stream?engine=elo_odds",
                    headers=AUTH_HEADERS,
                ) as resp:
                    body = "".join(resp.iter_text())
        finally:
            world_cup_predictions.logger.removeHandler(log_handler)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: start", body)
        self.assertIn("event: progress", body)
        self.assertIn('"match_id": "m1"', body)
        self.assertIn('"status": "error"', body)
        self.assertIn('"error": "Prediction generation failed"', body)
        self.assertIn("event: complete", body)
        exported = body + "\n" + "\n".join(rendered_logs)
        self.assertNotIn("fake-authorization", exported)
        self.assertNotIn("fake-ticket", exported)
        self.assertNotIn("D:/private", exported)
        self.assertNotIn("SELECT private_data", exported)
        self.assertNotIn("Traceback", exported)
        self.assertIn("RuntimeError", "\n".join(rendered_logs))

    def test_batch_switch_stream_fatal_failure_does_not_expose_exception_text(self):
        client = _prediction_client()
        session = unittest.mock.MagicMock()
        session.query.return_value.filter.return_value.all.side_effect = RuntimeError(
            "https://upstream.example/private?api_key=fake-secret "
            "Authorization: Bearer fake-authorization"
        )
        rendered_logs = []

        class RenderedLogHandler(logging.Handler):
            def emit(self, record):
                rendered_logs.append(self.format(record))

        log_handler = RenderedLogHandler()
        log_handler.setFormatter(logging.Formatter("%(message)s"))
        world_cup_predictions.logger.addHandler(log_handler)

        try:
            with patch.object(settings, "API_WRITE_KEY", "secret"), \
                    patch(
                        "app.api.routes.world_cup_predictions.get_prediction_session",
                        return_value=session,
                    ), \
                    patch("app.api.routes.world_cup_predictions.close_prediction_session"):
                with client.stream(
                    "GET",
                    "/world-cup/predictions/batch-switch-engine-stream?engine=elo_odds",
                    headers=AUTH_HEADERS,
                ) as resp:
                    body = "".join(resp.iter_text())
        finally:
            world_cup_predictions.logger.removeHandler(log_handler)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            body,
            'event: error\ndata: {"message": "Batch engine switch failed"}\n\n',
        )
        exported = body + "\n" + "\n".join(rendered_logs)
        self.assertNotIn("fake-secret", exported)
        self.assertNotIn("fake-authorization", exported)
        self.assertNotIn("upstream.example", exported)
        self.assertNotIn("Traceback", exported)
        self.assertIn("RuntimeError", "\n".join(rendered_logs))

    def test_batch_switch_engine_rejects_unknown_engine(self):
        """Same boundary validation for the query-parameter batch routes."""
        client = _prediction_client()
        batch = AsyncMock(return_value={"status": "ok"})

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_prediction_pipeline.batch_predict_matches",
                    batch,
                ):
            resp = client.post(
                "/world-cup/predictions/batch-switch-engine?engine=not_an_engine",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 422)
        batch.assert_not_awaited()

    def test_history_entry_includes_current_prediction_metadata_when_snapshot_matches(self):
        factors = {
            "data_quality": "mock",
            "data_quality_notes": ["legacy_fixture"],
            "confidence_calibration": {
                "raw": 0.80,
                "calibrated": 0.65,
                "method": "piecewise_linear_reliability",
                "total_samples": 8,
            },
            "explanation_contributions": {
                "engine": "elo_odds",
                "items": [
                    {
                        "key": "elo",
                        "label": "Elo",
                        "unit": "pp",
                        "home_impact": 5,
                        "away_impact": -3,
                        "description": "rating edge",
                    }
                ],
            },
        }
        prediction = MatchPrediction(
            match_id="m1",
            predicted_home_score=2.0,
            predicted_away_score=1.0,
            home_win_prob=0.60,
            draw_prob=0.25,
            away_win_prob=0.15,
            confidence=0.65,
            prediction_method="elo_only",
            factors=factors,
        )
        history = PredictionHistory(
            match_id="m1",
            timestamp=naive(),
            predicted_home_score=2.0,
            predicted_away_score=1.0,
            home_win_prob=0.60,
            draw_prob=0.25,
            away_win_prob=0.15,
            confidence=0.65,
            trigger="manual",
            prediction_method="elo_only",
        )

        payload = _serialize_history_entry(history, prediction, _serialize_prediction(prediction))

        self.assertEqual(payload["engine_used"], "elo_odds")
        self.assertEqual(payload["raw_confidence"], 0.80)
        self.assertEqual(payload["confidence_calibration"]["calibrated"], 0.65)
        self.assertEqual(payload["explanation_contributions"]["items"][0]["label"], "Elo")
        self.assertEqual(payload["data_quality"], "partial")
        self.assertIn("legacy_fixture", payload["data_quality_notes"])
        self.assertIn("historical_non_real_quality_normalized", payload["data_quality_notes"])

    def test_history_entry_marks_unmatched_snapshots_as_missing_quality(self):
        history = PredictionHistory(
            match_id="m-old",
            timestamp=naive(),
            predicted_home_score=1.0,
            predicted_away_score=0.0,
            home_win_prob=0.50,
            draw_prob=0.30,
            away_win_prob=0.20,
            confidence=0.55,
            trigger="daily_update",
            prediction_method="hybrid",
        )

        payload = _serialize_history_entry(history)

        self.assertEqual(payload["data_quality"], "partial")
        self.assertIn("data_quality_missing", payload["data_quality_notes"])

    def test_history_entry_omits_current_prediction_metadata_when_snapshot_differs(self):
        prediction = MatchPrediction(
            match_id="m1",
            predicted_home_score=2.0,
            predicted_away_score=1.0,
            home_win_prob=0.60,
            draw_prob=0.25,
            away_win_prob=0.15,
            confidence=0.65,
            prediction_method="elo_only",
            factors={
                "confidence_calibration": {
                    "raw": 0.80,
                    "calibrated": 0.65,
                    "method": "piecewise_linear_reliability",
                },
            },
        )
        older_history = PredictionHistory(
            match_id="m1",
            timestamp=naive(),
            predicted_home_score=1.0,
            predicted_away_score=1.0,
            home_win_prob=0.40,
            draw_prob=0.35,
            away_win_prob=0.25,
            confidence=0.55,
            trigger="daily_update",
            prediction_method="elo_only",
        )

        payload = _serialize_history_entry(older_history, prediction, _serialize_prediction(prediction))

        self.assertNotIn("raw_confidence", payload)
        self.assertNotIn("confidence_calibration", payload)
        self.assertNotIn("explanation_contributions", payload)

    def test_gbm_prediction_serializes_engine_used_as_gbm(self):
        prediction = MatchPrediction(
            match_id="m-gbm",
            predicted_home_score=1.5,
            predicted_away_score=0.8,
            home_win_prob=0.58,
            draw_prob=0.24,
            away_win_prob=0.18,
            confidence=0.70,
            prediction_method="gbm_lightgbm",
        )
        history = PredictionHistory(
            match_id="m-gbm",
            timestamp=naive(),
            predicted_home_score=1.5,
            predicted_away_score=0.8,
            home_win_prob=0.58,
            draw_prob=0.24,
            away_win_prob=0.18,
            confidence=0.70,
            trigger="manual",
            prediction_method="gbm_lightgbm",
        )

        self.assertEqual(_serialize_prediction(prediction)["engine_used"], "gbm")
        self.assertEqual(_serialize_history_entry(history)["engine_used"], "gbm")

    def test_serialize_prediction_normalizes_historical_mock_quality(self):
        prediction = MatchPrediction(
            match_id="m-mock",
            predicted_home_score=1.8,
            predicted_away_score=1.1,
            home_win_prob=0.56,
            draw_prob=0.25,
            away_win_prob=0.19,
            confidence=0.62,
            prediction_method="hybrid",
            factors={
                "data_quality": "mock",
                "data_quality_notes": ["legacy_fixture"],
                "data_quality_metrics": {"quality": "mock"},
            },
        )

        payload = _serialize_prediction(prediction)

        self.assertEqual(payload["data_quality"], "partial")
        self.assertIn("legacy_fixture", payload["data_quality_notes"])
        self.assertIn("historical_non_real_quality_normalized", payload["data_quality_notes"])


class WorldCupPredictionWireShapeTests(unittest.TestCase):
    """Wire shapes of GET /matches/{match_id} and GET /today (audit 53.89).

    Both routes now bind named envelopes whose ``prediction`` key is written
    only when a prediction row exists, so the routes carry the
    ``response_model_exclude_unset`` flag. Driving the real response-model
    validation through TestClient means a key the envelope forbids fails
    loudly here, not only in the generated types -- and the flag's effect
    (no-prediction shapes grow no ``"prediction": null``) is pinned as
    behaviour, not as a decorator attribute.
    """

    _PREDICTION_KEYS = [
        "predicted_score", "outcome_probabilities", "confidence",
        "prediction_method", "engine_used", "ai_reasoning", "key_factors",
        "last_updated", "has_betting_odds", "data_quality",
        "data_quality_notes", "data_quality_score", "raw_confidence",
        "confidence_calibration", "high_confidence_selection",
        "explanation_contributions",
    ]

    def _fixture(self):
        return SimpleNamespace(
            match_id="m1", fixture_id="fx1", home_team="Alpha",
            away_team="Beta", kickoff_utc=naive(), venue="Stadium",
            stage="group", group="C", status="scheduled",
            home_score=None, away_score=None,
        )

    def _prediction(self):
        return MatchPrediction(
            match_id="m1",
            predicted_home_score=1.5,
            predicted_away_score=1.2,
            home_win_prob=0.4,
            draw_prob=0.3,
            away_win_prob=0.3,
            confidence=0.61,
            prediction_method="hybrid",
            ai_reasoning="rule + ai blend",
            key_factors=["elo gap"],
            last_updated=naive(),
            factors={},
        )

    def _client(self, fixture_q, pred_q):
        from app.models.world_cup_prediction import MatchFixture

        client = _prediction_client()
        session = MagicMock()
        session.query.side_effect = (
            lambda model: fixture_q if model is MatchFixture else pred_q
        )
        return client, session

    def _get(self, session, path):
        with patch(
            "app.api.routes.world_cup_predictions.get_prediction_session",
            return_value=session,
        ), patch("app.api.routes.world_cup_predictions.close_prediction_session"):
            return _prediction_client().get(path)

    def test_match_detail_with_prediction_serves_both_blocks_in_wire_order(self):
        fixture_q, pred_q = MagicMock(), MagicMock()
        fixture_q.filter_by.return_value.first.return_value = self._fixture()
        pred_q.filter_by.return_value.first.return_value = self._prediction()
        _, session = self._client(fixture_q, pred_q)

        resp = self._get(session, "/world-cup/predictions/matches/m1")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(list(body), ["status", "match", "prediction"])
        self.assertEqual(
            list(body["match"]),
            ["match_id", "fixture_id", "home_team", "away_team",
             "kickoff_utc", "venue", "stage", "group", "status"],
            "the detail match block is the nine-key MatchDetailRecord "
            "order -- home_score/away_score are deliberately absent "
            "here, which is why MatchDetailRecord does not share "
            "MatchFixtureRecord (53.83 criterion (3), audit 53.89)",
        )
        self.assertEqual(
            list(body["prediction"]),
            self._PREDICTION_KEYS,
            "the prediction block is the sixteen-key "
            "MatchPredictionPayload order (audit 53.89)",
        )

    def test_match_detail_without_prediction_grows_no_null_key(self):
        fixture_q, pred_q = MagicMock(), MagicMock()
        fixture_q.filter_by.return_value.first.return_value = self._fixture()
        pred_q.filter_by.return_value.first.return_value = None
        _, session = self._client(fixture_q, pred_q)

        resp = self._get(session, "/world-cup/predictions/matches/m1")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            list(resp.json()), ["status", "match"],
            "the no-prediction shape must not grow a \"prediction\": null "
            "-- the flag is load-bearing for MatchDetailResponse "
            "(audit 53.89)",
        )

    def test_today_envelope_and_element_serve_wire_order(self):
        fixture_q, pred_q = MagicMock(), MagicMock()
        fixture_q.filter.return_value.order_by.return_value.all.return_value = [
            self._fixture()
        ]
        pred_q.filter_by.return_value.first.return_value = self._prediction()
        _, session = self._client(fixture_q, pred_q)

        resp = self._get(session, "/world-cup/predictions/today")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(
            list(body), ["status", "date", "count", "matches"],
            "the today envelope is the four-key TodayMatchesResponse "
            "order (audit 53.89)",
        )
        self.assertEqual(body["count"], 1)
        element = body["matches"][0]
        self.assertEqual(list(element), ["match", "prediction"])
        self.assertEqual(
            list(element["match"]),
            ["match_id", "fixture_id", "home_team", "away_team",
             "kickoff_utc", "venue", "stage", "group", "status",
             "home_score", "away_score"],
            "the today match block is the eleven-key MatchFixtureRecord "
            "order -- the /matches element shape, reused not copied "
            "(audit 53.89)",
        )
        self.assertEqual(
            list(element["prediction"]), self._PREDICTION_KEYS,
            "today's prediction block is the same sixteen-key "
            "MatchPredictionPayload the other three routes serve "
            "(audit 53.89)",
        )

    def test_today_element_without_prediction_grows_no_null_key(self):
        fixture_q, pred_q = MagicMock(), MagicMock()
        fixture_q.filter.return_value.order_by.return_value.all.return_value = [
            self._fixture()
        ]
        pred_q.filter_by.return_value.first.return_value = None
        _, session = self._client(fixture_q, pred_q)

        resp = self._get(session, "/world-cup/predictions/today")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            list(resp.json()["matches"][0]), ["match"],
            "the no-prediction element must not grow a \"prediction\": "
            "null -- the flag is load-bearing for MatchListEntry here "
            "exactly as on /matches (audit 53.89)",
        )


if __name__ == "__main__":
    unittest.main()
