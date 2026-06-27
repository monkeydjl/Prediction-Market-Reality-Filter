import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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

    def test_history_entry_includes_current_prediction_metadata_when_snapshot_matches(self):
        factors = {
            "confidence_calibration": {
                "raw": 0.80,
                "calibrated": 0.65,
                "method": "bucketed_reliability_curve",
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
                    "method": "bucketed_reliability_curve",
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


if __name__ == "__main__":
    unittest.main()
