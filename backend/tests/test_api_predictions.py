# backend/tests/test_api_predictions.py
"""Tests for /api/predictions API routes and DB query functions."""
import pytest

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    get_latest_prediction, get_match_ids_with_predictions,
    KernelPrediction,
)
from datetime import datetime, timezone


@pytest.fixture
def db(tmp_path):
    """Initialize a temporary kernel DB for each test."""
    db_path = str(tmp_path / "test_api_predictions.db")
    init_kernel_db(db_path)
    yield
    close_kernel_session()


def _insert_prediction(match_id: str, engine: str = "elo_odds"):
    """Insert a prediction row for testing."""
    session = get_kernel_session()
    pred = KernelPrediction(
        match_id=match_id,
        sport="football",
        competition="world_cup",
        season="2026",
        engine=engine,
        predicted_scores={"home": 2, "away": 1},
        outcome_probabilities={"home_win": 0.6, "draw": 0.2, "away_win": 0.2},
        confidence=0.72,
        feature_version="1.0",
        explanation=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(pred)
    session.commit()
    return pred


class TestGetLatestPrediction:
    def test_get_latest_prediction_returns_row(self, db):
        """Table has data → returns the row."""
        _insert_prediction("wc-123")
        result = get_latest_prediction("wc-123")
        assert result is not None
        assert result.match_id == "wc-123"
        assert result.engine == "elo_odds"

    def test_get_latest_prediction_returns_none(self, db):
        """Table empty for this match_id → returns None."""
        result = get_latest_prediction("wc-nonexistent")
        assert result is None


class TestGetMatchIdsWithPredictions:
    def test_returns_subset_with_predictions(self, db):
        """3 matches, 2 have predictions → returns 2."""
        _insert_prediction("wc-1")
        _insert_prediction("nba-2")
        # wc-3 has no prediction
        result = get_match_ids_with_predictions(["wc-1", "nba-2", "wc-3"])
        assert result == {"wc-1", "nba-2"}

    def test_empty_input_returns_empty_set(self, db):
        """Empty list → empty set."""
        result = get_match_ids_with_predictions([])
        assert result == set()

    def test_no_predictions_returns_empty_set(self, db):
        """Matches exist but no predictions → empty set."""
        result = get_match_ids_with_predictions(["wc-1", "wc-2"])
        assert result == set()


from unittest.mock import patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)


def _make_raw_match(match_id="wc-test", sport_code="football", comp_code="world_cup",
                    kickoff=None) -> RawMatchData:
    """Create a RawMatchData for testing."""
    sport = SportIdentity(code=sport_code, name=sport_code.capitalize())
    comp = CompetitionIdentity(code=comp_code, name=comp_code, sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="HOM", name="Home Team", competition=comp)
    away = TeamIdentity(code="AWY", name="Away Team", competition=comp)
    if kickoff is None:
        kickoff = datetime.now(timezone.utc)
    match = MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away, kickoff_utc=kickoff,
    )
    return RawMatchData(match=match, raw_json={})


class MultiSportFakeAdapter:
    """Fake adapter that returns matches across multiple sports."""

    def __init__(self):
        self._matches = [_make_raw_match("wc-1", "football", "world_cup"),
                         _make_raw_match("nba-1", "basketball", "nba"),
                         _make_raw_match("mlb-1", "baseball", "mlb"),
                         _make_raw_match("nhl-1", "hockey", "nhl")]

    def fetch_schedule(self, filters):
        return list(self._matches)

    def get_match_identity(self, match_id):
        for m in self._matches:
            if m.match.match_id == match_id:
                return m.match
        return None

    def fetch_all_data(self, match):
        return {"team": {}, "market": {}, "player": {}, "environment": {}, "general": {}}

    def fetch_team_data(self, team): return {}
    def fetch_player_data(self, team): return {}
    def fetch_market_data(self, match): return {}
    def fetch_outcome(self, match_id): return None
    def sync_schedule(self): return 0


@pytest.fixture
def api_client(tmp_path):
    """TestClient with Kernel enabled and FakeAdapter."""
    from app.main import app
    from app.core import config
    from app.api.security import settings as security_settings
    from app.api.routes import predictions
    from app.kernel.kernel_db import init_kernel_db, close_kernel_session

    init_kernel_db(str(tmp_path / "test_api.db"))
    # Clear cached kernel
    if hasattr(predictions._get_kernel, "_instance"):
        delattr(predictions._get_kernel, "_instance")

    with patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
         patch.object(security_settings, "API_WRITE_KEY", ""), \
         patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        yield TestClient(app)

    if hasattr(predictions._get_kernel, "_instance"):
        delattr(predictions._get_kernel, "_instance")
    close_kernel_session()


def _patch_kernel_adapter(api_client, adapter=None):
    """Patch the kernel's adapter with a FakeAdapter."""
    from app.api.routes import predictions
    if adapter is None:
        adapter = MultiSportFakeAdapter()
    kernel = predictions._get_kernel()
    kernel._adapter = adapter
    return adapter


class TestListMatches:
    def test_list_matches_returns_today_matches(self, api_client, tmp_path):
        """Returns today's matches from the adapter."""
        _patch_kernel_adapter(api_client)
        resp = api_client.get("/api/predictions/matches")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4  # football + basketball + baseball + hockey

    def test_list_matches_sport_filter(self, api_client):
        """Sport filter works."""
        _patch_kernel_adapter(api_client)
        resp = api_client.get("/api/predictions/matches?sport=basketball")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sport"] == "basketball"

    def test_list_matches_empty_when_no_fixtures(self, api_client):
        """Empty adapter → empty list."""
        from app.kernel.protocols import ScheduleFilter
        empty_adapter = MultiSportFakeAdapter()
        empty_adapter._matches = []
        _patch_kernel_adapter(api_client, empty_adapter)
        resp = api_client.get("/api/predictions/matches")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_matches_summary_format(self, api_client):
        """Summary fields are complete."""
        _patch_kernel_adapter(api_client)
        resp = api_client.get("/api/predictions/matches?sport=football")
        assert resp.status_code == 200
        item = resp.json()[0]
        expected_keys = {"match_id", "sport", "competition", "home_team", "away_team",
                         "home_code", "away_code", "kickoff_utc", "stage", "has_prediction"}
        assert set(item.keys()) == expected_keys
        assert item["match_id"] == "wc-1"
        assert item["has_prediction"] is False

    def test_list_matches_503_when_kernel_disabled(self, tmp_path):
        """KERNEL_PREDICTION_ENABLED=false → 503."""
        from app.main import app
        from app.core import config
        from app.api.security import settings as security_settings
        with patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", False), \
             patch.object(security_settings, "API_WRITE_KEY", ""), \
             patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            client = TestClient(app)
            resp = client.get("/api/predictions/matches")
            assert resp.status_code == 503


class TestGetMatch:
    def test_get_match_returns_detail_and_prediction(self, api_client, tmp_path):
        """Returns detail + existing prediction."""
        _patch_kernel_adapter(api_client)
        # Insert a prediction for wc-1
        _insert_prediction("wc-1")
        resp = api_client.get("/api/predictions/matches/wc-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["match"]["match_id"] == "wc-1"
        assert data["prediction"] is not None
        assert data["prediction"]["engine"] == "elo_odds"

    def test_get_match_returns_null_prediction_when_none(self, api_client):
        """prediction=null when no prediction exists."""
        _patch_kernel_adapter(api_client)
        resp = api_client.get("/api/predictions/matches/nba-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["match"]["match_id"] == "nba-1"
        assert data["prediction"] is None

    def test_get_match_404_when_not_found(self, api_client):
        """match_id not found → 404."""
        _patch_kernel_adapter(api_client)
        resp = api_client.get("/api/predictions/matches/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Match not found"

    def test_get_match_detail_format(self, api_client):
        """Detail fields are complete."""
        _patch_kernel_adapter(api_client)
        resp = api_client.get("/api/predictions/matches/mlb-1")
        assert resp.status_code == 200
        match = resp.json()["match"]
        expected_keys = {"match_id", "sport", "competition", "season_key",
                         "home_team", "away_team", "home_code", "away_code",
                         "kickoff_utc", "stage", "round"}
        assert set(match.keys()) == expected_keys
        assert match["sport"] == "baseball"


class TestPredictEndpointFix:
    def test_predict_returns_feature_version(self, api_client):
        """Fix: predict response includes feature_version."""
        from app.api.routes import predictions
        from app.kernel.domain import (
            PredictionResult, ContributionItem,
        )
        _patch_kernel_adapter(api_client)

        # Mock kernel.predict to return a controlled result
        fake_result = PredictionResult(
            predicted_scores={"home": 2, "away": 1},
            outcome_probabilities={"home_win": 0.6, "draw": 0.2, "away_win": 0.2},
            confidence=0.72,
            engine_name="elo_odds",
            explanation=[],
            betting_analysis=None,
            feature_version="1.0",
            prediction_timestamp=datetime.now(timezone.utc),
        )
        kernel = predictions._get_kernel()
        with patch.object(kernel, "predict", return_value=fake_result):
            resp = api_client.post("/api/predictions/matches/wc-1/predict")
        assert resp.status_code == 200
        data = resp.json()
        assert "feature_version" in data
        assert data["feature_version"] == "1.0"

    def test_predict_returns_prediction_timestamp(self, api_client):
        """Fix: predict response includes prediction_timestamp."""
        from app.api.routes import predictions
        from app.kernel.domain import PredictionResult
        _patch_kernel_adapter(api_client)

        ts = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        fake_result = PredictionResult(
            predicted_scores={"home": 2, "away": 1},
            outcome_probabilities={"home_win": 0.6, "draw": 0.2, "away_win": 0.2},
            confidence=0.72,
            engine_name="elo_odds",
            explanation=[],
            betting_analysis=None,
            feature_version="1.0",
            prediction_timestamp=ts,
        )
        kernel = predictions._get_kernel()
        with patch.object(kernel, "predict", return_value=fake_result):
            resp = api_client.post("/api/predictions/matches/wc-1/predict")
        assert resp.status_code == 200
        data = resp.json()
        assert "prediction_timestamp" in data
        assert data["prediction_timestamp"] is not None
