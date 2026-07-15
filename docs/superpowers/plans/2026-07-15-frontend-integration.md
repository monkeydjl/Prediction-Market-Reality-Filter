# Frontend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified multi-sport frontend (`/sports/` route tree) with 2 new backend API endpoints, exposing the Kernel's cross-sport prediction capabilities through generic, data-driven components.

**Architecture:** New `/sports/` Next.js route tree + 5 generic React components + 1 API client module. Backend extends existing `/api/predictions/*` router with `GET /matches` and `GET /matches/{id}` endpoints. Old `/world-cup/` pages and components remain untouched.

**Tech Stack:** Python/FastAPI (backend), Next.js 16 + React 19 + TypeScript + Tailwind CSS 4 + Vitest 4 (frontend)

## Global Constraints

1. `KERNEL_PREDICTION_ENABLED` defaults to OFF — new `/matches` endpoints return 503 when disabled
2. `GET /api/predictions/matches` must obtain data via `kernel._adapter.fetch_schedule()` — never query DB tables directly
3. `GET /api/predictions/matches/{match_id}` 404 response must return `{"detail": "Match not found"}`
4. `get_latest_prediction()` and `get_match_ids_with_predictions()` must be placed in `kernel_db.py`
5. `POST /predict` endpoint fix must add `feature_version` and `prediction_timestamp` fields
6. `_prediction_to_dict()` helper must handle both `PredictionResult` (dataclass) and `KernelPrediction` (ORM) types
7. `MultiAdapter`, `PredictionKernel`, `domain.py`, all Adapters — zero modification
8. Existing `/api/world-cup/predictions/*` routes — zero modification
9. `frontend/src/app/world-cup/` directory — zero modification
10. `frontend/src/components/world-cup/` directory — zero modification
11. `frontend/src/lib/world-cup-predictions.ts` — zero modification
12. New components must be placed under `frontend/src/components/sports/`
13. `ProbabilityBar` must not hardcode outcome count — must render dynamically via `Object.keys(probabilities)`
14. `FactorBreakdownTable` must not hardcode factor names or count — must iterate `items` array dynamically
15. `sports-api.ts` must reuse existing `getWorldCupApiBase()` function from `lib/env.ts` — do not re-implement API base URL logic
16. Navigation `app-nav.tsx` only adds `/sports` entry, does not modify or delete existing entries
17. Sport icons use emoji (⚽🏀⚾🏒) — no icon library dependency introduced
18. TypeScript type definitions placed at top of `lib/sports-api.ts`, components import from this file
19. Backend tests use FakeAdapter, do not mock real Adapters
20. Frontend tests do not test Next.js routing or styles — focus on component logic
21. All existing tests must remain passing

**Spec correction notes (applied in this plan):**
- Spec says `getApiBaseUrl()` — actual function in `lib/env.ts` is `getWorldCupApiBase()` (returns base WITHOUT `/api` suffix; fetch paths include `/api/`)
- Spec says test files in `__tests__/` subdirectories — actual project convention is colocation (e.g., `foo.test.tsx` next to `foo.tsx`). Plan follows colocation.
- Spec says "15 backend tests" — actual count is 16 (5+4+2+2+3). Spec says "21 frontend tests" — actual count is 23 (6+6+6+5). Total 39 is correct.

---

## File Structure

### Backend (2 files modified, 1 file new)

| File | Responsibility |
|------|----------------|
| `backend/app/kernel/kernel_db.py` | Add `get_latest_prediction()` + `get_match_ids_with_predictions()` DB query functions |
| `backend/app/api/routes/predictions.py` | Add 2 endpoints (`GET /matches`, `GET /matches/{id}`) + 3 helpers + fix predict return |
| `backend/tests/test_api_predictions.py` | 16 backend tests for new endpoints and DB functions |

### Frontend (10 files new, 1 file modified, 4 test files new)

| File | Responsibility |
|------|----------------|
| `frontend/src/lib/sports-api.ts` | TypeScript types + 3 API client functions |
| `frontend/src/components/sports/probability-bar.tsx` | Dynamic binary/ternary probability bar |
| `frontend/src/components/sports/factor-breakdown-table.tsx` | Dynamic factor breakdown table |
| `frontend/src/components/sports/match-list-card.tsx` | List card with sport icon + status badge |
| `frontend/src/components/sports/sport-filter.tsx` | Sport filter button group |
| `frontend/src/components/sports/match-detail-panel.tsx` | Detail panel composing above components |
| `frontend/src/app/sports/page.tsx` | Today's matches list page |
| `frontend/src/app/sports/loading.tsx` | List page skeleton |
| `frontend/src/app/sports/[matchId]/page.tsx` | Single match detail page |
| `frontend/src/app/sports/[matchId]/loading.tsx` | Detail page skeleton |
| `frontend/src/components/app-nav.tsx` | Add `/sports` nav entry (modified) |
| `frontend/src/lib/sports-api.test.ts` | 5 tests for API client |
| `frontend/src/components/sports/probability-bar.test.tsx` | 6 tests for ProbabilityBar |
| `frontend/src/components/sports/factor-breakdown-table.test.tsx` | 6 tests for FactorBreakdownTable |
| `frontend/src/components/sports/match-list-card.test.tsx` | 6 tests for MatchListCard |

---

### Task 1: Backend DB Functions

**Files:**
- Modify: `backend/app/kernel/kernel_db.py` (append 2 functions after `close_kernel_session`)
- Test: `backend/tests/test_api_predictions.py` (new file, `TestGetLatestPrediction` + `TestGetMatchIdsWithPredictions` classes only)

**Interfaces:**
- Consumes: `KernelPrediction` model (already defined in `kernel_db.py`), `get_kernel_session()` (already defined)
- Produces: `get_latest_prediction(match_id: str) -> KernelPrediction | None`, `get_match_ids_with_predictions(match_ids: list[str]) -> set[str]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_api_predictions.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_api_predictions.py::TestGetLatestPrediction tests/test_api_predictions.py::TestGetMatchIdsWithPredictions -v`
Expected: FAIL with `ImportError: cannot import name 'get_latest_prediction'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/kernel/kernel_db.py` (after `close_kernel_session` function, at end of file):

```python
def get_latest_prediction(match_id: str) -> KernelPrediction | None:
    """Get the latest prediction for a match from the kernel_predictions table.

    The table uses match_id as primary key, so each match has at most one row
    (updated on each prediction).
    """
    session = get_kernel_session()
    try:
        return session.query(KernelPrediction).filter_by(match_id=match_id).one_or_none()
    except Exception:
        return None


def get_match_ids_with_predictions(match_ids: list[str]) -> set[str]:
    """Batch query: return the subset of match_ids that have a prediction row.

    Used by the list endpoint to populate has_prediction without N+1 queries.
    """
    if not match_ids:
        return set()
    session = get_kernel_session()
    try:
        rows = session.query(KernelPrediction.match_id).filter(
            KernelPrediction.match_id.in_(match_ids)
        ).all()
        return {row[0] for row in rows}
    except Exception:
        return set()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_api_predictions.py::TestGetLatestPrediction tests/test_api_predictions.py::TestGetMatchIdsWithPredictions -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/kernel/kernel_db.py tests/test_api_predictions.py
git commit -m "feat: add get_latest_prediction and get_match_ids_with_predictions DB functions"
```

---

### Task 2: Backend API Endpoints

**Files:**
- Modify: `backend/app/api/routes/predictions.py` (add 2 endpoints + 3 helpers + fix predict return)
- Modify: `backend/tests/test_api_predictions.py` (append `TestListMatches` + `TestGetMatch` + `TestPredictEndpointFix` classes)

**Interfaces:**
- Consumes: `get_latest_prediction()`, `get_match_ids_with_predictions()` from Task 1; `ScheduleFilter`, `RawMatchData` from `protocols.py`; `MatchIdentity` from `domain.py`
- Produces: `GET /api/predictions/matches` (list endpoint), `GET /api/predictions/matches/{match_id}` (detail endpoint), fixed `POST /api/predictions/matches/{match_id}/predict` return

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api_predictions.py` (after `TestGetMatchIdsWithPredictions`):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_api_predictions.py::TestListMatches tests/test_api_predictions.py::TestGetMatch tests/test_api_predictions.py::TestPredictEndpointFix -v`
Expected: FAIL with 404 or missing fields

- [ ] **Step 3: Implement the helpers and endpoints**

In `backend/app/api/routes/predictions.py`:

**3a. Fix the predict endpoint return value** — find the `predict_match` function and replace its return dict:

Replace:
```python
        return {
            "match_id": match_id,
            "engine": result.engine_name,
            "predicted_scores": result.predicted_scores,
            "outcome_probabilities": result.outcome_probabilities,
            "confidence": result.confidence,
            "explanation": [c.__dict__ for c in result.explanation],
        }
```

With:
```python
        return {
            "match_id": match_id,
            "engine": result.engine_name,
            "predicted_scores": result.predicted_scores,
            "outcome_probabilities": result.outcome_probabilities,
            "confidence": result.confidence,
            "explanation": [c.__dict__ for c in result.explanation],
            "feature_version": result.feature_version,
            "prediction_timestamp": result.prediction_timestamp.isoformat(),
        }
```

**3b. Add the 3 helper functions** — append before the last line of the file (after `engine_score` endpoint):

```python
def _match_summary(raw, predicted_ids: set[str]) -> dict:
    """Compact match summary for list endpoint."""
    m = raw.match
    return {
        "match_id": m.match_id,
        "sport": m.season.competition.sport.code,
        "competition": m.season.competition.code,
        "home_team": m.home.name,
        "away_team": m.away.name,
        "home_code": m.home.code,
        "away_code": m.away.code,
        "kickoff_utc": m.kickoff_utc.isoformat() if m.kickoff_utc else None,
        "stage": m.stage,
        "has_prediction": m.match_id in predicted_ids,
    }


def _match_detail(match) -> dict:
    """Full match detail for detail endpoint."""
    return {
        "match_id": match.match_id,
        "sport": match.season.competition.sport.code,
        "competition": match.season.competition.code,
        "season_key": match.season.season_key,
        "home_team": match.home.name,
        "away_team": match.away.name,
        "home_code": match.home.code,
        "away_code": match.away.code,
        "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
        "stage": match.stage,
        "round": match.round,
    }


def _prediction_to_dict(pred) -> dict:
    """Convert PredictionResult (dataclass) or KernelPrediction (ORM) to dict."""
    import json
    from datetime import datetime

    explanation = pred.explanation
    if isinstance(explanation, str):
        explanation = json.loads(explanation)

    timestamp = pred.prediction_timestamp if hasattr(pred, "prediction_timestamp") else pred.created_at
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()

    return {
        "engine": pred.engine,
        "predicted_scores": pred.predicted_scores,
        "outcome_probabilities": pred.outcome_probabilities,
        "confidence": pred.confidence,
        "explanation": explanation,
        "feature_version": pred.feature_version,
        "prediction_timestamp": timestamp,
    }
```

**3c. Add the 2 new endpoints** — append after the `engine_score` endpoint and before the helpers:

```python
@router.get("/matches")
def list_matches(sport: str | None = None):
    """List today's matches across all sports."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    kernel = _get_kernel()
    from app.kernel.protocols import ScheduleFilter
    raw_matches = kernel._adapter.fetch_schedule(ScheduleFilter())

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    today_matches = []
    for m in raw_matches:
        kickoff = m.match.kickoff_utc
        if kickoff is not None and kickoff.date() == today:
            today_matches.append(m)

    if sport:
        today_matches = [m for m in today_matches
                         if m.match.season.competition.sport.code == sport]

    from app.kernel.kernel_db import get_match_ids_with_predictions
    predicted_ids = get_match_ids_with_predictions([m.match.match_id for m in today_matches])

    return [_match_summary(m, predicted_ids) for m in today_matches]


@router.get("/matches/{match_id}")
def get_match(match_id: str):
    """Get match detail and latest prediction (if any)."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    kernel = _get_kernel()
    match = kernel._adapter.get_match_identity(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    from app.kernel.kernel_db import get_latest_prediction
    latest = get_latest_prediction(match_id)

    return {
        "match": _match_detail(match),
        "prediction": _prediction_to_dict(latest) if latest else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_api_predictions.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Run regression tests**

Run: `cd backend && python -m pytest tests/test_predictions_route.py tests/test_kernel_prediction_kernel.py -v`
Expected: PASS (all existing tests)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/api/routes/predictions.py tests/test_api_predictions.py
git commit -m "feat: add GET /matches and GET /matches/{id} endpoints + fix predict return"
```

---

### Task 3: Frontend API Client

**Files:**
- Create: `frontend/src/lib/sports-api.ts`
- Test: `frontend/src/lib/sports-api.test.ts`

**Interfaces:**
- Consumes: `getWorldCupApiBase()` from `lib/env.ts`
- Produces: `MatchSummary`, `MatchDetail`, `ContributionItem`, `PredictionResult` types; `fetchMatches()`, `fetchMatchDetail()`, `triggerPrediction()` functions; `NotFoundError` class

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/sports-api.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchMatches, fetchMatchDetail, triggerPrediction, NotFoundError } from "./sports-api";

// Mock env module
vi.mock("./env", () => ({
  getWorldCupApiBase: () => "http://localhost:8000",
}));

// Mock global fetch
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

describe("fetchMatches", () => {
  it("calls correct URL without sport param", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    await fetchMatches();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/matches");
  });

  it("calls correct URL with sport param", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    await fetchMatches("basketball");
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/matches?sport=basketball");
  });

  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchMatches()).rejects.toThrow("Failed to fetch matches");
  });
});

describe("fetchMatchDetail", () => {
  it("throws NotFoundError on 404", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(fetchMatchDetail("wc-1")).rejects.toThrow(NotFoundError);
  });
});

describe("triggerPrediction", () => {
  it("uses POST method", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ engine: "elo_odds" }),
    });
    await triggerPrediction("wc-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/matches/wc-1/predict",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/sports-api.test.ts`
Expected: FAIL with "Cannot find module './sports-api'"

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/sports-api.ts`:

```typescript
import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

// Type definitions (single source of truth)

export interface MatchSummary {
  match_id: string;
  sport: string;
  competition: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  has_prediction: boolean;
}

export interface MatchDetail {
  match_id: string;
  sport: string;
  competition: string;
  season_key: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  round: string | null;
}

export interface ContributionItem {
  factor: string;
  direction: string;
  weight: number;
  available: boolean;
  detail: string | null;
  predicted_outcome: string | null;
}

export interface PredictionResult {
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  explanation: ContributionItem[];
  feature_version: string;
  prediction_timestamp: string | null;
}

export class NotFoundError extends Error {}

export async function fetchMatches(sport?: string): Promise<MatchSummary[]> {
  const params = sport ? `?sport=${sport}` : "";
  const res = await fetch(`${API_BASE}/api/predictions/matches${params}`);
  if (!res.ok) throw new Error("Failed to fetch matches");
  return res.json();
}

export async function fetchMatchDetail(
  matchId: string,
): Promise<{ match: MatchDetail; prediction: PredictionResult | null }> {
  const res = await fetch(`${API_BASE}/api/predictions/matches/${matchId}`);
  if (res.status === 404) throw new NotFoundError("Match not found");
  if (!res.ok) throw new Error("Failed to fetch match");
  return res.json();
}

export async function triggerPrediction(matchId: string): Promise<PredictionResult> {
  const res = await fetch(`${API_BASE}/api/predictions/matches/${matchId}/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error("Prediction failed");
  return res.json();
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/sports-api.test.ts`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd frontend
git add src/lib/sports-api.ts src/lib/sports-api.test.ts
git commit -m "feat: add sports-api.ts client with types and fetch functions"
```

---

### Task 4: Frontend ProbabilityBar Component

**Files:**
- Create: `frontend/src/components/sports/probability-bar.tsx`
- Test: `frontend/src/components/sports/probability-bar.test.tsx`

**Interfaces:**
- Consumes: `PredictionResult.outcome_probabilities` type from `lib/sports-api`
- Produces: `ProbabilityBar` React component

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/sports/probability-bar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProbabilityBar } from "./probability-bar";

describe("ProbabilityBar", () => {
  it("renders two outcomes for binary", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    const bars = screen.getAllByRole("img", { name: /概率/ });
    expect(bars).toHaveLength(2);
  });

  it("renders three outcomes for ternary", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.5, draw: 0.25, away_win: 0.25 }}
        homeTeam="Brazil"
        awayTeam="Argentina"
      />,
    );
    const bars = screen.getAllByRole("img", { name: /概率/ });
    expect(bars).toHaveLength(3);
  });

  it("renders correct percentages", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    expect(screen.getByText("62.0%")).toBeDefined();
    expect(screen.getByText("38.0%")).toBeDefined();
  });

  it("applies home color to home_win", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    const homeBar = screen.getByRole("img", { name: /Lakers.*概率/ });
    expect(homeBar.className).toContain("bg-blue");
  });

  it("applies away color to away_win", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    const awayBar = screen.getByRole("img", { name: /Celtics.*概率/ });
    expect(awayBar.className).toContain("bg-red");
  });

  it("applies neutral color to draw", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.5, draw: 0.25, away_win: 0.25 }}
        homeTeam="Brazil"
        awayTeam="Argentina"
      />,
    );
    const drawBar = screen.getByRole("img", { name: /平局.*概率/ });
    expect(drawBar.className).toContain("bg-gray");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/probability-bar.test.tsx`
Expected: FAIL with "Cannot find module './probability-bar'"

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/sports/probability-bar.tsx`:

```tsx
interface ProbabilityBarProps {
  probabilities: Record<string, number>;
  homeTeam: string;
  awayTeam: string;
}

function getOutcomeStyle(key: string): { color: string; label: (home: string, away: string) => string } {
  if (key === "home_win" || key === "home") {
    return { color: "bg-blue-500", label: (home) => home };
  }
  if (key === "away_win" || key === "away") {
    return { color: "bg-red-500", label: (_home, away) => away };
  }
  // draw or any other key
  return { color: "bg-gray-400", label: () => "平局" };
}

export function ProbabilityBar({ probabilities, homeTeam, awayTeam }: ProbabilityBarProps) {
  const entries = Object.entries(probabilities);

  return (
    <div className="space-y-2">
      {entries.map(([key, prob]) => {
        const style = getOutcomeStyle(key);
        const label = style.label(homeTeam, awayTeam);
        const pct = (prob * 100).toFixed(1);
        return (
          <div key={key} className="space-y-1">
            <div className="flex justify-between text-sm">
              <span>{label}</span>
              <span className="font-mono">{pct}%</span>
            </div>
            <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
              <div
                role="img"
                aria-label={`${label} 概率 ${pct}%`}
                className={`h-full rounded-full ${style.color}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/probability-bar.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd frontend
git add src/components/sports/probability-bar.tsx src/components/sports/probability-bar.test.tsx
git commit -m "feat: add ProbabilityBar component with dynamic binary/ternary rendering"
```

---

### Task 5: Frontend FactorBreakdownTable Component

**Files:**
- Create: `frontend/src/components/sports/factor-breakdown-table.tsx`
- Test: `frontend/src/components/sports/factor-breakdown-table.test.tsx`

**Interfaces:**
- Consumes: `ContributionItem` type from `lib/sports-api`
- Produces: `FactorBreakdownTable` React component

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/sports/factor-breakdown-table.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FactorBreakdownTable } from "./factor-breakdown-table";
import type { ContributionItem } from "@/lib/sports-api";

function makeItem(overrides: Partial<ContributionItem> = {}): ContributionItem {
  return {
    factor: "elo",
    direction: "support",
    weight: 0.3,
    available: true,
    detail: "P(home_win)=0.65",
    predicted_outcome: "home_win",
    ...overrides,
  };
}

describe("FactorBreakdownTable", () => {
  it("renders all items", () => {
    const items = [
      makeItem({ factor: "elo" }),
      makeItem({ factor: "home_court" }),
      makeItem({ factor: "rest" }),
      makeItem({ factor: "form" }),
      makeItem({ factor: "starting_pitcher" }),
    ];
    render(<FactorBreakdownTable items={items} />);
    expect(screen.getAllByRole("row")).toHaveLength(6); // 1 header + 5 data
  });

  it("displays factor name zh mapping", () => {
    render(<FactorBreakdownTable items={[makeItem({ factor: "elo" })]} />);
    expect(screen.getByText("Elo 等级分")).toBeDefined();
  });

  it("displays unmapped factor as-is", () => {
    render(<FactorBreakdownTable items={[makeItem({ factor: "unknown_factor" })]} />);
    expect(screen.getByText("unknown_factor")).toBeDefined();
  });

  it("unavailable factor row is greyed", () => {
    render(<FactorBreakdownTable items={[makeItem({ available: false })]} />);
    const row = screen.getAllByRole("row")[1];
    expect(row.className).toContain("opacity");
  });

  it("direction is translated", () => {
    render(<FactorBreakdownTable items={[makeItem({ direction: "support" })]} />);
    expect(screen.getByText(/支持/)).toBeDefined();
  });

  it("predicted_outcome shown in brackets", () => {
    render(<FactorBreakdownTable items={[makeItem({ predicted_outcome: "home_win" })]} />);
    expect(screen.getByText(/主胜/)).toBeDefined();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/factor-breakdown-table.test.tsx`
Expected: FAIL with "Cannot find module './factor-breakdown-table'"

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/sports/factor-breakdown-table.tsx`:

```tsx
import type { ContributionItem } from "@/lib/sports-api";

const FACTOR_NAME_ZH: Record<string, string> = {
  elo: "Elo 等级分",
  home_court: "主场优势",
  rest: "休息天数",
  form: "近期状态",
  starting_pitcher: "先发投手",
  goalie: "门将",
  odds: "赔率",
};

const DIRECTION_ZH: Record<string, string> = {
  support: "支持",
  oppose: "反对",
  neutral: "中立",
};

const OUTCOME_ZH: Record<string, string> = {
  home_win: "主胜",
  away_win: "客胜",
  draw: "平局",
};

interface FactorBreakdownTableProps {
  items: ContributionItem[];
}

export function FactorBreakdownTable({ items }: FactorBreakdownTableProps) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b">
          <th className="py-2 text-left">因子</th>
          <th className="py-2 text-left">方向</th>
          <th className="py-2 text-right">权重</th>
          <th className="py-2 text-left">详情</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item, idx) => {
          const factorName = FACTOR_NAME_ZH[item.factor] ?? item.factor;
          const direction = DIRECTION_ZH[item.direction] ?? item.direction;
          const outcome = item.predicted_outcome ? ` (${OUTCOME_ZH[item.predicted_outcome] ?? item.predicted_outcome})` : "";
          const weight = `${(item.weight * 100).toFixed(0)}%`;
          const detail = item.available ? (item.detail ?? "") : "不可用";
          return (
            <tr
              key={idx}
              className={`border-b ${item.available ? "" : "opacity-40"}`}
            >
              <td className="py-2">{factorName}</td>
              <td className="py-2">{direction}{outcome}</td>
              <td className="py-2 text-right font-mono">{weight}</td>
              <td className="py-2 text-muted-foreground">{detail}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/factor-breakdown-table.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd frontend
git add src/components/sports/factor-breakdown-table.tsx src/components/sports/factor-breakdown-table.test.tsx
git commit -m "feat: add FactorBreakdownTable component with dynamic factor rendering"
```

---

### Task 6: Frontend MatchListCard Component

**Files:**
- Create: `frontend/src/components/sports/match-list-card.tsx`
- Test: `frontend/src/components/sports/match-list-card.test.tsx`

**Interfaces:**
- Consumes: `MatchSummary` type from `lib/sports-api`
- Produces: `MatchListCard` React component

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/sports/match-list-card.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MatchListCard } from "./match-list-card";
import type { MatchSummary } from "@/lib/sports-api";

const mockMatch: MatchSummary = {
  match_id: "nba-12345",
  sport: "basketball",
  competition: "nba",
  home_team: "Los Angeles Lakers",
  away_team: "Boston Celtics",
  home_code: "LAL",
  away_code: "BOS",
  kickoff_utc: "2026-07-15T02:00:00Z",
  stage: "regular_season",
  has_prediction: false,
};

describe("MatchListCard", () => {
  it("renders team names", () => {
    render(<MatchListCard match={mockMatch} />);
    expect(screen.getByText("Los Angeles Lakers")).toBeDefined();
    expect(screen.getByText("Boston Celtics")).toBeDefined();
  });

  it("renders kickoff local time", () => {
    render(<MatchListCard match={mockMatch} />);
    // The exact local time depends on timezone, but it should render a time string
    expect(screen.getByText(/\d/)).toBeDefined();
  });

  it("renders sport icon for basketball", () => {
    render(<MatchListCard match={mockMatch} />);
    expect(screen.getByText("🏀")).toBeDefined();
  });

  it("renders predicted badge when has_prediction is true", () => {
    render(<MatchListCard match={{ ...mockMatch, has_prediction: true }} />);
    expect(screen.getByText("已预测")).toBeDefined();
  });

  it("renders not predicted badge when has_prediction is false", () => {
    render(<MatchListCard match={mockMatch} />);
    expect(screen.getByText("未预测")).toBeDefined();
  });

  it("card is a link to detail page", () => {
    render(<MatchListCard match={mockMatch} />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/sports/nba-12345/");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/match-list-card.test.tsx`
Expected: FAIL with "Cannot find module './match-list-card'"

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/sports/match-list-card.tsx`:

```tsx
import Link from "next/link";
import type { MatchSummary } from "@/lib/sports-api";

const SPORT_ICONS: Record<string, string> = {
  football: "⚽",
  basketball: "🏀",
  baseball: "⚾",
  hockey: "🏒",
};

interface MatchListCardProps {
  match: MatchSummary;
}

export function MatchListCard({ match }: MatchListCardProps) {
  const icon = SPORT_ICONS[match.sport] ?? "❓";
  const kickoff = match.kickoff_utc
    ? new Date(match.kickoff_utc).toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "时间待定";

  return (
    <Link
      href={`/sports/${match.match_id}/`}
      className="block rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl" aria-hidden="true">{icon}</span>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold">{match.home_team}</span>
              <span className="text-muted-foreground">vs</span>
              <span className="font-semibold">{match.away_team}</span>
            </div>
            <div className="text-xs text-muted-foreground">{kickoff}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded bg-secondary px-2 py-1 text-xs font-medium">
            {match.competition}
          </span>
          {match.has_prediction ? (
            <span className="rounded bg-primary/15 px-2 py-1 text-xs font-medium text-primary">
              已预测
            </span>
          ) : (
            <span className="rounded bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
              未预测
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/match-list-card.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd frontend
git add src/components/sports/match-list-card.tsx src/components/sports/match-list-card.test.tsx
git commit -m "feat: add MatchListCard component with sport icon and status badge"
```

---

### Task 7: Frontend SportFilter, MatchDetailPanel, Pages, Navigation

**Files:**
- Create: `frontend/src/components/sports/sport-filter.tsx`
- Create: `frontend/src/components/sports/match-detail-panel.tsx`
- Create: `frontend/src/app/sports/page.tsx`
- Create: `frontend/src/app/sports/loading.tsx`
- Create: `frontend/src/app/sports/[matchId]/page.tsx`
- Create: `frontend/src/app/sports/[matchId]/loading.tsx`
- Modify: `frontend/src/components/app-nav.tsx` (add `/sports` nav entry)

**Interfaces:**
- Consumes: All components from Tasks 3-6, `fetchMatches`/`fetchMatchDetail`/`triggerPrediction` from `lib/sports-api`
- Produces: Complete `/sports/` route tree with list and detail pages

**Note:** This task has no unit tests (per spec, composition components and pages are verified via manual testing). Verification is via typecheck and build.

- [ ] **Step 1: Create SportFilter component**

Create `frontend/src/components/sports/sport-filter.tsx`:

```tsx
"use client";

interface SportFilterProps {
  value: string | null;
  onChange: (sport: string | null) => void;
}

const SPORTS: { code: string | null; label: string }[] = [
  { code: null, label: "全部" },
  { code: "football", label: "Football" },
  { code: "basketball", label: "Basketball" },
  { code: "baseball", label: "Baseball" },
  { code: "hockey", label: "Hockey" },
];

export function SportFilter({ value, onChange }: SportFilterProps) {
  return (
    <div className="flex gap-1">
      {SPORTS.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => onChange(s.code)}
          className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
            value === s.code
              ? "bg-secondary text-foreground"
              : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
          }`}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create MatchDetailPanel component**

Create `frontend/src/components/sports/match-detail-panel.tsx`:

```tsx
"use client";

import { ProbabilityBar } from "./probability-bar";
import { FactorBreakdownTable } from "./factor-breakdown-table";
import type { MatchDetail, PredictionResult } from "@/lib/sports-api";

const SPORT_ICONS: Record<string, string> = {
  football: "⚽",
  basketball: "🏀",
  baseball: "⚾",
  hockey: "🏒",
};

interface MatchDetailPanelProps {
  match: MatchDetail;
  prediction: PredictionResult | null;
  onPredict: () => void;
  isPredicting: boolean;
}

export function MatchDetailPanel({ match, prediction, onPredict, isPredicting }: MatchDetailPanelProps) {
  const icon = SPORT_ICONS[match.sport] ?? "❓";
  const kickoff = match.kickoff_utc
    ? new Date(match.kickoff_utc).toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "时间待定";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="text-3xl" aria-hidden="true">{icon}</span>
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl font-semibold">{match.home_team}</span>
            <span className="text-muted-foreground">vs</span>
            <span className="text-xl font-semibold">{match.away_team}</span>
          </div>
          <div className="text-sm text-muted-foreground">
            <span className="rounded bg-secondary px-1.5 py-0.5 text-xs">{match.competition}</span>
            {" "}
            {kickoff}
          </div>
        </div>
      </div>

      {/* Action area */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onPredict}
          disabled={isPredicting}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {isPredicting ? "预测中..." : prediction ? "重新预测" : "预测"}
        </button>
        {prediction && prediction.prediction_timestamp && (
          <span className="text-xs text-muted-foreground">
            预测时间: {new Date(prediction.prediction_timestamp).toLocaleString("zh-CN")}
          </span>
        )}
      </div>

      {/* Prediction result area */}
      {prediction && (
        <div className="space-y-4">
          <div>
            <h3 className="mb-2 text-sm font-medium">胜率概率</h3>
            <ProbabilityBar
              probabilities={prediction.outcome_probabilities}
              homeTeam={match.home_team}
              awayTeam={match.away_team}
            />
          </div>

          {Object.keys(prediction.predicted_scores).length > 0 && (
            <div className="flex items-center gap-4">
              <div>
                <span className="text-xs text-muted-foreground">预测比分</span>
                <div className="font-mono text-lg">
                  {Object.entries(prediction.predicted_scores).map(([k, v]) => `${k}: ${v.toFixed(1)}`).join(" | ")}
                </div>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">置信度</span>
                <div className="font-mono text-lg">{(prediction.confidence * 100).toFixed(1)}%</div>
              </div>
            </div>
          )}

          <div>
            <h3 className="mb-2 text-sm font-medium">因子分解</h3>
            <FactorBreakdownTable items={prediction.explanation} />
          </div>

          <div>
            <span className="rounded bg-secondary px-2 py-1 text-xs font-mono">
              {prediction.feature_version}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create list page**

Create `frontend/src/app/sports/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { SportFilter } from "@/components/sports/sport-filter";
import { MatchListCard } from "@/components/sports/match-list-card";
import { fetchMatches, type MatchSummary } from "@/lib/sports-api";

export default function SportsPage() {
  const [sport, setSport] = useState<string | null>(null);
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchMatches(sport ?? undefined)
      .then((data) => {
        setMatches(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [sport]);

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">体育预测</h1>

      <SportFilter value={sport} onChange={setSport} />

      {loading && <p className="text-muted-foreground">加载中...</p>}

      {error && (
        <div className="space-y-2">
          <p className="text-destructive">加载失败: {error}</p>
          <button
            type="button"
            onClick={() => setSport(sport)}
            className="rounded-md border px-3 py-1.5 text-sm"
          >
            重试
          </button>
        </div>
      )}

      {!loading && !error && matches.length === 0 && (
        <p className="text-muted-foreground">今日无比赛</p>
      )}

      {!loading && !error && matches.length > 0 && (
        <div className="space-y-3">
          {matches.map((match) => (
            <MatchListCard key={match.match_id} match={match} />
          ))}
        </div>
      )}
    </main>
  );
}
```

- [ ] **Step 4: Create list page skeleton**

Create `frontend/src/app/sports/loading.tsx`:

```tsx
export default function Loading() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <div className="h-8 w-32 animate-pulse rounded bg-muted" />
      <div className="flex gap-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-8 w-20 animate-pulse rounded bg-muted" />
        ))}
      </div>
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Create detail page**

Create `frontend/src/app/sports/[matchId]/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { MatchDetailPanel } from "@/components/sports/match-detail-panel";
import {
  fetchMatchDetail,
  triggerPrediction,
  NotFoundError,
  type MatchDetail,
  type PredictionResult,
} from "@/lib/sports-api";

export default function MatchDetailPage() {
  const params = useParams();
  const matchId = params.matchId as string;

  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [isPredicting, setIsPredicting] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchMatchDetail(matchId)
      .then((data) => {
        setMatch(data.match);
        setPrediction(data.prediction);
        setLoading(false);
      })
      .catch((err) => {
        if (err instanceof NotFoundError) {
          setNotFound(true);
        } else {
          setError(err.message);
        }
        setLoading(false);
      });
  }, [matchId]);

  const handlePredict = () => {
    setIsPredicting(true);
    triggerPrediction(matchId)
      .then((result) => {
        setPrediction(result);
        setIsPredicting(false);
      })
      .catch((err) => {
        setError(err.message);
        setIsPredicting(false);
      });
  };

  if (loading) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-6 md:px-6">
        <p className="text-muted-foreground">加载中...</p>
      </main>
    );
  }

  if (notFound) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-muted-foreground">比赛不存在</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </main>
    );
  }

  if (error || !match) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-destructive">加载失败: {error}</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <Link href="/sports" className="text-sm text-muted-foreground hover:underline">
        ← 返回列表
      </Link>
      <MatchDetailPanel
        match={match}
        prediction={prediction}
        onPredict={handlePredict}
        isPredicting={isPredicting}
      />
    </main>
  );
}
```

- [ ] **Step 6: Create detail page skeleton**

Create `frontend/src/app/sports/[matchId]/loading.tsx`:

```tsx
export default function Loading() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <div className="h-4 w-20 animate-pulse rounded bg-muted" />
      <div className="h-10 w-64 animate-pulse rounded bg-muted" />
      <div className="h-10 w-24 animate-pulse rounded bg-muted" />
      <div className="space-y-4">
        <div className="h-32 animate-pulse rounded bg-muted" />
        <div className="h-48 animate-pulse rounded bg-muted" />
      </div>
    </main>
  );
}
```

- [ ] **Step 7: Add navigation entry**

In `frontend/src/components/app-nav.tsx`, add `Medal` to the lucide-react import and add a new NAV entry:

Replace the import line:
```typescript
import { Activity, FlaskConical, Gauge, History, Newspaper, Radar, Target, Trophy, TrendingUp, Zap } from "lucide-react";
```
With:
```typescript
import { Activity, FlaskConical, Gauge, History, Medal, Newspaper, Radar, Target, Trophy, TrendingUp, Zap } from "lucide-react";
```

Add the new entry to the NAV array (before the World Cup entry):
```typescript
  { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
  { href: "/world-cup", label: "世界杯", icon: Trophy, match: ["/world-cup"] },
```

- [ ] **Step 8: Run typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS (no type errors)

- [ ] **Step 9: Run all frontend tests**

Run: `cd frontend && npx vitest run`
Expected: PASS (all existing + new tests)

- [ ] **Step 10: Commit**

```bash
cd frontend
git add src/components/sports/sport-filter.tsx src/components/sports/match-detail-panel.tsx src/app/sports/ src/components/app-nav.tsx
git commit -m "feat: add /sports pages, SportFilter, MatchDetailPanel, and navigation entry"
```
