# Learning Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a closed-loop learning dashboard at `/sports/learning` with 3 tabs (engine performance, prediction history, calibration) + standalone single-match trajectory route.

**Architecture:** 5 new read-only GET endpoints reuse Phase 3's existing learning tables (KernelPredictionHistory, KernelCalibration, KernelEngineScore). 6 new frontend components under `components/sports/learning/` compose into a 3-tab dashboard. New `learning-api.ts` client isolates from MVP's `sports-api.ts`.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), Next.js App Router/React/TypeScript/recharts/Vitest (frontend)

**Spec:** `docs/superpowers/specs/2026-07-15-learning-dashboard-design.md`

## Global Constraints

1. All 5 new endpoints must be GET — read-only, trigger no learning loop, write no data
2. 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`) zero modification
3. `PredictionKernel`, `LearningService` zero modification
4. `KernelPrediction` table zero modification (reliability query reads but does not alter)
5. `bins` parameter range 5-20, default 10 — out of range returns 422
6. `COMPETITION_SPORT` mapping constant at top of `predictions.py`, covers all 10 competitions: wc/ucl/epl/laliga/bundesliga/seriea/ligue1/nba/mlb/nhl
7. `KernelPredictionHistory` has no sport/competition column — history list's sport/competition filter must JOIN `KernelPrediction` table
8. `KernelEngineScore` has no sport column — sport filter must reverse-lookup via `COMPETITION_SPORT` to competition list
9. `get_prediction_history_by_match` returns empty list, NOT 404 — match_id nonexistent returns `{ items: [], count: 0 }`
10. Frontend new components all under `components/sports/learning/` subdirectory
11. New `learning-api.ts`, not extending `sports-api.ts`
12. `getWorldCupApiBase()` returns without `/api` suffix — fetch paths include `/api/` prefix
13. recharts + `@/components/ui/chart-lite` reuse — no new chart library
14. MVP 5 components + 2 pages zero modification
15. `app-nav.tsx` only adds 1 entry — insert `学习仪表盘 → /sports/learning` after `/sports`, before `/world-cup`
16. Dynamic rendering — trajectory chart uses `Object.keys(probs)` for dynamic line count
17. Vitest jsdom must mock `next/link` — inherited `trades/page.test.tsx:18-24` pattern
18. TDD strict — backend DB functions RED (ImportError) before GREEN
19. `KERNEL_PREDICTION_ENABLED=false` returns 503 on all 5 endpoints
20. `outcome_correct=null` shows "待算"; `outcome=null` shows "—"
21. Parallel request fault tolerance uses `Promise.allSettled`
22. `loading.tsx` is server component (no `"use client"`); `page.tsx` with hooks is `"use client"`

---

## File Structure

**Backend:**
- Modify: `backend/app/kernel/kernel_db.py` — 5 new query functions appended after `get_match_ids_with_predictions`
- Modify: `backend/app/api/routes/predictions.py` — `COMPETITION_SPORT` constant + 5 new endpoints + 3 helper functions
- Create: `backend/tests/test_learning_endpoints.py` — DB function tests + endpoint tests

**Frontend:**
- Create: `frontend/src/lib/learning-api.ts` — types + 5 fetch functions
- Create: `frontend/src/lib/learning-api.test.ts`
- Create: `frontend/src/components/sports/learning/reliability-chart.tsx` — leaf chart component
- Create: `frontend/src/components/sports/learning/reliability-chart.test.tsx`
- Create: `frontend/src/components/sports/learning/engine-performance-panel.tsx` — Tab 1
- Create: `frontend/src/components/sports/learning/engine-performance-panel.test.tsx`
- Create: `frontend/src/components/sports/learning/prediction-history-list.tsx` — Tab 2
- Create: `frontend/src/components/sports/learning/prediction-history-list.test.tsx`
- Create: `frontend/src/components/sports/learning/prediction-trajectory.tsx` — standalone route
- Create: `frontend/src/components/sports/learning/prediction-trajectory.test.tsx`
- Create: `frontend/src/components/sports/learning/calibration-panel.tsx` — Tab 3
- Create: `frontend/src/components/sports/learning/calibration-panel.test.tsx`
- Create: `frontend/src/components/sports/learning/learning-tabs.tsx` — tab container
- Create: `frontend/src/components/sports/learning/learning-tabs.test.tsx`
- Create: `frontend/src/app/sports/learning/page.tsx`
- Create: `frontend/src/app/sports/learning/loading.tsx`
- Create: `frontend/src/app/sports/learning/history/[matchId]/page.tsx`
- Create: `frontend/src/app/sports/learning/history/[matchId]/loading.tsx`
- Modify: `frontend/src/components/app-nav.tsx` — 1 new NAV entry + GraduationCap import

---

### Task 1: Backend DB Functions

**Files:**
- Modify: `backend/app/kernel/kernel_db.py` (append after `get_match_ids_with_predictions`, ~line 291)
- Test: `backend/tests/test_learning_endpoints.py` (new)

**Interfaces:**
- Produces: `get_engine_scores(engine=None, competition=None, sport=None) -> list[KernelEngineScore]`
- Produces: `get_prediction_history(sport=None, competition=None, limit=50, offset=0) -> tuple[list[dict], int]`
- Produces: `get_prediction_history_by_match(match_id: str) -> dict` (returns `{match_id, sport, competition, items, count}`)
- Produces: `get_calibrations(engine=None, competition=None) -> list[KernelCalibration]`
- Produces: `compute_reliability_bins(engine=None, competition=None, bins=10) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_learning_endpoints.py`:

```python
"""Tests for learning dashboard DB functions and endpoints."""
import json
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    close_kernel_session,
    get_kernel_session,
    init_kernel_db,
    KernelCalibration,
    KernelEngineScore,
    KernelMatchOutcome,
    KernelPrediction,
    KernelPredictionHistory,
    get_engine_scores,
    get_prediction_history,
    get_prediction_history_by_match,
    get_calibrations,
    compute_reliability_bins,
)


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite DB per test."""
    init_kernel_db(str(tmp_path / "test_kernel.db"))
    session = get_kernel_session()
    yield session
    session.close()
    close_kernel_session()


def _insert_prediction(session, match_id="nba-1", sport="basketball", competition="nba",
                       engine="basketball", probs=None, confidence=0.6):
    """Insert a KernelPrediction row."""
    if probs is None:
        probs = {"home_win": 0.62, "away_win": 0.38}
    pred = KernelPrediction(
        match_id=match_id, sport=sport, competition=competition, season="2025",
        engine=engine, predicted_scores={"home": 112, "away": 108},
        outcome_probabilities=probs, confidence=confidence,
        feature_version="nba-1.0", explanation=[],
        created_at=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    session.add(pred)
    session.commit()
    return pred


def _insert_history(session, match_id="nba-1", engine="basketball", trigger="initial",
                    created_at=None):
    """Insert a KernelPredictionHistory row."""
    if created_at is None:
        created_at = datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc)
    hist = KernelPredictionHistory(
        match_id=match_id, engine=engine,
        predicted_scores={"home": 112, "away": 108},
        outcome_probabilities={"home_win": 0.62, "away_win": 0.38},
        confidence=0.6, feature_version="nba-1.0", trigger=trigger,
        created_at=created_at,
    )
    session.add(hist)
    session.commit()
    return hist


def _insert_outcome(session, match_id="nba-1", outcome="home_win", correct=1,
                    score_mae=2.5, brier=0.19):
    """Insert a KernelMatchOutcome row."""
    o = KernelMatchOutcome(
        match_id=match_id, home_score=113, away_score=107,
        outcome=outcome, engine="basketball",
        score_mae=score_mae, outcome_correct=correct, brier_score=brier,
        finished_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
    )
    session.add(o)
    session.commit()
    return o


def _insert_engine_score(session, engine="basketball", competition="nba",
                         accuracy=0.625, avg_mae=3.2, brier=0.21, count=48, cal=0.94):
    """Insert a KernelEngineScore row."""
    s = KernelEngineScore(
        engine=engine, competition=competition, accuracy=accuracy,
        avg_mae=avg_mae, brier_score=brier, sample_count=count,
        confidence_calibration=cal,
        last_updated=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    session.add(s)
    session.commit()
    return s


def _insert_calibration(session, engine="basketball", competition="nba",
                        slope=0.85, intercept=0.05, count=48, avg_conf=0.62, avg_acc=0.625):
    """Insert a KernelCalibration row."""
    c = KernelCalibration(
        engine=engine, competition=competition, slope=slope, intercept=intercept,
        sample_count=count, avg_confidence=avg_conf, avg_accuracy=avg_acc,
        last_updated=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    session.add(c)
    session.commit()
    return c


class TestGetEngineScores:
    def test_returns_all(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores()
        assert len(result) == 2

    def test_filter_by_engine(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores(engine="basketball")
        assert len(result) == 1
        assert result[0].engine == "basketball"

    def test_filter_by_competition(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores(competition="nba")
        assert len(result) == 1
        assert result[0].competition == "nba"

    def test_filter_by_sport_reverse_lookup(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores(sport="basketball")
        assert len(result) == 1
        assert result[0].competition == "nba"

    def test_empty_table_returns_empty_list(self, db):
        result = get_engine_scores()
        assert result == []


class TestGetPredictionHistory:
    def test_pagination(self, db):
        _insert_prediction(db, match_id="nba-1")
        for i in range(3):
            _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 18 + i, tzinfo=timezone.utc))
        items, total = get_prediction_history(limit=2, offset=0)
        assert len(items) == 2
        assert total == 3

    def test_total_count(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 19, tzinfo=timezone.utc))
        _, total = get_prediction_history()
        assert total == 2

    def test_sport_filter(self, db):
        _insert_prediction(db, match_id="nba-1", sport="basketball", competition="nba")
        _insert_prediction(db, match_id="wc-1", sport="football", competition="wc")
        _insert_history(db, match_id="nba-1")
        _insert_history(db, match_id="wc-1")
        items, total = get_prediction_history(sport="basketball")
        assert total == 1
        assert items[0]["match_id"] == "nba-1"

    def test_outcome_null_when_unfinished(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        items, _ = get_prediction_history()
        assert items[0]["outcome"] is None

    def test_outcome_correct_null_when_uncomputed(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        # Insert outcome WITHOUT outcome_correct (null)
        o = KernelMatchOutcome(
            match_id="nba-1", home_score=113, away_score=107,
            outcome="home_win", engine=None,
            score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
        )
        db.add(o)
        db.commit()
        items, _ = get_prediction_history()
        assert items[0]["outcome"] is not None
        assert items[0]["outcome"]["outcome_correct"] is None

    def test_items_include_sport_and_competition(self, db):
        _insert_prediction(db, match_id="nba-1", sport="basketball", competition="nba")
        _insert_history(db, match_id="nba-1")
        items, _ = get_prediction_history()
        assert items[0]["sport"] == "basketball"
        assert items[0]["competition"] == "nba"


class TestGetPredictionHistoryByMatch:
    def test_returns_asc_list(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 20, tzinfo=timezone.utc))
        _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 18, tzinfo=timezone.utc))
        result = get_prediction_history_by_match("nba-1")
        assert result["count"] == 2
        assert result["items"][0]["created_at"] < result["items"][1]["created_at"]

    def test_nonexistent_returns_empty_not_404(self, db):
        result = get_prediction_history_by_match("nonexistent")
        assert result["count"] == 0
        assert result["items"] == []

    def test_includes_sport_competition(self, db):
        _insert_prediction(db, match_id="nba-1", sport="basketball", competition="nba")
        _insert_history(db, match_id="nba-1")
        result = get_prediction_history_by_match("nba-1")
        assert result["sport"] == "basketball"
        assert result["competition"] == "nba"

    def test_sport_null_when_no_kernel_prediction(self, db):
        # History exists but no KernelPrediction row
        _insert_history(db, match_id="orphan-1")
        result = get_prediction_history_by_match("orphan-1")
        assert result["sport"] is None
        assert result["competition"] is None
        assert result["count"] == 1


class TestGetCalibrations:
    def test_returns_all(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        _insert_calibration(db, engine="elo_odds", competition="wc")
        result = get_calibrations()
        assert len(result) == 2

    def test_filter_by_engine(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        _insert_calibration(db, engine="elo_odds", competition="wc")
        result = get_calibrations(engine="basketball")
        assert len(result) == 1
        assert result[0].engine == "basketball"

    def test_filter_by_competition(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        _insert_calibration(db, engine="elo_odds", competition="wc")
        result = get_calibrations(competition="nba")
        assert len(result) == 1

    def test_empty_table_returns_empty_list(self, db):
        result = get_calibrations()
        assert result == []


class TestComputeReliabilityBins:
    def test_ten_bins_default(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        result = compute_reliability_bins()
        assert len(result["bins"]) == 10
        assert result["total_samples"] == 1

    def test_five_bins(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        result = compute_reliability_bins(bins=5)
        assert len(result["bins"]) == 5

    def test_empty_bins_return_null_values(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.95, "away_win": 0.05})
        _insert_outcome(db, match_id="m1", correct=1)
        result = compute_reliability_bins(bins=10)
        # First few bins should be empty (count=0, avg_predicted=null)
        empty_bins = [b for b in result["bins"] if b["count"] == 0]
        assert len(empty_bins) > 0
        assert all(b["avg_predicted"] is None for b in empty_bins)
        assert all(b["actual_frequency"] is None for b in empty_bins)

    def test_total_samples_correct(self, db):
        for i in range(5):
            _insert_prediction(db, match_id=f"m{i}", probs={"home_win": 0.6 + i * 0.05, "away_win": 0.4 - i * 0.05})
            _insert_outcome(db, match_id=f"m{i}", correct=1 if i % 2 == 0 else 0)
        result = compute_reliability_bins()
        assert result["total_samples"] == 5

    def test_filter_by_engine(self, db):
        _insert_prediction(db, match_id="m1", engine="basketball", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        _insert_prediction(db, match_id="m2", engine="elo_odds", competition="wc", sport="football",
                          probs={"home_win": 0.5, "draw": 0.3, "away_win": 0.2})
        _insert_outcome(db, match_id="m2", correct=0)
        result = compute_reliability_bins(engine="basketball")
        assert result["total_samples"] == 1

    def test_no_samples_returns_empty_bins(self, db):
        result = compute_reliability_bins()
        assert len(result["bins"]) == 10
        assert result["total_samples"] == 0
        assert all(b["count"] == 0 for b in result["bins"])

    def test_avg_predicted_and_actual_frequency(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.55, "away_win": 0.45})
        _insert_outcome(db, match_id="m1", correct=1)
        _insert_prediction(db, match_id="m2", probs={"home_win": 0.58, "away_win": 0.42})
        _insert_outcome(db, match_id="m2", correct=0)
        result = compute_reliability_bins(bins=10)
        # Both predictions fall in bin [0.5, 0.6)
        bin_50_60 = [b for b in result["bins"] if b["lower"] == 0.5][0]
        assert bin_50_60["count"] == 2
        assert abs(bin_50_60["avg_predicted"] - 0.565) < 0.01
        assert abs(bin_50_60["actual_frequency"] - 0.5) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_learning_endpoints.py -v`
Expected: ImportError — cannot import name `get_engine_scores` from `app.kernel.kernel_db`

- [ ] **Step 3: Implement the 5 DB functions**

Append to `backend/app/kernel/kernel_db.py` after `get_match_ids_with_predictions` (end of file):

```python
# --- Learning Dashboard query functions ---


def get_engine_scores(engine: str | None = None,
                      competition: str | None = None,
                      sport: str | None = None) -> list[KernelEngineScore]:
    """Get engine performance scores, optionally filtered.

    Args:
        engine: Filter by engine name.
        competition: Filter by competition code.
        sport: Filter by sport code — reverse-lookup via COMPETITION_SPORT
               mapping (defined in predictions.py to avoid circular import).
               Here we accept sport and convert to competition list.

    Note: COMPETITION_SPORT is imported lazily to avoid circular dependency.
    """
    session = get_kernel_session()
    try:
        query = session.query(KernelEngineScore)
        if engine is not None:
            query = query.filter(KernelEngineScore.engine == engine)
        if competition is not None:
            query = query.filter(KernelEngineScore.competition == competition)
        if sport is not None:
            # Reverse-lookup: sport → competition list
            from app.api.routes.predictions import COMPETITION_SPORT
            competitions = [c for c, s in COMPETITION_SPORT.items() if s == sport]
            if competitions:
                query = query.filter(KernelEngineScore.competition.in_(competitions))
            else:
                return []  # No matching competitions
        return query.all()
    except Exception:
        return []
    finally:
        session.close()


def get_prediction_history(sport: str | None = None,
                           competition: str | None = None,
                           limit: int = 50,
                           offset: int = 0) -> tuple[list[dict], int]:
    """Get prediction history with optional filters, paginated.

    Returns (items, total) where items is a list of dicts with history +
    outcome data, and total is the unpaginated count.
    """
    session = get_kernel_session()
    try:
        # Build base query with JOINs
        query = (
            session.query(KernelPredictionHistory, KernelMatchOutcome, KernelPrediction)
            .outerjoin(KernelMatchOutcome,
                       KernelPredictionHistory.match_id == KernelMatchOutcome.match_id)
            .outerjoin(KernelPrediction,
                       KernelPredictionHistory.match_id == KernelPrediction.match_id)
        )

        # Apply filters on KernelPrediction
        if sport is not None:
            query = query.filter(KernelPrediction.sport == sport)
        if competition is not None:
            query = query.filter(KernelPrediction.competition == competition)

        # Get total count (before pagination)
        total = query.count()

        # Apply pagination + ordering
        rows = (
            query
            .order_by(KernelPredictionHistory.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        items = []
        for hist, outcome, pred in rows:
            item = {
                "id": hist.id,
                "match_id": hist.match_id,
                "sport": pred.sport if pred else None,
                "competition": pred.competition if pred else None,
                "engine": hist.engine,
                "predicted_scores": hist.predicted_scores,
                "outcome_probabilities": hist.outcome_probabilities,
                "confidence": hist.confidence,
                "feature_version": hist.feature_version,
                "trigger": hist.trigger,
                "created_at": hist.created_at.isoformat() if hist.created_at else None,
                "outcome": None,
            }
            if outcome is not None:
                item["outcome"] = {
                    "home_score": outcome.home_score,
                    "away_score": outcome.away_score,
                    "outcome": outcome.outcome,
                    "outcome_correct": outcome.outcome_correct,
                    "score_mae": outcome.score_mae,
                    "brier_score": outcome.brier_score,
                    "finished_at": outcome.finished_at.isoformat() if outcome.finished_at else None,
                }
            items.append(item)

        return items, total
    except Exception:
        return [], 0
    finally:
        session.close()


def get_prediction_history_by_match(match_id: str) -> dict:
    """Get all prediction history records for a single match, time-sorted ASC.

    Returns {match_id, sport, competition, items, count}.
    Returns empty items (NOT 404) when match_id has no history.
    """
    session = get_kernel_session()
    try:
        rows = (
            session.query(KernelPredictionHistory, KernelPrediction)
            .outerjoin(KernelPrediction,
                       KernelPredictionHistory.match_id == KernelPrediction.match_id)
            .filter(KernelPredictionHistory.match_id == match_id)
            .order_by(KernelPredictionHistory.created_at.asc())
            .all()
        )

        sport = None
        competition = None
        items = []
        for hist, pred in rows:
            if pred is not None and sport is None:
                sport = pred.sport
                competition = pred.competition
            items.append({
                "id": hist.id,
                "match_id": hist.match_id,
                "sport": pred.sport if pred else None,
                "competition": pred.competition if pred else None,
                "engine": hist.engine,
                "predicted_scores": hist.predicted_scores,
                "outcome_probabilities": hist.outcome_probabilities,
                "confidence": hist.confidence,
                "feature_version": hist.feature_version,
                "trigger": hist.trigger,
                "created_at": hist.created_at.isoformat() if hist.created_at else None,
                "outcome": None,  # trajectory doesn't need outcome per record
            })

        return {
            "match_id": match_id,
            "sport": sport,
            "competition": competition,
            "items": items,
            "count": len(items),
        }
    except Exception:
        return {"match_id": match_id, "sport": None, "competition": None, "items": [], "count": 0}
    finally:
        session.close()


def get_calibrations(engine: str | None = None,
                     competition: str | None = None) -> list[KernelCalibration]:
    """Get calibration parameters, optionally filtered."""
    session = get_kernel_session()
    try:
        query = session.query(KernelCalibration)
        if engine is not None:
            query = query.filter(KernelCalibration.engine == engine)
        if competition is not None:
            query = query.filter(KernelCalibration.competition == competition)
        return query.all()
    except Exception:
        return []
    finally:
        session.close()


def compute_reliability_bins(engine: str | None = None,
                             competition: str | None = None,
                             bins: int = 10) -> dict:
    """Compute binned reliability data on-the-fly.

    Bins predictions by max(outcome_probabilities) and compares to actual
    outcome_correct frequency. Returns bins with avg_predicted,
    actual_frequency, and count per bin.

    Empty bins (count=0) return avg_predicted=null, actual_frequency=null.
    """
    session = get_kernel_session()
    try:
        query = (
            session.query(KernelPrediction, KernelMatchOutcome)
            .join(KernelMatchOutcome,
                  KernelPrediction.match_id == KernelMatchOutcome.match_id)
            .filter(KernelMatchOutcome.outcome_correct.isnot(None))
        )
        if engine is not None:
            query = query.filter(KernelPrediction.engine == engine)
        if competition is not None:
            query = query.filter(KernelPrediction.competition == competition)

        rows = query.all()

        # Initialize bins
        bin_width = 1.0 / bins
        bin_list = []
        for i in range(bins):
            lower = i * bin_width
            upper = (i + 1) * bin_width
            bin_list.append({
                "lower": round(lower, 4),
                "upper": round(upper, 4),
                "center": round((lower + upper) / 2, 4),
                "avg_predicted": None,
                "actual_frequency": None,
                "count": 0,
            })

        # Accumulate per bin
        bin_sums = [{"predicted_sum": 0.0, "actual_sum": 0.0, "count": 0} for _ in range(bins)]
        for pred, outcome in rows:
            probs = pred.outcome_probabilities or {}
            if not probs:
                continue
            predicted_prob = max(probs.values())
            actual = outcome.outcome_correct  # 1 or 0

            # Determine bin index (clamp to last bin for prob=1.0)
            bin_idx = min(int(predicted_prob / bin_width), bins - 1)
            bin_sums[bin_idx]["predicted_sum"] += predicted_prob
            bin_sums[bin_idx]["actual_sum"] += actual
            bin_sums[bin_idx]["count"] += 1

        # Finalize bin values
        for i, bs in enumerate(bin_sums):
            if bs["count"] > 0:
                bin_list[i]["avg_predicted"] = round(bs["predicted_sum"] / bs["count"], 4)
                bin_list[i]["actual_frequency"] = round(bs["actual_sum"] / bs["count"], 4)
                bin_list[i]["count"] = bs["count"]

        return {
            "engine": engine,
            "competition": competition,
            "bins": bin_list,
            "total_samples": len(rows),
        }
    except Exception:
        return {
            "engine": engine,
            "competition": competition,
            "bins": [],
            "total_samples": 0,
        }
    finally:
        session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_learning_endpoints.py -v`
Expected: All 25 DB function tests PASS

- [ ] **Step 5: Run regression check**

Run: `cd backend && python -m pytest tests/test_api_predictions.py tests/test_kernel_db_fixtures.py -v`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/kernel/kernel_db.py backend/tests/test_learning_endpoints.py
git commit -m "feat: add 5 learning dashboard DB query functions"
```

---

### Task 2: Backend API Endpoints

**Files:**
- Modify: `backend/app/api/routes/predictions.py` (add `COMPETITION_SPORT` constant + 5 endpoints + 3 helpers)
- Test: `backend/tests/test_learning_endpoints.py` (append endpoint tests)

**Interfaces:**
- Consumes: Task 1's 5 DB functions
- Produces: 5 GET endpoints (`/engines/scores`, `/history`, `/history/{match_id}`, `/calibration`, `/calibration/reliability`)

- [ ] **Step 1: Write the failing endpoint tests**

Append to `backend/tests/test_learning_endpoints.py`:

```python
# --- Endpoint tests ---

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes.predictions import router
from app.core import config


def _create_app():
    app = FastAPI()
    app.include_router(router)
    return app


def _enable_kernel():
    """Enable KERNEL_PREDICTION_ENABLED for endpoint tests."""
    original = config.settings.KERNEL_PREDICTION_ENABLED
    config.settings.KERNEL_PREDICTION_ENABLED = True
    return original


def _restore_kernel(original):
    config.settings.KERNEL_PREDICTION_ENABLED = original


class TestEngineScoresEndpoint:
    def test_200_returns_list(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/engines/scores")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["engine"] == "basketball"
            assert "accuracy" in data[0]
            assert "confidence_calibration" in data[0]
        finally:
            _restore_kernel(original)

    def test_sport_filter_passthrough(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/engines/scores?sport=basketball")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["competition"] == "nba"
        finally:
            _restore_kernel(original)

    def test_503_when_disabled(self):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/engines/scores")
            assert resp.status_code == 503
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original


class TestHistoryListEndpoint:
    def test_200_returns_paginated_structure(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/history")
            assert resp.status_code == 200
            data = resp.json()
            assert "items" in data
            assert "total" in data
            assert "limit" in data
            assert "offset" in data
            assert len(data["items"]) == 1
        finally:
            _restore_kernel(original)

    def test_limit_offset_passthrough(self, db):
        _insert_prediction(db, match_id="nba-1")
        for i in range(3):
            _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 18 + i, tzinfo=timezone.utc))
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/history?limit=2&offset=0")
            assert resp.status_code == 200
            data = resp.json()
            assert data["limit"] == 2
            assert data["offset"] == 0
            assert len(data["items"]) == 2
            assert data["total"] == 3
        finally:
            _restore_kernel(original)

    def test_503_when_disabled(self):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/history")
            assert resp.status_code == 503
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original


class TestHistoryByMatchEndpoint:
    def test_200_returns_trajectory(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/history/nba-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["match_id"] == "nba-1"
            assert data["count"] == 1
            assert len(data["items"]) == 1
        finally:
            _restore_kernel(original)

    def test_nonexistent_returns_empty_not_404(self, db):
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/history/nonexistent")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
            assert data["items"] == []
        finally:
            _restore_kernel(original)

    def test_503_when_disabled(self):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/history/nba-1")
            assert resp.status_code == 503
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original


class TestCalibrationEndpoint:
    def test_200_returns_list(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/calibration")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["engine"] == "basketball"
            assert "slope" in data[0]
            assert "intercept" in data[0]
        finally:
            _restore_kernel(original)

    def test_503_when_disabled(self):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/calibration")
            assert resp.status_code == 503
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original


class TestReliabilityEndpoint:
    def test_200_returns_binned_data(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/calibration/reliability")
            assert resp.status_code == 200
            data = resp.json()
            assert "bins" in data
            assert "total_samples" in data
            assert len(data["bins"]) == 10
        finally:
            _restore_kernel(original)

    def test_bins_param_passthrough(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/calibration/reliability?bins=5")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["bins"]) == 5
        finally:
            _restore_kernel(original)

    def test_bins_out_of_range_422(self):
        original = _enable_kernel()
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/calibration/reliability?bins=3")
            assert resp.status_code == 422
        finally:
            _restore_kernel(original)

    def test_503_when_disabled(self):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            app = _create_app()
            client = TestClient(app)
            resp = client.get("/predictions/calibration/reliability")
            assert resp.status_code == 503
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_learning_endpoints.py::TestEngineScoresEndpoint -v`
Expected: FAIL — endpoints not defined (404 or route not found)

- [ ] **Step 3: Add COMPETITION_SPORT constant**

Add at top of `backend/app/api/routes/predictions.py`, after `logger = logging.getLogger(__name__)` (line 23):

```python
COMPETITION_SPORT = {
    "wc": "football", "ucl": "football", "epl": "football",
    "laliga": "football", "bundesliga": "football",
    "seriea": "football", "ligue1": "football",
    "nba": "basketball", "mlb": "baseball", "nhl": "hockey",
}
```

- [ ] **Step 4: Add 5 endpoints + 3 helper functions**

Append to the end of `backend/app/api/routes/predictions.py`:

```python
# --- Learning Dashboard endpoints ---


def _engine_score_to_dict(score) -> dict:
    """Serialize KernelEngineScore to dict."""
    return {
        "engine": score.engine,
        "competition": score.competition,
        "accuracy": score.accuracy,
        "avg_mae": score.avg_mae,
        "brier_score": score.brier_score,
        "sample_count": score.sample_count,
        "confidence_calibration": score.confidence_calibration,
        "last_updated": score.last_updated.isoformat() if score.last_updated else None,
    }


def _calibration_to_dict(cal) -> dict:
    """Serialize KernelCalibration to dict."""
    return {
        "engine": cal.engine,
        "competition": cal.competition,
        "slope": cal.slope,
        "intercept": cal.intercept,
        "sample_count": cal.sample_count,
        "avg_confidence": cal.avg_confidence,
        "avg_accuracy": cal.avg_accuracy,
        "last_updated": cal.last_updated.isoformat() if cal.last_updated else None,
    }


@router.get("/engines/scores")
def list_engine_scores(engine: str | None = None,
                       competition: str | None = None,
                       sport: str | None = None):
    """List engine performance scores with optional filters."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    from app.kernel.kernel_db import get_engine_scores
    scores = get_engine_scores(engine=engine, competition=competition, sport=sport)
    return [_engine_score_to_dict(s) for s in scores]


@router.get("/history")
def list_prediction_history(sport: str | None = None,
                            competition: str | None = None,
                            limit: int = 50,
                            offset: int = 0):
    """List prediction history, paginated."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1-200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    from app.kernel.kernel_db import get_prediction_history
    items, total = get_prediction_history(sport=sport, competition=competition,
                                           limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/history/{match_id}")
def get_match_history(match_id: str):
    """Get single-match prediction trajectory (all history records)."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    from app.kernel.kernel_db import get_prediction_history_by_match
    return get_prediction_history_by_match(match_id)


@router.get("/calibration")
def list_calibrations(engine: str | None = None,
                      competition: str | None = None):
    """List calibration parameters."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    from app.kernel.kernel_db import get_calibrations
    cals = get_calibrations(engine=engine, competition=competition)
    return [_calibration_to_dict(c) for c in cals]


@router.get("/calibration/reliability")
def get_reliability(engine: str | None = None,
                    competition: str | None = None,
                    bins: int = 10):
    """Get binned reliability data for calibration chart."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    if bins < 5 or bins > 20:
        raise HTTPException(status_code=422, detail="bins must be 5-20")
    from app.kernel.kernel_db import compute_reliability_bins
    return compute_reliability_bins(engine=engine, competition=competition, bins=bins)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_learning_endpoints.py -v`
Expected: All tests PASS (DB + endpoint)

- [ ] **Step 6: Run regression check**

Run: `cd backend && python -m pytest tests/test_api_predictions.py -v`
Expected: All 16 existing API tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/predictions.py backend/tests/test_learning_endpoints.py
git commit -m "feat: add 5 learning dashboard GET endpoints + COMPETITION_SPORT constant"
```

---

### Task 3: Frontend API Client

**Files:**
- Create: `frontend/src/lib/learning-api.ts`
- Create: `frontend/src/lib/learning-api.test.ts`

**Interfaces:**
- Consumes: Task 2's 5 endpoints
- Produces: TypeScript types + 5 fetch functions for Tasks 4-7

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/learning-api.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchEngineScores,
  fetchPredictionHistory,
  fetchPredictionTrajectory,
  fetchCalibration,
  fetchReliability,
} from "./learning-api";

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

describe("fetchEngineScores", () => {
  it("calls correct URL without params", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchEngineScores();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/engines/scores");
  });

  it("calls correct URL with sport param", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchEngineScores({ sport: "basketball" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/engines/scores?sport=basketball",
    );
  });

  it("calls correct URL with multiple params", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchEngineScores({ engine: "basketball", competition: "nba" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/engines/scores?engine=basketball&competition=nba",
    );
  });

  it("throws on 503", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 });
    await expect(fetchEngineScores()).rejects.toThrow("Failed to fetch engine scores");
  });
});

describe("fetchPredictionHistory", () => {
  it("calls correct URL with limit and offset", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 50, offset: 0 }),
    });
    await fetchPredictionHistory({ limit: 50, offset: 0 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/history?limit=50&offset=0",
    );
  });

  it("calls correct URL with sport filter", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 50, offset: 0 }),
    });
    await fetchPredictionHistory({ sport: "basketball", limit: 50, offset: 0 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/history?sport=basketball&limit=50&offset=0",
    );
  });

  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchPredictionHistory()).rejects.toThrow("Failed to fetch prediction history");
  });
});

describe("fetchPredictionTrajectory", () => {
  it("calls correct URL with matchId", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ match_id: "nba-1", sport: null, competition: null, items: [], count: 0 }),
    });
    await fetchPredictionTrajectory("nba-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/history/nba-1",
    );
  });

  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchPredictionTrajectory("nba-1")).rejects.toThrow("Failed to fetch trajectory");
  });
});

describe("fetchCalibration", () => {
  it("calls correct URL without params", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchCalibration();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/calibration");
  });

  it("calls correct URL with engine param", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchCalibration({ engine: "basketball" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/calibration?engine=basketball",
    );
  });
});

describe("fetchReliability", () => {
  it("calls correct URL with bins param", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ engine: null, competition: null, bins: [], total_samples: 0 }),
    });
    await fetchReliability({ bins: 10 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/calibration/reliability?bins=10",
    );
  });

  it("throws on 422", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 422 });
    await expect(fetchReliability({ bins: 3 })).rejects.toThrow("Failed to fetch reliability");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/learning-api.test.ts`
Expected: FAIL — cannot import from `./learning-api`

- [ ] **Step 3: Implement learning-api.ts**

Create `frontend/src/lib/learning-api.ts`:

```typescript
import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

// Type definitions (single source of truth)

export interface EngineScoreItem {
  engine: string;
  competition: string | null;
  accuracy: number;
  avg_mae: number;
  brier_score: number;
  sample_count: number;
  confidence_calibration: number;
  last_updated: string | null;
}

export interface PredictionHistoryItem {
  id: number;
  match_id: string;
  sport: string | null;
  competition: string | null;
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  feature_version: string;
  trigger: string;
  created_at: string;
  outcome: {
    home_score: number;
    away_score: number;
    outcome: string;
    outcome_correct: number | null;
    score_mae: number | null;
    brier_score: number | null;
    finished_at: string | null;
  } | null;
}

export interface PredictionHistoryList {
  items: PredictionHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PredictionTrajectory {
  match_id: string;
  sport: string | null;
  competition: string | null;
  items: PredictionHistoryItem[];
  count: number;
}

export interface CalibrationItem {
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_confidence: number;
  avg_accuracy: number;
  last_updated: string | null;
}

export interface ReliabilityBin {
  lower: number;
  upper: number;
  center: number;
  avg_predicted: number | null;
  actual_frequency: number | null;
  count: number;
}

export interface ReliabilityData {
  engine: string | null;
  competition: string | null;
  bins: ReliabilityBin[];
  total_samples: number;
}

// Helper: build query string from params object
function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

// Fetch functions

export async function fetchEngineScores(params?: {
  engine?: string;
  competition?: string;
  sport?: string;
}): Promise<EngineScoreItem[]> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/engines/scores${qs}`);
  if (!res.ok) throw new Error("Failed to fetch engine scores");
  return res.json();
}

export async function fetchPredictionHistory(params?: {
  sport?: string;
  competition?: string;
  limit?: number;
  offset?: number;
}): Promise<PredictionHistoryList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/history${qs}`);
  if (!res.ok) throw new Error("Failed to fetch prediction history");
  return res.json();
}

export async function fetchPredictionTrajectory(matchId: string): Promise<PredictionTrajectory> {
  const res = await fetch(`${API_BASE}/api/predictions/history/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch trajectory");
  return res.json();
}

export async function fetchCalibration(params?: {
  engine?: string;
  competition?: string;
}): Promise<CalibrationItem[]> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/calibration${qs}`);
  if (!res.ok) throw new Error("Failed to fetch calibration");
  return res.json();
}

export async function fetchReliability(params?: {
  engine?: string;
  competition?: string;
  bins?: number;
}): Promise<ReliabilityData> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/predictions/calibration/reliability${qs}`);
  if (!res.ok) throw new Error("Failed to fetch reliability");
  return res.json();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/learning-api.test.ts`
Expected: All 10 tests PASS

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/learning-api.ts frontend/src/lib/learning-api.test.ts
git commit -m "feat: add learning-api.ts client with types and 5 fetch functions"
```

---

### Task 4: ReliabilityChart Component

**Files:**
- Create: `frontend/src/components/sports/learning/reliability-chart.tsx`
- Create: `frontend/src/components/sports/learning/reliability-chart.test.tsx`

**Interfaces:**
- Consumes: `ReliabilityBin[]` from Task 3's `learning-api.ts`
- Produces: `ReliabilityChart({ bins }: { bins: ReliabilityBin[] })` for Task 7's CalibrationPanel

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/sports/learning/reliability-chart.test.tsx`:

```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReliabilityChart } from "./reliability-chart";
import type { ReliabilityBin } from "@/lib/learning-api";

// Mock recharts
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  ScatterChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="scatter-chart">{children}</div>
  ),
  Scatter: ({ data }: { data: unknown[] }) => (
    <div data-testid="scatter" data-count={data.length} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  ReferenceLine: (props: { segment?: unknown[] }) => (
    <div data-testid="reference-line" data-has-segment={!!props.segment} />
  ),
  Tooltip: () => <div data-testid="tooltip" />,
}));

// Mock chart-lite
vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

describe("ReliabilityChart", () => {
  const nonEmptyBins: ReliabilityBin[] = [
    { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
    { lower: 0.6, upper: 0.7, center: 0.65, avg_predicted: 0.62, actual_frequency: 0.70, count: 8 },
  ];

  it("renders chart frame and scatter", () => {
    render(<ReliabilityChart bins={nonEmptyBins} />);
    expect(screen.getByTestId("chart-frame")).toBeInTheDocument();
    expect(screen.getByTestId("scatter")).toBeInTheDocument();
  });

  it("passes non-empty bins to Scatter as data points", () => {
    render(<ReliabilityChart bins={nonEmptyBins} />);
    const scatter = screen.getByTestId("scatter");
    expect(scatter.getAttribute("data-count")).toBe("2");
  });

  it("renders diagonal reference line", () => {
    render(<ReliabilityChart bins={nonEmptyBins} />);
    const refLine = screen.getByTestId("reference-line");
    expect(refLine.getAttribute("data-has-segment")).toBe("true");
  });

  it("renders with empty bins without crashing", () => {
    const emptyBins: ReliabilityBin[] = Array.from({ length: 10 }, (_, i) => ({
      lower: i * 0.1,
      upper: (i + 1) * 0.1,
      center: i * 0.1 + 0.05,
      avg_predicted: null,
      actual_frequency: null,
      count: 0,
    }));
    render(<ReliabilityChart bins={emptyBins} />);
    expect(screen.getByTestId("scatter").getAttribute("data-count")).toBe("0");
  });

  it("skips empty bins (null avg_predicted) in scatter data", () => {
    const mixedBins: ReliabilityBin[] = [
      { lower: 0.0, upper: 0.1, center: 0.05, avg_predicted: null, actual_frequency: null, count: 0 },
      { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
      { lower: 0.9, upper: 1.0, center: 0.95, avg_predicted: null, actual_frequency: null, count: 0 },
    ];
    render(<ReliabilityChart bins={mixedBins} />);
    expect(screen.getByTestId("scatter").getAttribute("data-count")).toBe("1");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/sports/learning/reliability-chart.test.tsx`
Expected: FAIL — cannot import `ReliabilityChart`

- [ ] **Step 3: Implement ReliabilityChart**

Create `frontend/src/components/sports/learning/reliability-chart.tsx`:

```typescript
import { CartesianGrid, ReferenceLine, Scatter, ScatterChart, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import type { ReliabilityBin } from "@/lib/learning-api";

interface ReliabilityChartProps {
  bins: ReliabilityBin[];
}

export function ReliabilityChart({ bins }: ReliabilityChartProps) {
  // Filter out empty bins (null avg_predicted) for scatter data
  const data = bins
    .filter((b) => b.avg_predicted !== null && b.actual_frequency !== null)
    .map((b) => ({
      x: b.avg_predicted,
      y: b.actual_frequency,
      lower: b.lower,
      upper: b.upper,
      count: b.count,
    }));

  return (
    <ChartFrame height={320}>
      <ScatterChart margin={{ top: 16, right: 24, bottom: 24, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis
          type="number"
          dataKey="x"
          name="预测概率"
          domain={[0, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <YAxis
          type="number"
          dataKey="y"
          name="实际频率"
          domain={[0, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <DarkTooltip
          formatter={(value, _name, payload) => {
            const p = payload as { lower?: number; upper?: number; count?: number };
            return [
              `${(Number(value) * 100).toFixed(1)}%`,
              `桶 [${p.lower?.toFixed(1)} - ${p.upper?.toFixed(1)}) · ${p.count} 样本`,
            ];
          }}
        />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ]}
          stroke="var(--muted-foreground)"
          strokeDasharray="4 4"
          label={{ value: "完美校准", position: "topRight", fontSize: 11 }}
        />
        <Scatter data={data} fill="var(--primary)" />
      </ScatterChart>
    </ChartFrame>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/sports/learning/reliability-chart.test.tsx`
Expected: All 5 tests PASS

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/sports/learning/reliability-chart.tsx frontend/src/components/sports/learning/reliability-chart.test.tsx
git commit -m "feat: add ReliabilityChart component with scatter + diagonal reference line"
```

---

### Task 5: EnginePerformancePanel Component (Tab 1)

**Files:**
- Create: `frontend/src/components/sports/learning/engine-performance-panel.tsx`
- Create: `frontend/src/components/sports/learning/engine-performance-panel.test.tsx`

**Interfaces:**
- Consumes: `fetchEngineScores` from Task 3
- Produces: `EnginePerformancePanel()` for Task 7's LearningTabs

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/sports/learning/engine-performance-panel.test.tsx`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { EnginePerformancePanel } from "./engine-performance-panel";

// Mock learning-api
vi.mock("@/lib/learning-api", () => ({
  fetchEngineScores: vi.fn(),
}));

import { fetchEngineScores } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchEngineScores).mockReset();
});

const mockScore = {
  engine: "basketball",
  competition: "nba",
  accuracy: 0.625,
  avg_mae: 3.2,
  brier_score: 0.21,
  sample_count: 48,
  confidence_calibration: 0.94,
  last_updated: "2026-07-14T18:30:00Z",
};

describe("EnginePerformancePanel", () => {
  it("renders filter dropdowns", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("引擎")).toBeInTheDocument();
      expect(screen.getByText("赛事")).toBeInTheDocument();
      expect(screen.getByText("运动")).toBeInTheDocument();
    });
  });

  it("renders data table with scores", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([mockScore]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("basketball")).toBeInTheDocument();
      expect(screen.getByText("nba")).toBeInTheDocument();
      expect(screen.getByText("62.5%")).toBeInTheDocument();
    });
  });

  it("renders empty state when no data", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("暂无性能数据，等待比赛结果录入")).toBeInTheDocument();
    });
  });

  it("renders loading state", async () => {
    vi.mocked(fetchEngineScores).mockReturnValueOnce(new Promise(() => {})); // never resolves
    render(<EnginePerformancePanel />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("renders error state on fetch failure", async () => {
    vi.mocked(fetchEngineScores).mockRejectedValueOnce(new Error("Network error"));
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument();
    });
  });

  it("applies green color class for high accuracy", async () => {
    vi.mocked(fetchEngineScores).mockResolvedValueOnce([{ ...mockScore, accuracy: 0.85 }]);
    render(<EnginePerformancePanel />);
    await waitFor(() => {
      const accuracyCell = screen.getByText("85.0%");
      expect(accuracyCell.className).toContain("text-green");
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/sports/learning/engine-performance-panel.test.tsx`
Expected: FAIL — cannot import `EnginePerformancePanel`

- [ ] **Step 3: Implement EnginePerformancePanel**

Create `frontend/src/components/sports/learning/engine-performance-panel.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import { fetchEngineScores, type EngineScoreItem } from "@/lib/learning-api";

const SPORT_OPTIONS = [
  { value: "", label: "全部" },
  { value: "football", label: "Football" },
  { value: "basketball", label: "Basketball" },
  { value: "baseball", label: "Baseball" },
  { value: "hockey", label: "Hockey" },
];

const ENGINE_OPTIONS = [
  { value: "", label: "全部" },
  { value: "elo_odds", label: "elo_odds" },
  { value: "basketball", label: "basketball" },
  { value: "baseball", label: "baseball" },
  { value: "hockey", label: "hockey" },
];

const COMPETITION_OPTIONS = [
  { value: "", label: "全部" },
  { value: "wc", label: "wc" },
  { value: "ucl", label: "ucl" },
  { value: "epl", label: "epl" },
  { value: "laliga", label: "laliga" },
  { value: "bundesliga", label: "bundesliga" },
  { value: "seriea", label: "seriea" },
  { value: "ligue1", label: "ligue1" },
  { value: "nba", label: "nba" },
  { value: "mlb", label: "mlb" },
  { value: "nhl", label: "nhl" },
];

function accuracyClass(acc: number): string {
  if (acc >= 0.70) return "text-green-600 dark:text-green-400 font-medium";
  if (acc < 0.50) return "text-red-600 dark:text-red-400";
  return "";
}

function brierClass(brier: number): string {
  if (brier <= 0.20) return "text-green-600 dark:text-green-400 font-medium";
  if (brier > 0.30) return "text-red-600 dark:text-red-400";
  return "";
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function EnginePerformancePanel() {
  const [data, setData] = useState<EngineScoreItem[] | null>(null);
  const [error, setError] = useState(false);
  const [engine, setEngine] = useState("");
  const [competition, setCompetition] = useState("");
  const [sport, setSport] = useState("");

  useEffect(() => {
    setError(false);
    setData(null);
    fetchEngineScores({
      engine: engine || undefined,
      competition: competition || undefined,
      sport: sport || undefined,
    })
      .then(setData)
      .catch(() => setError(true));
  }, [engine, competition, sport]);

  if (data === null && !error) {
    return <div className="p-4 text-sm text-muted-foreground">加载中...</div>;
  }

  if (error) {
    return <div className="p-4 text-sm text-red-500">加载失败</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span>引擎</span>
          <select
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {ENGINE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>赛事</span>
          <select
            value={competition}
            onChange={(e) => setCompetition(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {COMPETITION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>运动</span>
          <select
            value={sport}
            onChange={(e) => setSport(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {SPORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>

      {data!.length === 0 ? (
        <div className="p-4 text-sm text-muted-foreground">暂无性能数据，等待比赛结果录入</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 pr-4">引擎</th>
                <th className="py-2 pr-4">赛事</th>
                <th className="py-2 pr-4">准确率</th>
                <th className="py-2 pr-4">MAE</th>
                <th className="py-2 pr-4">Brier</th>
                <th className="py-2 pr-4">校准度</th>
                <th className="py-2 pr-4">样本数</th>
                <th className="py-2 pr-4">更新时间</th>
              </tr>
            </thead>
            <tbody>
              {data!.map((row, i) => (
                <tr key={`${row.engine}-${row.competition ?? "global"}-${i}`} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono">{row.engine}</td>
                  <td className="py-2 pr-4 font-mono">{row.competition ?? "全局"}</td>
                  <td className={`py-2 pr-4 font-mono ${accuracyClass(row.accuracy)}`}>{fmtPct(row.accuracy)}</td>
                  <td className="py-2 pr-4 font-mono">{row.avg_mae?.toFixed(2) ?? "—"}</td>
                  <td className={`py-2 pr-4 font-mono ${brierClass(row.brier_score)}`}>{row.brier_score?.toFixed(3) ?? "—"}</td>
                  <td className="py-2 pr-4 font-mono">{row.confidence_calibration?.toFixed(3) ?? "—"}</td>
                  <td className="py-2 pr-4 font-mono">{row.sample_count}</td>
                  <td className="py-2 pr-4 text-muted-foreground">
                    {row.last_updated ? new Date(row.last_updated).toLocaleString("zh-CN") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/sports/learning/engine-performance-panel.test.tsx`
Expected: All 6 tests PASS

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/sports/learning/engine-performance-panel.tsx frontend/src/components/sports/learning/engine-performance-panel.test.tsx
git commit -m "feat: add EnginePerformancePanel component (Tab 1) with filters and color-coded table"
```

---

### Task 6: PredictionHistoryList + PredictionTrajectory

**Files:**
- Create: `frontend/src/components/sports/learning/prediction-history-list.tsx`
- Create: `frontend/src/components/sports/learning/prediction-history-list.test.tsx`
- Create: `frontend/src/components/sports/learning/prediction-trajectory.tsx`
- Create: `frontend/src/components/sports/learning/prediction-trajectory.test.tsx`

**Interfaces:**
- Consumes: `fetchPredictionHistory`, `fetchPredictionTrajectory` from Task 3
- Produces: `PredictionHistoryList()` for Tab 2, `PredictionTrajectory({ matchId })` for standalone route

- [ ] **Step 1: Write the failing test for PredictionHistoryList**

Create `frontend/src/components/sports/learning/prediction-history-list.test.tsx`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PredictionHistoryList } from "./prediction-history-list";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

// Mock learning-api
vi.mock("@/lib/learning-api", () => ({
  fetchPredictionHistory: vi.fn(),
}));

import { fetchPredictionHistory } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchPredictionHistory).mockReset();
});

const mockItem = {
  id: 1,
  match_id: "nba-20250101-LAL-BOS",
  sport: "basketball",
  competition: "nba",
  engine: "basketball",
  predicted_scores: { home: 112, away: 108 },
  outcome_probabilities: { home_win: 0.62, away_win: 0.38 },
  confidence: 0.59,
  feature_version: "nba-1.0",
  trigger: "initial",
  created_at: "2026-07-14T18:30:00Z",
  outcome: {
    home_score: 113,
    away_score: 107,
    outcome: "home_win",
    outcome_correct: 1,
    score_mae: 2.5,
    brier_score: 0.19,
    finished_at: "2026-07-15T02:00:00Z",
  },
};

describe("PredictionHistoryList", () => {
  it("renders table rows with history data", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [mockItem], total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("nba-20250101-LAL-BOS")).toBeInTheDocument();
    });
  });

  it("shows — for outcome=null (unfinished)", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [{ ...mockItem, outcome: null }], total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("—")).toBeInTheDocument();
    });
  });

  it("shows 待算 for outcome_correct=null", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [{
        ...mockItem,
        outcome: { ...mockItem.outcome!, outcome_correct: null },
      }],
      total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("待算")).toBeInTheDocument();
    });
  });

  it("renders row as link to trajectory page", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [mockItem], total: 1, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      const link = screen.getByRole("link");
      expect(link.getAttribute("href")).toBe("/sports/learning/history/nba-20250101-LAL-BOS");
    });
  });

  it("renders pagination controls", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [mockItem], total: 100, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("下一页")).toBeInTheDocument();
    });
  });

  it("renders empty state", async () => {
    vi.mocked(fetchPredictionHistory).mockResolvedValueOnce({
      items: [], total: 0, limit: 50, offset: 0,
    });
    render(<PredictionHistoryList />);
    await waitFor(() => {
      expect(screen.getByText("暂无预测历史记录")).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 2: Write the failing test for PredictionTrajectory**

Create `frontend/src/components/sports/learning/prediction-trajectory.test.tsx`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PredictionTrajectory } from "./prediction-trajectory";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

// Mock recharts
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  Line: ({ dataKey }: { dataKey: string }) => (
    <div data-testid="line" data-key={dataKey} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

// Mock chart-lite
vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

// Mock learning-api
vi.mock("@/lib/learning-api", () => ({
  fetchPredictionTrajectory: vi.fn(),
}));

import { fetchPredictionTrajectory } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchPredictionTrajectory).mockReset();
});

const mockTrajectory = {
  match_id: "nba-1",
  sport: "basketball",
  competition: "nba",
  items: [
    {
      id: 1, match_id: "nba-1", sport: "basketball", competition: "nba",
      engine: "basketball",
      predicted_scores: { home: 112, away: 108 },
      outcome_probabilities: { home_win: 0.62, away_win: 0.38 },
      confidence: 0.59, feature_version: "nba-1.0", trigger: "initial",
      created_at: "2026-07-14T18:30:00Z", outcome: null,
    },
    {
      id: 2, match_id: "nba-1", sport: "basketball", competition: "nba",
      engine: "basketball",
      predicted_scores: { home: 114, away: 106 },
      outcome_probabilities: { home_win: 0.68, away_win: 0.32 },
      confidence: 0.64, feature_version: "nba-1.0", trigger: "weight_update",
      created_at: "2026-07-14T20:00:00Z", outcome: null,
    },
  ],
  count: 2,
};

describe("PredictionTrajectory", () => {
  it("renders match_id in header", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      expect(screen.getByText("nba-1")).toBeInTheDocument();
    });
  });

  it("renders trajectory chart with dynamic lines per outcome", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      const lines = screen.getAllByTestId("line");
      // 2 outcomes (home_win, away_win) → 2 lines in trajectory chart
      // Plus 1 line in confidence chart = 3 total
      expect(lines.length).toBe(3);
    });
  });

  it("renders back link", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      const link = screen.getByRole("link");
      expect(link.getAttribute("href")).toBe("/sports/learning?tab=history");
    });
  });

  it("renders empty state when no history", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce({
      match_id: "empty-1", sport: null, competition: null, items: [], count: 0,
    });
    render(<PredictionTrajectory matchId="empty-1" />);
    await waitFor(() => {
      expect(screen.getByText("该比赛暂无历史预测记录")).toBeInTheDocument();
    });
  });

  it("renders detail table with trigger info", async () => {
    vi.mocked(fetchPredictionTrajectory).mockResolvedValueOnce(mockTrajectory);
    render(<PredictionTrajectory matchId="nba-1" />);
    await waitFor(() => {
      expect(screen.getByText("initial")).toBeInTheDocument();
      expect(screen.getByText("weight_update")).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/learning/prediction-history-list.test.tsx src/components/sports/learning/prediction-trajectory.test.tsx`
Expected: FAIL — cannot import components

- [ ] **Step 4: Implement PredictionHistoryList**

Create `frontend/src/components/sports/learning/prediction-history-list.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchPredictionHistory, type PredictionHistoryItem } from "@/lib/learning-api";

const SPORT_FILTERS = [
  { value: "", label: "全部" },
  { value: "football", label: "Football" },
  { value: "basketball", label: "Basketball" },
  { value: "baseball", label: "Baseball" },
  { value: "hockey", label: "Hockey" },
];

const COMPETITION_OPTIONS = [
  { value: "", label: "全部" },
  { value: "wc", label: "wc" },
  { value: "ucl", label: "ucl" },
  { value: "epl", label: "epl" },
  { value: "laliga", label: "laliga" },
  { value: "bundesliga", label: "bundesliga" },
  { value: "seriea", label: "seriea" },
  { value: "ligue1", label: "ligue1" },
  { value: "nba", label: "nba" },
  { value: "mlb", label: "mlb" },
  { value: "nhl", label: "nhl" },
];

const PAGE_SIZE = 50;

function maxProb(probs: Record<string, number>): string {
  const values = Object.values(probs);
  if (values.length === 0) return "—";
  return `${(Math.max(...values) * 100).toFixed(1)}%`;
}

function resultBadge(item: PredictionHistoryItem): string {
  if (item.outcome === null) return "—";
  if (item.outcome.outcome_correct === null) return "待算";
  return item.outcome.outcome_correct ? "✓" : "✗";
}

export function PredictionHistoryList() {
  const [items, setItems] = useState<PredictionHistoryItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [sport, setSport] = useState("");
  const [competition, setCompetition] = useState("");
  const [error, setError] = useState(false);

  useEffect(() => {
    setOffset(0);
  }, [sport, competition]);

  useEffect(() => {
    setError(false);
    setItems(null);
    fetchPredictionHistory({
      sport: sport || undefined,
      competition: competition || undefined,
      limit: PAGE_SIZE,
      offset,
    })
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
      })
      .catch(() => setError(true));
  }, [sport, offset]);

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  if (items === null && !error) {
    return <div className="p-4 text-sm text-muted-foreground">加载中...</div>;
  }

  if (error) {
    return <div className="p-4 text-sm text-red-500">加载失败</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span>运动</span>
          <select
            value={sport}
            onChange={(e) => setSport(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {SPORT_FILTERS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>赛事</span>
          <select
            value={competition}
            onChange={(e) => setCompetition(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {COMPETITION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>

      {items!.length === 0 ? (
        <div className="p-4 text-sm text-muted-foreground">暂无预测历史记录</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4">时间</th>
                  <th className="py-2 pr-4">Match ID</th>
                  <th className="py-2 pr-4">引擎</th>
                  <th className="py-2 pr-4">预测概率</th>
                  <th className="py-2 pr-4">置信度</th>
                  <th className="py-2 pr-4">结果</th>
                  <th className="py-2 pr-4">MAE</th>
                </tr>
              </thead>
              <tbody>
                {items!.map((item) => (
                  <tr key={item.id} className="border-b border-border/50 hover:bg-muted/30">
                    <td className="py-2 pr-4 text-muted-foreground">
                      {new Date(item.created_at).toLocaleString("zh-CN")}
                    </td>
                    <td className="py-2 pr-4">
                      <Link
                        href={`/sports/learning/history/${item.match_id}`}
                        className="font-mono text-primary hover:underline"
                      >
                        {item.match_id.length > 20 ? `${item.match_id.slice(0, 18)}...` : item.match_id}
                      </Link>
                    </td>
                    <td className="py-2 pr-4 font-mono">{item.engine}</td>
                    <td className="py-2 pr-4 font-mono">{maxProb(item.outcome_probabilities)}</td>
                    <td className="py-2 pr-4 font-mono">{(item.confidence * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 font-mono">{resultBadge(item)}</td>
                    <td className="py-2 pr-4 font-mono">
                      {item.outcome?.score_mae?.toFixed(2) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <span>第 {currentPage} / {totalPages} 页</span>
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="rounded border border-border px-3 py-1 disabled:opacity-40"
            >
              上一页
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total}
              className="rounded border border-border px-3 py-1 disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Implement PredictionTrajectory**

Create `frontend/src/components/sports/learning/prediction-trajectory.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import { fetchPredictionTrajectory, type PredictionTrajectory as TrajectoryData } from "@/lib/learning-api";

interface PredictionTrajectoryProps {
  matchId: string;
}

const SPORT_ICONS: Record<string, string> = {
  football: "⚽", basketball: "🏀", baseball: "⚾", hockey: "🏒",
};

export function PredictionTrajectory({ matchId }: PredictionTrajectoryProps) {
  const [data, setData] = useState<TrajectoryData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
    setData(null);
    fetchPredictionTrajectory(matchId)
      .then(setData)
      .catch(() => setError(true));
  }, [matchId]);

  if (data === null && !error) {
    return <div className="p-4 text-sm text-muted-foreground">加载中...</div>;
  }

  if (error) {
    return <div className="p-4 text-sm text-red-500">加载失败</div>;
  }

  if (data!.count === 0) {
    return (
      <div className="space-y-4">
        <Link href="/sports/learning?tab=history" className="text-sm text-primary hover:underline">
          ← 返回列表
        </Link>
        <div className="p-4 text-sm text-muted-foreground">该比赛暂无历史预测记录</div>
      </div>
    );
  }

  // Dynamic outcome keys from first item's probabilities
  const outcomeKeys = data!.items.length > 0
    ? Object.keys(data!.items[0].outcome_probabilities)
    : [];

  // Prepare chart data: [{ created_at, home_win: 0.62, away_win: 0.38, confidence: 0.59 }, ...]
  const chartData = data!.items.map((item) => ({
    created_at: new Date(item.created_at).toLocaleString("zh-CN"),
    ...item.outcome_probabilities,
    confidence: item.confidence,
  }));

  const sportIcon = data!.sport ? (SPORT_ICONS[data!.sport] ?? "❓") : "❓";

  return (
    <div className="space-y-6">
      <Link href="/sports/learning?tab=history" className="text-sm text-primary hover:underline">
        ← 返回列表
      </Link>

      <div className="flex items-center gap-3">
        <span className="text-2xl">{sportIcon}</span>
        <div>
          <h1 className="text-lg font-semibold font-mono">{data!.match_id}</h1>
          {data!.sport && (
            <p className="text-sm text-muted-foreground">{data!.sport} · {data!.competition}</p>
          )}
        </div>
      </div>

      {/* Probability trajectory chart */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">概率轨迹</h2>
        <ChartFrame height={280}>
          <LineChart data={chartData} margin={{ top: 16, right: 24, bottom: 24, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="created_at" fontSize={11} />
            <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} fontSize={11} />
            <DarkTooltip />
            {outcomeKeys.map((key) => (
              <Line key={key} type="monotone" dataKey={key} stroke="var(--primary)" strokeWidth={2} dot={{ r: 4 }} />
            ))}
          </LineChart>
        </ChartFrame>
      </div>

      {/* Confidence trajectory chart */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">置信度变化</h2>
        <ChartFrame height={200}>
          <LineChart data={chartData} margin={{ top: 16, right: 24, bottom: 24, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="created_at" fontSize={11} />
            <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} fontSize={11} />
            <DarkTooltip />
            <Line type="monotone" dataKey="confidence" stroke="var(--primary)" strokeWidth={2} dot={{ r: 4 }} />
          </LineChart>
        </ChartFrame>
      </div>

      {/* Detail table */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">预测详情</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 pr-4">时间</th>
                <th className="py-2 pr-4">引擎</th>
                <th className="py-2 pr-4">预测比分</th>
                <th className="py-2 pr-4">置信度</th>
                <th className="py-2 pr-4">版本</th>
                <th className="py-2 pr-4">触发</th>
              </tr>
            </thead>
            <tbody>
              {data!.items.map((item) => (
                <tr key={item.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 text-muted-foreground">
                    {new Date(item.created_at).toLocaleString("zh-CN")}
                  </td>
                  <td className="py-2 pr-4 font-mono">{item.engine}</td>
                  <td className="py-2 pr-4 font-mono">
                    {item.predicted_scores.home} - {item.predicted_scores.away}
                  </td>
                  <td className="py-2 pr-4 font-mono">{(item.confidence * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-4 font-mono">{item.feature_version}</td>
                  <td className="py-2 pr-4 font-mono">{item.trigger}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/learning/prediction-history-list.test.tsx src/components/sports/learning/prediction-trajectory.test.tsx`
Expected: All 11 tests PASS

- [ ] **Step 7: Run typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/sports/learning/prediction-history-list.tsx frontend/src/components/sports/learning/prediction-history-list.test.tsx frontend/src/components/sports/learning/prediction-trajectory.tsx frontend/src/components/sports/learning/prediction-trajectory.test.tsx
git commit -m "feat: add PredictionHistoryList (Tab 2) and PredictionTrajectory (standalone route)"
```

---

### Task 7: CalibrationPanel + LearningTabs + Pages + Nav

**Files:**
- Create: `frontend/src/components/sports/learning/calibration-panel.tsx`
- Create: `frontend/src/components/sports/learning/calibration-panel.test.tsx`
- Create: `frontend/src/components/sports/learning/learning-tabs.tsx`
- Create: `frontend/src/components/sports/learning/learning-tabs.test.tsx`
- Create: `frontend/src/app/sports/learning/page.tsx`
- Create: `frontend/src/app/sports/learning/loading.tsx`
- Create: `frontend/src/app/sports/learning/history/[matchId]/page.tsx`
- Create: `frontend/src/app/sports/learning/history/[matchId]/loading.tsx`
- Modify: `frontend/src/components/app-nav.tsx` (add GraduationCap import + 1 NAV entry)

**Interfaces:**
- Consumes: Tasks 4-6 components (`ReliabilityChart`, `EnginePerformancePanel`, `PredictionHistoryList`)
- Consumes: Task 3's `fetchCalibration`, `fetchReliability`

- [ ] **Step 1: Write the failing test for CalibrationPanel**

Create `frontend/src/components/sports/learning/calibration-panel.test.tsx`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { CalibrationPanel } from "./calibration-panel";

// Mock recharts (needed because ReliabilityChart is rendered)
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  ScatterChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="scatter-chart">{children}</div>
  ),
  Scatter: ({ data }: { data: unknown[] }) => (
    <div data-testid="scatter" data-count={data.length} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  ReferenceLine: () => <div data-testid="reference-line" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

vi.mock("@/lib/learning-api", () => ({
  fetchCalibration: vi.fn(),
  fetchReliability: vi.fn(),
}));

import { fetchCalibration, fetchReliability } from "@/lib/learning-api";

afterEach(() => {
  vi.mocked(fetchCalibration).mockReset();
  vi.mocked(fetchReliability).mockReset();
});

const mockCal = {
  engine: "basketball",
  competition: "nba",
  slope: 0.85,
  intercept: 0.05,
  sample_count: 48,
  avg_confidence: 0.62,
  avg_accuracy: 0.625,
  last_updated: "2026-07-14T18:30:00Z",
};

const mockReliability = {
  engine: null,
  competition: null,
  bins: [
    { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
  ],
  total_samples: 48,
};

describe("CalibrationPanel", () => {
  it("renders parameter table with calibration data", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([mockCal]);
    vi.mocked(fetchReliability).mockResolvedValueOnce(mockReliability);
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("basketball")).toBeInTheDocument();
      expect(screen.getByText("0.85")).toBeInTheDocument();
    });
  });

  it("renders reliability chart", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([mockCal]);
    vi.mocked(fetchReliability).mockResolvedValueOnce(mockReliability);
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("scatter")).toBeInTheDocument();
    });
  });

  it("renders empty state for calibration when no data", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([]);
    vi.mocked(fetchReliability).mockResolvedValueOnce({ ...mockReliability, total_samples: 0, bins: [] });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("暂无校准数据，需 ≥ MIN_SAMPLES_FOR_CALIBRATION 条记录")).toBeInTheDocument();
    });
  });

  it("renders filter dropdowns", async () => {
    vi.mocked(fetchCalibration).mockResolvedValueOnce([]);
    vi.mocked(fetchReliability).mockResolvedValueOnce({ ...mockReliability, total_samples: 0, bins: [] });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("引擎")).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 2: Write the failing test for LearningTabs**

Create `frontend/src/components/sports/learning/learning-tabs.test.tsx`:

```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { LearningTabs } from "./learning-tabs";

// Mock child panels
vi.mock("./engine-performance-panel", () => ({
  EnginePerformancePanel: () => <div data-testid="engine-panel">Engine Panel</div>,
}));
vi.mock("./prediction-history-list", () => ({
  PredictionHistoryList: () => <div data-testid="history-panel">History Panel</div>,
}));
vi.mock("./calibration-panel", () => ({
  CalibrationPanel: () => <div data-testid="calibration-panel">Calibration Panel</div>,
}));

describe("LearningTabs", () => {
  it("renders 3 tab buttons", () => {
    render(<LearningTabs />);
    expect(screen.getByText("性能对比")).toBeInTheDocument();
    expect(screen.getByText("预测历史")).toBeInTheDocument();
    expect(screen.getByText("校准诊断")).toBeInTheDocument();
  });

  it("renders engine panel by default", () => {
    render(<LearningTabs />);
    expect(screen.getByTestId("engine-panel")).toBeInTheDocument();
  });

  it("switches to history panel on tab click", () => {
    render(<LearningTabs />);
    screen.getByText("预测历史").click();
    expect(screen.getByTestId("history-panel")).toBeInTheDocument();
  });

  it("switches to calibration panel on tab click", () => {
    render(<LearningTabs />);
    screen.getByText("校准诊断").click();
    expect(screen.getByTestId("calibration-panel")).toBeInTheDocument();
  });

  it("renders refresh button", () => {
    render(<LearningTabs />);
    expect(screen.getByText("刷新")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/learning/calibration-panel.test.tsx src/components/sports/learning/learning-tabs.test.tsx`
Expected: FAIL — cannot import components

- [ ] **Step 4: Implement CalibrationPanel**

Create `frontend/src/components/sports/learning/calibration-panel.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import { fetchCalibration, fetchReliability, type CalibrationItem, type ReliabilityData } from "@/lib/learning-api";
import { ReliabilityChart } from "./reliability-chart";

const ENGINE_OPTIONS = [
  { value: "", label: "全部" },
  { value: "elo_odds", label: "elo_odds" },
  { value: "basketball", label: "basketball" },
  { value: "baseball", label: "baseball" },
  { value: "hockey", label: "hockey" },
];

const COMPETITION_OPTIONS = [
  { value: "", label: "全部" },
  { value: "wc", label: "wc" },
  { value: "ucl", label: "ucl" },
  { value: "epl", label: "epl" },
  { value: "nba", label: "nba" },
  { value: "mlb", label: "mlb" },
  { value: "nhl", label: "nhl" },
];

export function CalibrationPanel() {
  const [calibrations, setCalibrations] = useState<CalibrationItem[] | null>(null);
  const [reliability, setReliability] = useState<ReliabilityData | null>(null);
  const [calError, setCalError] = useState(false);
  const [relError, setRelError] = useState(false);
  const [engine, setEngine] = useState("");
  const [competition, setCompetition] = useState("");

  useEffect(() => {
    setCalError(false);
    setCalibrations(null);
    setRelError(false);
    setReliability(null);

    const params = { engine: engine || undefined, competition: competition || undefined };

    // Parallel requests with allSettled — one failure doesn't block the other
    Promise.allSettled([
      fetchCalibration(params),
      fetchReliability(params),
    ]).then(([calResult, relResult]) => {
      if (calResult.status === "fulfilled") {
        setCalibrations(calResult.value);
      } else {
        setCalError(true);
      }
      if (relResult.status === "fulfilled") {
        setReliability(relResult.value);
      } else {
        setRelError(true);
      }
    });
  }, [engine, competition]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span>引擎</span>
          <select
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {ENGINE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>赛事</span>
          <select
            value={competition}
            onChange={(e) => setCompetition(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {COMPETITION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>

      {/* Parameter table */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">校准参数</h2>
        {calError ? (
          <div className="p-4 text-sm text-red-500">校准数据加载失败</div>
        ) : calibrations === null ? (
          <div className="p-4 text-sm text-muted-foreground">加载中...</div>
        ) : calibrations.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground">暂无校准数据，需 ≥ MIN_SAMPLES_FOR_CALIBRATION 条记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4">引擎</th>
                  <th className="py-2 pr-4">赛事</th>
                  <th className="py-2 pr-4">斜率</th>
                  <th className="py-2 pr-4">截距</th>
                  <th className="py-2 pr-4">样本数</th>
                  <th className="py-2 pr-4">平均置信度</th>
                  <th className="py-2 pr-4">平均准确率</th>
                  <th className="py-2 pr-4">更新时间</th>
                </tr>
              </thead>
              <tbody>
                {calibrations.map((cal, i) => (
                  <tr key={`${cal.engine}-${cal.competition}-${i}`} className="border-b border-border/50">
                    <td className="py-2 pr-4 font-mono">{cal.engine}</td>
                    <td className="py-2 pr-4 font-mono">{cal.competition}</td>
                    <td className="py-2 pr-4 font-mono">{cal.slope.toFixed(3)}</td>
                    <td className="py-2 pr-4 font-mono">{cal.intercept.toFixed(3)}</td>
                    <td className="py-2 pr-4 font-mono">{cal.sample_count}</td>
                    <td className="py-2 pr-4 font-mono">{(cal.avg_confidence * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 font-mono">{(cal.avg_accuracy * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 text-muted-foreground">
                      {cal.last_updated ? new Date(cal.last_updated).toLocaleString("zh-CN") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Reliability chart */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">可靠性图</h2>
        {relError ? (
          <div className="p-4 text-sm text-red-500">可靠性数据加载失败</div>
        ) : reliability === null ? (
          <div className="p-4 text-sm text-muted-foreground">加载中...</div>
        ) : (
          <ReliabilityChart bins={reliability.bins} />
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Implement LearningTabs**

Create `frontend/src/components/sports/learning/learning-tabs.tsx`:

```typescript
"use client";

import { useCallback, useEffect, useReducer, useState } from "react";
import { EnginePerformancePanel } from "./engine-performance-panel";
import { PredictionHistoryList } from "./prediction-history-list";
import { CalibrationPanel } from "./calibration-panel";

type TabId = "performance" | "history" | "calibration";

const TABS: { id: TabId; label: string }[] = [
  { id: "performance", label: "性能对比" },
  { id: "history", label: "预测历史" },
  { id: "calibration", label: "校准诊断" },
];

const VALID_TABS: TabId[] = ["performance", "history", "calibration"];

export function LearningTabs() {
  const [activeTab, setActiveTab] = useState<TabId>("performance");
  // refreshKey forces panel re-mount on refresh button click
  const [refreshKey, forceRefresh] = useReducer((x: number) => x + 1, 0);

  // Read ?tab= from URL on mount (spec 2.1: synced with URL query for shareability)
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab && VALID_TABS.includes(tab as TabId)) {
      setActiveTab(tab as TabId);
    }
  }, []);

  // Update URL on tab change (replaceState, no scroll jump)
  const handleTabChange = useCallback((tab: TabId) => {
    setActiveTab(tab);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (tab === "performance") {
      url.searchParams.delete("tab");
    } else {
      url.searchParams.set("tab", tab);
    }
    window.history.replaceState({}, "", url);
  }, []);

  const handleRefresh = useCallback(() => {
    forceRefresh();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">闭环学习仪表盘</h1>
        <button
          onClick={handleRefresh}
          className="rounded border border-border px-3 py-1 text-sm hover:bg-muted"
        >
          刷新
        </button>
      </div>

      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-primary text-primary font-medium"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div key={`${activeTab}-${refreshKey}`}>
        {activeTab === "performance" && <EnginePerformancePanel />}
        {activeTab === "history" && <PredictionHistoryList />}
        {activeTab === "calibration" && <CalibrationPanel />}
      </div>
    </div>
  );
}
```

**Note:** URL `?tab=` sync is implemented via `window.location` + `history.replaceState` (not `useSearchParams`) to avoid Next.js Suspense boundary requirements. On mount, reads `?tab=` and sets active tab; on tab change, updates URL. This satisfies spec 2.1's shareability requirement and makes the trajectory back link (`/sports/learning?tab=history`) land on Tab 2.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/learning/calibration-panel.test.tsx src/components/sports/learning/learning-tabs.test.tsx`
Expected: All 9 tests PASS

- [ ] **Step 7: Create page files**

Create `frontend/src/app/sports/learning/page.tsx`:

```typescript
"use client";

import { LearningTabs } from "@/components/sports/learning/learning-tabs";

export default function LearningDashboardPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <LearningTabs />
    </main>
  );
}
```

Create `frontend/src/app/sports/learning/loading.tsx`:

```typescript
export default function Loading() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <div className="h-8 w-48 animate-pulse rounded bg-muted" />
      <div className="mt-6 h-10 w-full animate-pulse rounded bg-muted" />
      <div className="mt-4 h-64 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}
```

Create `frontend/src/app/sports/learning/history/[matchId]/page.tsx`:

```typescript
"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { PredictionTrajectory } from "@/components/sports/learning/prediction-trajectory";

export default function MatchTrajectoryPage() {
  const params = useParams();
  const matchId = params.matchId as string;

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <PredictionTrajectory matchId={matchId} />
    </main>
  );
}
```

Create `frontend/src/app/sports/learning/history/[matchId]/loading.tsx`:

```typescript
export default function Loading() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      <div className="mt-4 h-8 w-64 animate-pulse rounded bg-muted" />
      <div className="mt-6 h-64 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}
```

- [ ] **Step 8: Modify app-nav.tsx**

In `frontend/src/components/app-nav.tsx`:

Edit 1: Add `GraduationCap` to the lucide-react import (after `Medal`):

```typescript
import { Activity, FlaskConical, Gauge, GraduationCap, History, Medal, Newspaper, Radar, Target, Trophy, TrendingUp, Zap } from "lucide-react";
```

Edit 2: Add new NAV entry after `/sports`, before `/world-cup`:

```typescript
  { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
```

The final NAV array should look like:

```typescript
const NAV = [
  { href: "/", label: "监控面板", icon: Radar, match: ["/", "/events"] },
  { href: "/decisions", label: "决策机会", icon: Target, match: ["/decisions"] },
  { href: "/edges", label: "Edge 监测", icon: Zap, match: ["/edges"] },
  { href: "/analyze", label: "人工分析", icon: FlaskConical, match: ["/analyze"] },
  { href: "/history", label: "历史复盘", icon: History, match: ["/history"] },
  { href: "/quality-metrics", label: "质量切片", icon: Gauge, match: ["/quality-metrics"] },
  { href: "/trades", label: "模拟交易", icon: TrendingUp, match: ["/trades"] },
  { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
  { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
  { href: "/world-cup", label: "世界杯", icon: Trophy, match: ["/world-cup"] },
];
```

- [ ] **Step 9: Run full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: All tests PASS (existing 173 + new ~35 = ~208), no regressions

- [ ] **Step 10: Run typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 11: Commit**

```bash
git add frontend/src/components/sports/learning/calibration-panel.tsx frontend/src/components/sports/learning/calibration-panel.test.tsx frontend/src/components/sports/learning/learning-tabs.tsx frontend/src/components/sports/learning/learning-tabs.test.tsx frontend/src/app/sports/learning/ frontend/src/components/app-nav.tsx
git commit -m "feat: add CalibrationPanel, LearningTabs, pages, and nav entry for learning dashboard"
```
