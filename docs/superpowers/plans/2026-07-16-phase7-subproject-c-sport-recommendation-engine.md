# Sport Recommendation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateless recommendation layer that consumes Subproject B's persisted edges and produces `SportActionableRecommendation` per match (direction + decision + confidence + risk + allocation + rationale).

**Architecture:** `SportRecommendationService` reads B's `kernel_sport_edges` via `EdgeStore` (read-only), enriches with `KernelPrediction` + `KernelCalibration` metadata, calls `diagnosis_service.decide()` for the act/watch/skip gate, and assembles a `SportActionableRecommendation` dataclass. No new table, no scheduler job, no writes. 3 API endpoints + CLI + frontend page.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic, pytest (backend); Next.js 14, React, TypeScript, Vitest, Tailwind (frontend).

## Global Constraints

1. `PHASE7_SPORT_RECOMMENDATION_ENABLED` feature flag must default to OFF — when false, all 3 endpoints return 503
2. C must NOT modify Subproject B's code (`edge_detector_service.py`, `edge_store.py`, `sport_edges.py`, `kernel_sport_edges` table)
3. C must NOT modify the event pipeline's `ActionableRecommendation` model (`backend/app/models/event.py`), its producer, or its consumers
4. C must NOT modify `diagnosis_service.py` — calls `decide()` as a pure function only
5. C must NOT modify `decision_quality_service.py` — not invoked for sports (requires `evidence_breakdown`)
6. C must NOT modify `PredictionKernel`, `LearningService`, the 3 learning tables, or the learning dashboard
7. C is stateless — never writes to the database; all data computed on-demand from B's persisted edges
8. Unit conversion: B's `adjusted_edge` is 0-1; C multiplies by 100 → pp scale before calling `decide()` and for `edge_pct` field
9. `decide()` is called with `DECISION_ACT_EDGE=6.0`, `DECISION_WATCH_EDGE=2.0`, `COLD_START_BYPASS_ENABLED=true` from existing config
10. `qualified` is determined by `get_calibration(engine, competition).sample_count >= CALIBRATION_FEEDBACK_MIN_SAMPLES` (8)
11. `direction` derivation: `raw_edge > 0` → YES, `< 0` → NO, `stale` → WAIT, high risk → AVOID
12. Primary outcome selection: pick `mapped_outcome` with max `|adjusted_edge|`
13. All 3 GET endpoints are read-only (no `require_write_key`)
14. `/{match_id}` returns 404 when no edges exist; `/open` excludes skip decisions; `/discrepancies` `min_abs_edge` is 0-1 scale (B's convention)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/core/config.py` | Modify (line ~1079) | Add `PHASE7_SPORT_RECOMMENDATION_ENABLED` flag |
| `backend/app/kernel/sport_recommendation_service.py` | Create | `SportActionableRecommendation` dataclass + `SportRecommendationService` (stateless) |
| `backend/app/api/routes/sport_recommendations.py` | Create | 3 GET endpoints (`/{match_id}`, `/open`, `/discrepancies`) |
| `backend/app/api/router.py` | Modify (line 3, 14) | Register `sport_recommendations` router |
| `backend/scripts/sport_recommendation_cli.py` | Create | CLI tool (match / open / picks subcommands) |
| `backend/tests/test_sport_recommendation_service.py` | Create | ~32 service tests (20 pure + 12 DB-integrated) |
| `backend/tests/test_sport_recommendation_routes.py` | Create | ~8 route tests |
| `backend/tests/test_sport_recommendation_cli.py` | Create | ~3 CLI tests |
| `frontend/src/lib/sport-recommendations-api.ts` | Create | API client (fetch functions + TypeScript interfaces) |
| `frontend/src/app/sports/recommendations/page.tsx` | Create | Recommendations page |
| `frontend/src/components/sports/recommendations/RecommendationCard.tsx` | Create | Single recommendation card |
| `frontend/src/components/sports/recommendations/OpenDecisionsList.tsx` | Create | Open decisions list with filter |
| `frontend/src/components/app-nav.tsx` | Modify (line 21) | Add nav entry after `/sports/markets` |
| `frontend/src/components/sports/recommendations/RecommendationCard.test.tsx` | Create | ~5 component tests |
| `frontend/src/components/sports/recommendations/OpenDecisionsList.test.tsx` | Create | ~4 component tests |

---

## Task 1: Config + SportRecommendationService

**Files:**
- Modify: `backend/app/core/config.py` (add flag before `settings = Settings()` at line 1082)
- Create: `backend/app/kernel/sport_recommendation_service.py`
- Test: `backend/tests/test_sport_recommendation_service.py`

**Interfaces:**
- Consumes: `EdgeStore.get_latest_edges(match_id) -> list[dict]`, `EdgeStore.get_top_discrepancies(limit, min_abs_edge) -> list[dict]`, `get_latest_prediction(match_id) -> KernelPrediction | None`, `get_calibration(engine, competition) -> KernelCalibration | None`, `diagnosis_service.decide(adjusted_edge, qualified, act_edge, watch_edge, cold_start_bypass_enabled) -> str`
- Produces: `SportActionableRecommendation` dataclass, `SportRecommendationService` class with `get_recommendation(match_id)`, `get_open_decisions(limit, decision)`, `get_top_picks(limit, min_abs_edge_pct)`

- [ ] **Step 1: Write the config flag test**

Create `backend/tests/test_sport_recommendation_service.py`:

```python
"""Tests for SportRecommendationService (stateless recommendation engine).

Covers: direction derivation, risk/confidence/allocation computation,
primary outcome selection, unit conversion (0-1 → 0-100), qualified
determination, get_recommendation, get_open_decisions, get_top_picks.
"""
from datetime import datetime, timezone

import pytest

from app.core import config
from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelPrediction,
    KernelCalibration,
    get_kernel_session,
)
from app.kernel.edge_store import EdgeStore
from app.kernel.sport_recommendation_service import (
    SportActionableRecommendation,
    SportRecommendationService,
    _derive_direction,
    _compute_risk_level,
    _compute_confidence,
    _compute_allocation,
    _build_rationale,
    _select_primary_outcome,
)


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "rec_service_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def service(kernel_db):
    return SportRecommendationService()


def _utcnow():
    return datetime.now(timezone.utc)


def _seed_prediction(match_id="m1", engine="BasketballEngine", competition="nba", probs=None):
    if probs is None:
        probs = {"home_win": 0.6, "away_win": 0.4}
    ts = _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition=competition,
            season="2025-26", engine=engine, predicted_scores={},
            outcome_probabilities=probs, confidence=0.7, feature_version="nba-1.0",
            explanation={}, created_at=ts, updated_at=ts,
        ))
        session.commit()
    finally:
        session.close()


def _seed_calibration(engine="BasketballEngine", competition="nba", sample_count=20, avg_accuracy=0.72):
    """Insert calibration row. Idempotent — checks before inserting."""
    session = get_kernel_session()
    try:
        existing = session.query(KernelCalibration).filter_by(
            engine=engine, competition=competition
        ).one_or_none()
        if existing is None:
            session.add(KernelCalibration(
                engine=engine, competition=competition, slope=1.0, intercept=0.0,
                sample_count=sample_count, avg_confidence=0.65,
                avg_accuracy=avg_accuracy, last_updated=_utcnow(),
            ))
            session.commit()
    finally:
        session.close()


def _seed_edge(match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.55,
               raw_edge=0.10, trust=0.72, liquidity_factor=1.0, adjusted_edge=0.072,
               stale=False, captured_at=None):
    """Insert an edge row directly via EdgeStore."""
    store = EdgeStore()
    return store.append_edge(
        match_id=match_id, mapped_outcome=mapped_outcome,
        model_prob=model_prob, market_prob=market_prob,
        raw_edge=raw_edge, trust=trust, liquidity_factor=liquidity_factor,
        adjusted_edge=adjusted_edge, spread=None, sources_count=1,
        stale=stale, captured_at=captured_at or _utcnow(),
    )


# --- Pure function tests (no DB needed) ---

def test_direction_yes_when_raw_edge_positive():
    assert _derive_direction(raw_edge=0.05, stale=False, risk_level="low") == "YES"


def test_direction_no_when_raw_edge_negative():
    assert _derive_direction(raw_edge=-0.05, stale=False, risk_level="low") == "NO"


def test_direction_wait_when_stale():
    assert _derive_direction(raw_edge=0.05, stale=True, risk_level="low") == "WAIT"


def test_direction_avoid_when_high_risk():
    assert _derive_direction(raw_edge=0.05, stale=False, risk_level="high") == "AVOID"


def test_direction_wait_when_raw_edge_zero():
    assert _derive_direction(raw_edge=0.0, stale=False, risk_level="low") == "WAIT"


def test_risk_level_high_when_stale():
    assert _compute_risk_level(liquidity_factor=1.0, trust=0.9, stale=True) == "high"


def test_risk_level_high_when_low_liquidity():
    assert _compute_risk_level(liquidity_factor=0.1, trust=0.9, stale=False) == "high"


def test_risk_level_high_when_low_trust():
    assert _compute_risk_level(liquidity_factor=1.0, trust=0.1, stale=False) == "high"


def test_risk_level_medium():
    assert _compute_risk_level(liquidity_factor=0.4, trust=0.9, stale=False) == "medium"


def test_risk_level_low():
    assert _compute_risk_level(liquidity_factor=0.8, trust=0.8, stale=False) == "low"


def test_confidence_high():
    # 6pp * 0.67 trust = 4.02 >= 4.0
    assert _compute_confidence(adjusted_edge_pct=6.0, trust=0.67) == "high"


def test_confidence_medium():
    # 4pp * 0.5 trust = 2.0 >= 2.0
    assert _compute_confidence(adjusted_edge_pct=4.0, trust=0.5) == "medium"


def test_confidence_low():
    assert _compute_confidence(adjusted_edge_pct=1.0, trust=0.5) == "low"


def test_allocation_zero_when_skip():
    assert _compute_allocation(adjusted_edge_pct=10.0, risk_level="low", decision="skip") == 0.0


def test_allocation_zero_when_high_risk():
    assert _compute_allocation(adjusted_edge_pct=10.0, risk_level="high", decision="act") == 0.0


def test_allocation_capped_at_2():
    # 12pp / 6pp = 2.0, capped at 2.0
    result = _compute_allocation(adjusted_edge_pct=12.0, risk_level="low", decision="act")
    assert result == 2.0


def test_allocation_halved_for_medium_risk():
    # 6pp / 6pp = 1.0, * 0.5 = 0.5
    result = _compute_allocation(adjusted_edge_pct=6.0, risk_level="medium", decision="act")
    assert result == 0.5


def test_rationale_contains_outcome_and_edge():
    rationale = _build_rationale(
        direction="YES", mapped_outcome="home_win", edge_pct=7.2,
        trust=0.72, liquidity_factor=0.8, stale=False, decision="act",
    )
    assert "主胜" in rationale
    assert "7.20" in rationale or "7.2" in rationale
    assert "act" in rationale
    assert "仅供参考" in rationale


def test_rationale_stale_message():
    rationale = _build_rationale(
        direction="WAIT", mapped_outcome="home_win", edge_pct=0.0,
        trust=0.5, liquidity_factor=0.5, stale=True, decision="watch",
    )
    assert "数据过期" in rationale


def test_select_primary_outcome_picks_max_abs_adjusted_edge():
    edges = [
        {"mapped_outcome": "home_win", "adjusted_edge": 0.03},
        {"mapped_outcome": "away_win", "adjusted_edge": -0.08},
        {"mapped_outcome": "draw", "adjusted_edge": 0.01},
    ]
    primary = _select_primary_outcome(edges)
    assert primary["mapped_outcome"] == "away_win"


def test_select_primary_outcome_empty_returns_none():
    assert _select_primary_outcome([]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_service.py -v --no-header 2>&1 | head -30`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.sport_recommendation_service'`

- [ ] **Step 3: Add config flag**

In `backend/app/core/config.py`, after the `EDGE_DETECTION_INTERVAL_MIN` block (line 1079) and before `settings = Settings()` (line 1082), add:

```python
    # Phase 7 Subproject C — Sport Recommendation Engine (default OFF).
    # Stateless service that computes SportActionableRecommendation from B's
    # persisted edges. When false, all /api/sport-recommendations/* endpoints
    # return 503.
    PHASE7_SPORT_RECOMMENDATION_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_RECOMMENDATION_ENABLED", "false"
    )
```

- [ ] **Step 4: Create the service file with pure helpers + dataclass**

Create `backend/app/kernel/sport_recommendation_service.py`:

```python
"""SportRecommendationService — stateless recommendation engine.

Consumes Subproject B's persisted edges (kernel_sport_edges) and produces
SportActionableRecommendation per match. Read-only: never writes to the DB.

Direction (YES/NO/AVOID/WAIT) from raw_edge sign + staleness + risk.
Decision (act/provisional_act/watch/skip) from diagnosis_service.decide()
using B's adjusted_edge (0-1 → 0-100 pp conversion).

Zero-invasion: does not modify B's code, event pipeline's
ActionableRecommendation, diagnosis_service, or decision_quality_service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core import config
from app.kernel.edge_store import EdgeStore
from app.kernel.kernel_db import get_calibration, get_latest_prediction
from app.services.diagnosis_service import decide


@dataclass(frozen=True)
class SportActionableRecommendation:
    """Per-match actionable recommendation derived from B's edge data.

    Parallel to the event pipeline's ActionableRecommendation but with
    sports-specific fields (mapped_outcome, decision, engine_name, competition).
    Never persisted — computed on-demand from kernel_sport_edges.
    """
    match_id: str
    mapped_outcome: str
    direction: str
    decision: str
    confidence: str
    risk_level: str
    edge_pct: float
    raw_edge_pct: float
    trust: float
    liquidity_factor: float
    stale: bool
    suggested_allocation_pct: float
    calibration_status: str
    rationale: str
    engine_name: str | None
    competition: str | None
    prediction_timestamp: datetime | None
    model_prob: float
    market_prob: float
    sources_count: int
    captured_at: datetime


# ---------------------------------------------------------------------------
# Pure helper functions (testable without DB)
# ---------------------------------------------------------------------------

def _select_primary_outcome(edges: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the edge with the largest |adjusted_edge|."""
    if not edges:
        return None
    return max(edges, key=lambda e: abs(e.get("adjusted_edge", 0.0)))


def _derive_direction(raw_edge: float, stale: bool, risk_level: str) -> str:
    """Derive YES/NO/WAIT/AVOID from edge sign, staleness, and risk."""
    if stale:
        return "WAIT"
    if risk_level == "high":
        return "AVOID"
    if raw_edge > 0:
        return "YES"
    if raw_edge < 0:
        return "NO"
    return "WAIT"


def _compute_risk_level(liquidity_factor: float, trust: float, stale: bool) -> str:
    """High risk when liquidity or trust is low, or data is stale."""
    if stale or liquidity_factor < 0.2 or trust < 0.2:
        return "high"
    if liquidity_factor < 0.5 or trust < 0.5:
        return "medium"
    return "low"


def _compute_confidence(adjusted_edge_pct: float, trust: float) -> str:
    """Confidence scales with both edge magnitude and trust."""
    score = abs(adjusted_edge_pct) * trust
    if score >= 4.0:
        return "high"
    if score >= 2.0:
        return "medium"
    return "low"


def _compute_allocation(adjusted_edge_pct: float, risk_level: str, decision: str) -> float:
    """Kelly-inspired fractional allocation, capped at 2% of bankroll.

    Returns value in 0-25 scale (realistically 0-2).
    Zero when decision is skip or risk is high.
    """
    if decision == "skip" or risk_level == "high":
        return 0.0
    base = min(abs(adjusted_edge_pct) / config.settings.DECISION_ACT_EDGE, 1.0) * 2.0
    if risk_level == "medium":
        base *= 0.5
    return round(base, 2)


def _build_rationale(
    direction: str,
    mapped_outcome: str,
    edge_pct: float,
    trust: float,
    liquidity_factor: float,
    stale: bool,
    decision: str,
) -> str:
    """Deterministic Chinese rationale (no LLM)."""
    outcome_zh = {"home_win": "主胜", "draw": "平局", "away_win": "客胜"}
    outcome_label = outcome_zh.get(mapped_outcome, mapped_outcome)

    if stale:
        return "数据过期，建议等待最新市场快照后再决策。"

    if direction == "AVOID":
        return "市场流动性不足或模型可信度低，建议规避。"

    if direction == "WAIT":
        return "模型与市场概率接近，无明显边际优势，建议观望。"

    action = "看好" if direction == "YES" else "看淡"
    confidence_desc = "高置信" if trust >= 0.7 else "中等置信" if trust >= 0.5 else "低置信"
    liquidity_desc = (
        "流动性充足" if liquidity_factor >= 0.5
        else "流动性一般" if liquidity_factor >= 0.2
        else "流动性不足"
    )

    return (
        f"模型{action}{outcome_label}，"
        f"调整后边际 {edge_pct:+.2f}pp，"
        f"{confidence_desc}（trust={trust:.2f}），"
        f"{liquidity_desc}。"
        f"决策建议：{decision}。"
        f"本分析仅供参考，不构成投资建议。"
    )


# ---------------------------------------------------------------------------
# Service class (DB-integrated, stateless)
# ---------------------------------------------------------------------------

class SportRecommendationService:
    """Stateless service that computes SportActionableRecommendation from B's edges.

    Reads-only: never writes to the database. All data comes from
    EdgeStore (B's persisted edges) + KernelPrediction + KernelCalibration.
    """

    def __init__(self) -> None:
        self._edge_store = EdgeStore()

    def get_recommendation(self, match_id: str) -> SportActionableRecommendation | None:
        """Compute recommendation for a single match.

        Returns None when no persisted edges exist for this match.
        """
        edges = self._edge_store.get_latest_edges(match_id)
        if not edges:
            return None
        primary = _select_primary_outcome(edges)
        if primary is None:
            return None
        return self._edge_dict_to_recommendation(primary, match_id)

    def get_open_decisions(
        self,
        limit: int = 20,
        decision: str | None = None,
    ) -> list[SportActionableRecommendation]:
        """List matches with open decisions (act/provisional_act/watch).

        Over-fetches 3x limit to account for multiple outcomes per match,
        then deduplicates by match_id keeping the primary outcome.
        """
        fetch_limit = limit * 3
        rows = self._edge_store.get_top_discrepancies(limit=fetch_limit, min_abs_edge=0.0)

        # Deduplicate by match_id, keeping the one with max |adjusted_edge|
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            mid = row.get("match_id", "")
            if mid not in seen or abs(row.get("adjusted_edge", 0.0)) > abs(seen[mid].get("adjusted_edge", 0.0)):
                seen[mid] = row

        # Build recommendations
        recs: list[SportActionableRecommendation] = []
        for row in seen.values():
            rec = self._edge_dict_to_recommendation(row, row.get("match_id", ""))
            if rec is None:
                continue
            # Filter: open decisions exclude "skip"
            if rec.decision == "skip":
                continue
            # Filter by specific decision if requested
            if decision is not None and rec.decision != decision:
                continue
            recs.append(rec)
            if len(recs) >= limit:
                break
        return recs

    def get_top_picks(
        self,
        limit: int = 20,
        min_abs_edge_pct: float = 0.0,
    ) -> list[SportActionableRecommendation]:
        """List top edge picks (largest |adjusted_edge|), regardless of decision.

        min_abs_edge_pct is on 0-100 pp scale.
        """
        # Convert pp to 0-1 for EdgeStore's API
        min_abs_edge_01 = min_abs_edge_pct / 100.0
        fetch_limit = limit * 3
        rows = self._edge_store.get_top_discrepancies(
            limit=fetch_limit, min_abs_edge=min_abs_edge_01
        )

        # Deduplicate by match_id, keeping the one with max |adjusted_edge|
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            mid = row.get("match_id", "")
            if mid not in seen or abs(row.get("adjusted_edge", 0.0)) > abs(seen[mid].get("adjusted_edge", 0.0)):
                seen[mid] = row

        recs: list[SportActionableRecommendation] = []
        for row in seen.values():
            rec = self._edge_dict_to_recommendation(row, row.get("match_id", ""))
            if rec is None:
                continue
            recs.append(rec)
            if len(recs) >= limit:
                break
        return recs

    def _edge_dict_to_recommendation(
        self, edge: dict[str, Any], match_id: str
    ) -> SportActionableRecommendation | None:
        """Convert a persisted edge row dict to SportActionableRecommendation."""
        raw_edge = float(edge.get("raw_edge", 0.0))
        adjusted_edge = float(edge.get("adjusted_edge", 0.0))
        trust = float(edge.get("trust", 0.5))
        liquidity_factor = float(edge.get("liquidity_factor", 1.0))
        stale = bool(edge.get("stale", False))

        # Convert B's 0-1 scale to 0-100 pp
        adjusted_edge_pct = adjusted_edge * 100
        raw_edge_pct = raw_edge * 100

        # Determine qualified from calibration
        qualified, engine_name, competition, prediction_timestamp = self._get_metadata(match_id)

        # Risk level
        risk_level = _compute_risk_level(liquidity_factor, trust, stale)

        # Direction
        direction = _derive_direction(raw_edge, stale, risk_level)

        # Decision gate (reuse diagnosis_service.decide)
        decision = decide(
            adjusted_edge=adjusted_edge_pct,
            qualified=qualified,
            act_edge=config.settings.DECISION_ACT_EDGE,
            watch_edge=config.settings.DECISION_WATCH_EDGE,
            cold_start_bypass_enabled=config.settings.COLD_START_BYPASS_ENABLED,
        )

        # Confidence
        confidence = _compute_confidence(adjusted_edge_pct, trust)

        # Allocation
        allocation = _compute_allocation(adjusted_edge_pct, risk_level, decision)

        # Calibration status
        calibration_status = "calibrated" if qualified else "uncalibrated_provisional"

        # Rationale
        rationale = _build_rationale(
            direction=direction,
            mapped_outcome=edge.get("mapped_outcome", ""),
            edge_pct=adjusted_edge_pct,
            trust=trust,
            liquidity_factor=liquidity_factor,
            stale=stale,
            decision=decision,
        )

        captured_at = edge.get("captured_at")
        if captured_at is None:
            captured_at = datetime.utcnow()

        return SportActionableRecommendation(
            match_id=match_id,
            mapped_outcome=edge.get("mapped_outcome", ""),
            direction=direction,
            decision=decision,
            confidence=confidence,
            risk_level=risk_level,
            edge_pct=round(adjusted_edge_pct, 2),
            raw_edge_pct=round(raw_edge_pct, 2),
            trust=trust,
            liquidity_factor=liquidity_factor,
            stale=stale,
            suggested_allocation_pct=allocation,
            calibration_status=calibration_status,
            rationale=rationale,
            engine_name=engine_name,
            competition=competition,
            prediction_timestamp=prediction_timestamp,
            model_prob=float(edge.get("model_prob", 0.0)),
            market_prob=float(edge.get("market_prob", 0.0)),
            sources_count=int(edge.get("sources_count", 0)),
            captured_at=captured_at,
        )

    def _get_metadata(
        self, match_id: str
    ) -> tuple[bool, str | None, str | None, datetime | None]:
        """Query KernelPrediction + KernelCalibration for metadata.

        Returns (qualified, engine_name, competition, prediction_timestamp).
        When prediction is gone, returns (False, None, None, None).
        """
        pred = get_latest_prediction(match_id)
        if pred is None:
            return (False, None, None, None)

        calibration = get_calibration(pred.engine, pred.competition)
        if calibration is None:
            return (False, pred.engine, pred.competition, pred.created_at)

        qualified = calibration.sample_count >= config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
        return (qualified, pred.engine, pred.competition, pred.created_at)
```

- [ ] **Step 5: Run pure function tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_service.py -v --no-header -k "direction or risk_level or confidence or allocation or rationale or select_primary" 2>&1 | tail -30`
Expected: PASS for all ~20 pure function tests

- [ ] **Step 6: Add DB-integrated tests**

Append to `backend/tests/test_sport_recommendation_service.py`:

```python
# --- DB-integrated tests (require kernel_db fixture) ---

def test_get_recommendation_returns_none_when_no_edges(service):
    assert service.get_recommendation("nonexistent") is None


def test_get_recommendation_returns_rec(service):
    _seed_prediction(match_id="m1")
    _seed_calibration()
    _seed_edge(match_id="m1", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    rec = service.get_recommendation("m1")
    assert rec is not None
    assert rec.match_id == "m1"
    assert rec.mapped_outcome == "home_win"
    assert rec.direction == "YES"
    assert rec.edge_pct == pytest.approx(7.2, abs=0.01)
    assert rec.engine_name == "BasketballEngine"
    assert rec.competition == "nba"
    assert rec.calibration_status == "calibrated"


def test_get_recommendation_qualified_false_when_no_calibration(service):
    _seed_prediction(match_id="m2", engine="NewEngine", competition="new")
    # No calibration row for NewEngine/new
    _seed_edge(match_id="m2", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.05)
    rec = service.get_recommendation("m2")
    assert rec is not None
    assert rec.calibration_status == "uncalibrated_provisional"
    assert rec.decision in ("provisional_act", "watch")  # cold_start_bypass


def test_get_recommendation_stale_returns_wait(service):
    _seed_prediction(match_id="m3")
    _seed_calibration()
    _seed_edge(match_id="m3", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072, stale=True)
    rec = service.get_recommendation("m3")
    assert rec is not None
    assert rec.direction == "WAIT"
    assert "数据过期" in rec.rationale


def test_get_recommendation_negative_edge_returns_no(service):
    _seed_prediction(match_id="m4", probs={"home_win": 0.4, "away_win": 0.6})
    _seed_calibration()
    _seed_edge(match_id="m4", mapped_outcome="home_win", raw_edge=-0.10, adjusted_edge=-0.072)
    rec = service.get_recommendation("m4")
    assert rec is not None
    assert rec.direction == "NO"


def test_get_recommendation_picks_primary_outcome(service):
    """When multiple outcomes exist, picks the one with max |adjusted_edge|."""
    _seed_prediction(match_id="m5", probs={"home_win": 0.6, "away_win": 0.4})
    _seed_calibration()
    _seed_edge(match_id="m5", mapped_outcome="home_win", raw_edge=0.03, adjusted_edge=0.022)
    _seed_edge(match_id="m5", mapped_outcome="away_win", raw_edge=-0.08, adjusted_edge=-0.058)
    rec = service.get_recommendation("m5")
    assert rec is not None
    assert rec.mapped_outcome == "away_win"  # larger |adjusted_edge|


def test_get_open_decisions_excludes_skip(service):
    _seed_prediction(match_id="m6")
    _seed_calibration()
    # Small edge → skip decision
    _seed_edge(match_id="m6", mapped_outcome="home_win", raw_edge=0.001, adjusted_edge=0.0007)
    recs = service.get_open_decisions(limit=10)
    match_ids = [r.match_id for r in recs]
    assert "m6" not in match_ids  # skip excluded


def test_get_open_decisions_includes_act(service):
    _seed_prediction(match_id="m7")
    _seed_calibration()
    # Large edge → act decision (0.10 * 0.72 * 1.0 = 0.072 → 7.2pp >= 6.0)
    _seed_edge(match_id="m7", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    recs = service.get_open_decisions(limit=10)
    match_ids = [r.match_id for r in recs]
    assert "m7" in match_ids


def test_get_open_decisions_filters_by_decision(service):
    _seed_prediction(match_id="m8")
    _seed_calibration()
    _seed_edge(match_id="m8", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    recs = service.get_open_decisions(limit=10, decision="act")
    assert all(r.decision == "act" for r in recs)
    assert any(r.match_id == "m8" for r in recs)


def test_get_top_picks_includes_skip(service):
    _seed_prediction(match_id="m9")
    _seed_calibration()
    _seed_edge(match_id="m9", mapped_outcome="home_win", raw_edge=0.001, adjusted_edge=0.0007)
    recs = service.get_top_picks(limit=10)
    match_ids = [r.match_id for r in recs]
    assert "m9" in match_ids  # includes skip decisions


def test_get_top_picks_respects_min_abs_edge(service):
    _seed_prediction(match_id="m10")
    _seed_calibration()
    _seed_edge(match_id="m10", mapped_outcome="home_win", raw_edge=0.001, adjusted_edge=0.0007)
    # min_abs_edge_pct=1.0 means adjusted_edge >= 0.01 (0.01 * 100 = 1.0pp)
    recs = service.get_top_picks(limit=10, min_abs_edge_pct=1.0)
    match_ids = [r.match_id for r in recs]
    assert "m10" not in match_ids  # 0.0007 < 0.01


def test_get_open_decisions_deduplicates_by_match(service):
    """Multiple outcomes for same match → only 1 recommendation per match."""
    _seed_prediction(match_id="m11", probs={"home_win": 0.6, "away_win": 0.4})
    _seed_calibration()
    _seed_edge(match_id="m11", mapped_outcome="home_win", raw_edge=0.10, adjusted_edge=0.072)
    _seed_edge(match_id="m11", mapped_outcome="away_win", raw_edge=-0.08, adjusted_edge=-0.058)
    recs = service.get_open_decisions(limit=10)
    match_count = sum(1 for r in recs if r.match_id == "m11")
    assert match_count == 1
```

- [ ] **Step 7: Run all service tests**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_service.py -v --no-header 2>&1 | tail -40`
Expected: PASS for all ~32 tests

- [ ] **Step 8: Run regression tests**

Run: `cd backend && python -m pytest tests/test_edge_detector_service.py tests/test_edge_store.py tests/test_sport_edge_routes.py tests/test_sport_edge_cli.py -v --no-header 2>&1 | tail -20`
Expected: PASS (zero regression on Subproject B)

- [ ] **Step 9: Commit**

```bash
git add backend/app/core/config.py backend/app/kernel/sport_recommendation_service.py backend/tests/test_sport_recommendation_service.py
git commit -m "feat(phase7-c): add SportRecommendationService with direction/decision/confidence/risk computation"
```

---

## Task 2: API Routes + Router Registration

**Files:**
- Create: `backend/app/api/routes/sport_recommendations.py`
- Modify: `backend/app/api/router.py` (line 3 — add import; after line 14 — add include_router)
- Test: `backend/tests/test_sport_recommendation_routes.py`

**Interfaces:**
- Consumes: `SportRecommendationService.get_recommendation(match_id)`, `get_open_decisions(limit, decision)`, `get_top_picks(limit, min_abs_edge_pct)`
- Produces: 3 GET endpoints at `/api/sport-recommendations/{match_id}`, `/open`, `/discrepancies`

- [ ] **Step 1: Write the failing route tests**

Create `backend/tests/test_sport_recommendation_routes.py`:

```python
"""Tests for sport recommendation API routes.

All endpoints gated by PHASE7_SPORT_RECOMMENDATION_ENABLED (503 when false).
All are GET (read-only) — no require_write_key auth.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "rec_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_RECOMMENDATION_ENABLED", True)
    from app.api.routes import sport_recommendations
    app = FastAPI()
    app.include_router(sport_recommendations.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_RECOMMENDATION_ENABLED", False)
    from app.api.routes import sport_recommendations
    app = FastAPI()
    app.include_router(sport_recommendations.router, prefix="/api")
    return TestClient(app)


def _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072):
    """Helper: seed prediction + calibration + edge row."""
    from datetime import datetime, timezone
    from app.kernel.kernel_db import KernelPrediction, KernelCalibration, get_kernel_session
    from app.kernel.edge_store import EdgeStore
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
            confidence=0.7, feature_version="nba-1.0", explanation={},
            created_at=now, updated_at=now,
        ))
        existing_cal = (
            session.query(KernelCalibration)
            .filter_by(engine="BasketballEngine", competition="nba")
            .one_or_none()
        )
        if existing_cal is None:
            session.add(KernelCalibration(
                engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
                sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
            ))
        session.commit()
    finally:
        session.close()
    EdgeStore().append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=implied,
        raw_edge=0.65 - implied, trust=0.72, liquidity_factor=1.0,
        adjusted_edge=adjusted_edge, spread=None, sources_count=1,
        stale=False, captured_at=now,
    )


def test_get_recommendation_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-recommendations/m1")
    assert res.status_code == 503


def test_get_recommendation_returns_404_when_no_edges(client):
    res = client.get("/api/sport-recommendations/nonexistent")
    assert res.status_code == 404


def test_get_recommendation_returns_rec(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["mapped_outcome"] == "home_win"
    assert data["direction"] == "YES"
    assert data["edge_pct"] == pytest.approx(7.2, abs=0.01)
    assert data["engine_name"] == "BasketballEngine"
    assert data["competition"] == "nba"
    assert "rationale" in data
    assert "仅供参考" in data["rationale"]


def test_open_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-recommendations/open")
    assert res.status_code == 503


def test_open_returns_list(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/open")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert any(item["match_id"] == "m1" for item in data["items"])


def test_open_filters_by_decision(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/open", params={"decision": "act"})
    assert res.status_code == 200
    data = res.json()
    assert all(item["decision"] == "act" for item in data["items"])


def test_discrepancies_returns_top_picks(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/discrepancies")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert data["items"][0]["match_id"] == "m1"


def test_discrepancies_respects_min_abs_edge(client):
    # Small edge → below threshold
    _seed_prediction_and_edge(match_id="m2", implied=0.64, adjusted_edge=0.0007)
    res = client.get("/api/sport-recommendations/discrepancies", params={"min_abs_edge": 0.05})
    assert res.status_code == 200
    data = res.json()
    match_ids = [item["match_id"] for item in data["items"]]
    assert "m2" not in match_ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_routes.py -v --no-header 2>&1 | head -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.sport_recommendations'`

- [ ] **Step 3: Create the route file**

Create `backend/app/api/routes/sport_recommendations.py`:

```python
"""Sport recommendation API routes.

When PHASE7_SPORT_RECOMMENDATION_ENABLED is false, all routes return 503.
All endpoints are GET (read-only) — no require_write_key auth (consistent
with Subproject B's GET endpoints).

Stateless: each request computes the recommendation on-demand from B's
persisted edges. No caching, no scheduler.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core import config

router = APIRouter(prefix="/sport-recommendations", tags=["Sport Recommendations"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_RECOMMENDATION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Sport recommendations are disabled. Set PHASE7_SPORT_RECOMMENDATION_ENABLED=true to enable.",
        )


def _service():
    from app.kernel.sport_recommendation_service import SportRecommendationService
    return SportRecommendationService()


def _rec_to_dict(rec) -> dict[str, Any]:
    """Serialize a SportActionableRecommendation to a JSON-friendly dict."""
    return {
        "match_id": rec.match_id,
        "mapped_outcome": rec.mapped_outcome,
        "direction": rec.direction,
        "decision": rec.decision,
        "confidence": rec.confidence,
        "risk_level": rec.risk_level,
        "edge_pct": rec.edge_pct,
        "raw_edge_pct": rec.raw_edge_pct,
        "trust": rec.trust,
        "liquidity_factor": rec.liquidity_factor,
        "stale": rec.stale,
        "suggested_allocation_pct": rec.suggested_allocation_pct,
        "calibration_status": rec.calibration_status,
        "rationale": rec.rationale,
        "engine_name": rec.engine_name,
        "competition": rec.competition,
        "prediction_timestamp": rec.prediction_timestamp.isoformat() if rec.prediction_timestamp else None,
        "model_prob": rec.model_prob,
        "market_prob": rec.market_prob,
        "sources_count": rec.sources_count,
        "captured_at": rec.captured_at.isoformat() if rec.captured_at else None,
    }


@router.get("/{match_id}")
def get_recommendation(match_id: str) -> dict[str, Any]:
    """Single match recommendation. Returns 404 when no edges exist."""
    _ensure_enabled()
    svc = _service()
    rec = svc.get_recommendation(match_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No edges found for match.")
    return _rec_to_dict(rec)


@router.get("/open")
def get_open_decisions(
    limit: int = Query(20, ge=1, le=100),
    decision: str | None = Query(None, pattern="^(act|provisional_act|watch)$"),
) -> dict[str, Any]:
    """Open decisions list (excludes skip). Filterable by decision type."""
    _ensure_enabled()
    svc = _service()
    recs = svc.get_open_decisions(limit=limit, decision=decision)
    return {"items": [_rec_to_dict(r) for r in recs], "total": len(recs)}


@router.get("/discrepancies")
def get_top_picks(
    limit: int = Query(20, ge=1, le=100),
    min_abs_edge: float = Query(0.0, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Top edge picks (all decisions). min_abs_edge is on 0-1 scale (B's convention)."""
    _ensure_enabled()
    svc = _service()
    recs = svc.get_top_picks(limit=limit, min_abs_edge_pct=min_abs_edge * 100)
    return {"items": [_rec_to_dict(r) for r in recs], "total": len(recs)}
```

- [ ] **Step 4: Register the router**

In `backend/app/api/router.py`:

Line 3 — add `sport_recommendations` to the import:
```python
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges, sport_recommendations
```

After line 14 — add the include_router call:
```python
api_router.include_router(sport_recommendations.router, tags=["Sport Recommendations"])
```

- [ ] **Step 5: Run route tests**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_routes.py -v --no-header 2>&1 | tail -20`
Expected: PASS for all 8 tests

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/sport_recommendations.py backend/app/api/router.py backend/tests/test_sport_recommendation_routes.py
git commit -m "feat(phase7-c): add 3 sport-recommendations API endpoints (match/open/discrepancies)"
```

---

## Task 3: CLI Tool

**Files:**
- Create: `backend/scripts/sport_recommendation_cli.py`
- Test: `backend/tests/test_sport_recommendation_cli.py`

**Interfaces:**
- Consumes: `SportRecommendationService.get_recommendation(match_id)`, `get_open_decisions(limit, decision)`, `get_top_picks(limit, min_abs_edge_pct)`
- Produces: CLI with `match`, `open`, `picks` subcommands

- [ ] **Step 1: Write the failing CLI tests**

Create `backend/tests/test_sport_recommendation_cli.py`:

```python
"""Tests for sport_recommendation_cli.

Follows test_sport_edge_cli.py pattern — tests main() function directly.
"""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db, get_kernel_session
from app.kernel.kernel_db import KernelPrediction, KernelCalibration
from app.kernel.edge_store import EdgeStore
from datetime import datetime, timezone


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    db_path = tmp_path / "rec_cli_test.db"
    # Patch the DB path before init_kernel_db is called by the CLI
    monkeypatch.setenv("KERNEL_DB_PATH", str(db_path))
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed_data(match_id="m1"):
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
            confidence=0.7, feature_version="nba-1.0", explanation={},
            created_at=now, updated_at=now,
        ))
        existing = session.query(KernelCalibration).filter_by(
            engine="BasketballEngine", competition="nba"
        ).one_or_none()
        if existing is None:
            session.add(KernelCalibration(
                engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
                sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
            ))
        session.commit()
    finally:
        session.close()
    EdgeStore().append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=0.50, raw_edge=0.15, trust=0.72,
        liquidity_factor=1.0, adjusted_edge=0.108, spread=None,
        sources_count=1, stale=False, captured_at=now,
    )


def test_cli_match_command(kernel_db):
    _seed_data("m1")
    from scripts.sport_recommendation_cli import main
    code = main(["match", "--match-id", "m1"])
    assert code == 0


def test_cli_open_command(kernel_db):
    _seed_data("m1")
    from scripts.sport_recommendation_cli import main
    code = main(["open", "--limit", "10"])
    assert code == 0


def test_cli_picks_command(kernel_db):
    _seed_data("m1")
    from scripts.sport_recommendation_cli import main
    code = main(["picks", "--limit", "10"])
    assert code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_cli.py -v --no-header 2>&1 | head -15`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.sport_recommendation_cli'`

- [ ] **Step 3: Create the CLI file**

Create `backend/scripts/sport_recommendation_cli.py`:

```python
"""Sport recommendation CLI.

Usage:
    python -m scripts.sport_recommendation_cli match --match-id ID
    python -m scripts.sport_recommendation_cli open [--limit N] [--decision act|provisional_act|watch]
    python -m scripts.sport_recommendation_cli picks [--limit N] [--min-abs-edge F]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_match(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_recommendation_service import SportRecommendationService
    init_kernel_db()
    svc = SportRecommendationService()
    rec = svc.get_recommendation(args.match_id)
    if rec is None:
        _print(f"[INFO] no edges found for match={args.match_id}")
        return 0
    _print(f"[OK] match={args.match_id}")
    _print(f"  outcome={rec.mapped_outcome} direction={rec.direction} decision={rec.decision}")
    _print(f"  edge={rec.edge_pct:+.2f}pp raw_edge={rec.raw_edge_pct:+.2f}pp")
    _print(f"  confidence={rec.confidence} risk={rec.risk_level} trust={rec.trust:.2f}")
    _print(f"  allocation={rec.suggested_allocation_pct}% calibration={rec.calibration_status}")
    _print(f"  engine={rec.engine_name} competition={rec.competition}")
    _print(f"  rationale: {rec.rationale}")
    return 0


def _cmd_open(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_recommendation_service import SportRecommendationService
    init_kernel_db()
    svc = SportRecommendationService()
    recs = svc.get_open_decisions(limit=args.limit, decision=args.decision)
    if not recs:
        _print("[INFO] no open decisions found")
        return 0
    _print(f"[OK] {len(recs)} open decisions (limit={args.limit}, decision={args.decision}):")
    for rec in recs:
        _print(
            f"  match={rec.match_id:<24} outcome={rec.mapped_outcome:<10} "
            f"dir={rec.direction:<6} decision={rec.decision:<14} "
            f"edge={rec.edge_pct:+.2f}pp"
        )
    return 0


def _cmd_picks(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_recommendation_service import SportRecommendationService
    init_kernel_db()
    svc = SportRecommendationService()
    recs = svc.get_top_picks(limit=args.limit, min_abs_edge_pct=args.min_abs_edge * 100)
    if not recs:
        _print("[INFO] no picks found")
        return 0
    _print(f"[OK] {len(recs)} picks (limit={args.limit}, min_abs_edge={args.min_abs_edge}):")
    for rec in recs:
        _print(
            f"  match={rec.match_id:<24} outcome={rec.mapped_outcome:<10} "
            f"dir={rec.direction:<6} decision={rec.decision:<14} "
            f"edge={rec.edge_pct:+.2f}pp"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport recommendation CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_match = sub.add_parser("match", help="show recommendation for a single match")
    p_match.add_argument("--match-id", required=True)
    p_match.set_defaults(func=_cmd_match)

    p_open = sub.add_parser("open", help="list open decisions (act/provisional_act/watch)")
    p_open.add_argument("--limit", type=int, default=20)
    p_open.add_argument("--decision", default=None, choices=["act", "provisional_act", "watch"])
    p_open.set_defaults(func=_cmd_open)

    p_picks = sub.add_parser("picks", help="list top edge picks (all decisions)")
    p_picks.add_argument("--limit", type=int, default=20)
    p_picks.add_argument("--min-abs-edge", type=float, default=0.0, dest="min_abs_edge")
    p_picks.set_defaults(func=_cmd_picks)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run CLI tests**

Run: `cd backend && python -m pytest tests/test_sport_recommendation_cli.py -v --no-header 2>&1 | tail -15`
Expected: PASS for all 3 tests

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/sport_recommendation_cli.py backend/tests/test_sport_recommendation_cli.py
git commit -m "feat(phase7-c): add sport_recommendation_cli with match/open/picks commands"
```

---

## Task 4: Frontend (API Client + Page + Components + Nav)

**Files:**
- Create: `frontend/src/lib/sport-recommendations-api.ts`
- Create: `frontend/src/app/sports/recommendations/page.tsx`
- Create: `frontend/src/components/sports/recommendations/RecommendationCard.tsx`
- Create: `frontend/src/components/sports/recommendations/OpenDecisionsList.tsx`
- Modify: `frontend/src/components/app-nav.tsx` (line 21 — add nav entry)
- Test: `frontend/src/components/sports/recommendations/RecommendationCard.test.tsx`
- Test: `frontend/src/components/sports/recommendations/OpenDecisionsList.test.tsx`

**Interfaces:**
- Consumes: `/api/sport-recommendations/{match_id}`, `/api/sport-recommendations/open`, `/api/sport-recommendations/discrepancies`
- Produces: `/sports/recommendations` page with open decisions list and recommendation cards

- [ ] **Step 1: Create the API client**

Create `frontend/src/lib/sport-recommendations-api.ts`:

```typescript
import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface SportRecommendation {
  match_id: string;
  mapped_outcome: string;
  direction: string;
  decision: string;
  confidence: string;
  risk_level: string;
  edge_pct: number;
  raw_edge_pct: number;
  trust: number;
  liquidity_factor: number;
  stale: boolean;
  suggested_allocation_pct: number;
  calibration_status: string;
  rationale: string;
  engine_name: string | null;
  competition: string | null;
  prediction_timestamp: string | null;
  model_prob: number;
  market_prob: number;
  sources_count: number;
  captured_at: string | null;
}

export interface RecommendationList {
  items: SportRecommendation[];
  total: number;
}

function buildQuery(params: Record<string, string | number | undefined | boolean>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

export async function fetchRecommendation(matchId: string): Promise<SportRecommendation> {
  const res = await fetch(`${API_BASE}/api/sport-recommendations/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch recommendation");
  return res.json();
}

export async function fetchOpenDecisions(params?: {
  limit?: number;
  decision?: string;
}): Promise<RecommendationList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/sport-recommendations/open${qs}`);
  if (!res.ok) throw new Error("Failed to fetch open decisions");
  return res.json();
}

export async function fetchTopPicks(params?: {
  limit?: number;
  min_abs_edge?: number;
}): Promise<RecommendationList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/sport-recommendations/discrepancies${qs}`);
  if (!res.ok) throw new Error("Failed to fetch top picks");
  return res.json();
}
```

- [ ] **Step 2: Create RecommendationCard component**

Create `frontend/src/components/sports/recommendations/RecommendationCard.tsx`:

```tsx
import type { SportRecommendation } from "@/lib/sport-recommendations-api";

const DIRECTION_STYLES: Record<string, string> = {
  YES: "bg-green-100 text-green-800",
  NO: "bg-red-100 text-red-800",
  WAIT: "bg-gray-100 text-gray-800",
  AVOID: "bg-orange-100 text-orange-800",
};

const DECISION_LABELS: Record<string, string> = {
  act: "行动",
  provisional_act: "临时行动",
  watch: "观察",
  skip: "跳过",
};

const OUTCOME_LABELS: Record<string, string> = {
  home_win: "主胜",
  draw: "平局",
  away_win: "客胜",
};

export function RecommendationCard({
  rec,
  summary = false,
}: {
  rec: SportRecommendation;
  summary?: boolean;
}) {
  // In summary mode, hide AVOID (inherit event-pipeline decision-card.tsx pattern)
  if (summary && rec.direction === "AVOID") {
    return null;
  }

  return (
    <div
      data-testid={`rec-card-${rec.match_id}`}
      className="rounded-lg border border-border bg-card p-4 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <span
          data-testid={`direction-${rec.match_id}`}
          className={`rounded px-2 py-0.5 text-xs font-medium ${DIRECTION_STYLES[rec.direction] ?? "bg-gray-100"}`}
        >
          {rec.direction}
        </span>
        <span className="text-xs text-muted-foreground">
          {OUTCOME_LABELS[rec.mapped_outcome] ?? rec.mapped_outcome}
        </span>
        <span className="text-xs text-muted-foreground">
          {DECISION_LABELS[rec.decision] ?? rec.decision}
        </span>
        <span className="ml-auto font-mono text-sm font-semibold" data-testid={`edge-${rec.match_id}`}>
          {rec.edge_pct > 0 ? "+" : ""}{rec.edge_pct.toFixed(2)}pp
        </span>
      </div>
      {!summary && (
        <div className="mt-3 space-y-1 text-xs text-muted-foreground">
          <div data-testid={`confidence-${rec.match_id}`}>
            置信度: {rec.confidence} | 风险: {rec.risk_level} | trust: {rec.trust.toFixed(2)}
          </div>
          {rec.suggested_allocation_pct > 0 && (
            <div data-testid={`allocation-${rec.match_id}`}>
              建议仓位: {rec.suggested_allocation_pct}%
            </div>
          )}
          <div data-testid={`rationale-${rec.match_id}`} className="text-foreground">
            {rec.rationale}
          </div>
          {rec.engine_name && (
            <div>引擎: {rec.engine_name} | 赛事: {rec.competition ?? "—"}</div>
          )}
        </div>
      )}
      {summary && (
        <div className="mt-2 truncate text-xs text-muted-foreground" data-testid={`rationale-summary-${rec.match_id}`}>
          {rec.rationale}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create OpenDecisionsList component**

Create `frontend/src/components/sports/recommendations/OpenDecisionsList.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import { fetchOpenDecisions, type SportRecommendation } from "@/lib/sport-recommendations-api";
import { RecommendationCard } from "./RecommendationCard";

type DecisionFilter = "all" | "act" | "provisional_act" | "watch";

export function OpenDecisionsList() {
  const [recs, setRecs] = useState<SportRecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<DecisionFilter>("all");

  useEffect(() => {
    setLoading(true);
    setError(null);
    const decision = filter === "all" ? undefined : filter;
    fetchOpenDecisions({ limit: 50, decision })
      .then((data) => {
        setRecs(data.items);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [filter]);

  if (loading) {
    return <div data-testid="loading">加载中...</div>;
  }
  if (error) {
    return <div data-testid="error">错误: {error}</div>;
  }
  if (recs.length === 0) {
    return <div data-testid="empty">暂无开放决策</div>;
  }

  return (
    <div data-testid="open-decisions-list">
      <div className="mb-4 flex gap-2">
        {(["all", "act", "provisional_act", "watch"] as DecisionFilter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded px-3 py-1 text-xs ${filter === f ? "bg-secondary" : "bg-muted"}`}
            data-testid={`filter-${f}`}
          >
            {f === "all" ? "全部" : f === "act" ? "行动" : f === "provisional_act" ? "临时行动" : "观察"}
          </button>
        ))}
      </div>
      <div className="space-y-3">
        {recs.map((rec) => (
          <RecommendationCard key={rec.match_id} rec={rec} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create the page**

Create `frontend/src/app/sports/recommendations/page.tsx`:

```tsx
"use client";
import { OpenDecisionsList } from "@/components/sports/recommendations/OpenDecisionsList";

export default function SportRecommendationsPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育推荐</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        基于 Subproject B 的 edge 数据，实时计算 act/watch/skip 决策建议。
      </p>
      <div className="mt-6">
        <OpenDecisionsList />
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Add navigation entry**

In `frontend/src/components/app-nav.tsx`, after line 21 (`/sports/markets`), add:

```typescript
  { href: "/sports/recommendations", label: "体育推荐", icon: Target, match: ["/sports/recommendations"] },
```

Note: `Target` is already imported on line 5.

- [ ] **Step 6: Write RecommendationCard tests**

Create `frontend/src/components/sports/recommendations/RecommendationCard.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RecommendationCard } from "./RecommendationCard";
import type { SportRecommendation } from "@/lib/sport-recommendations-api";

const baseRec: SportRecommendation = {
  match_id: "m1",
  mapped_outcome: "home_win",
  direction: "YES",
  decision: "act",
  confidence: "high",
  risk_level: "low",
  edge_pct: 7.2,
  raw_edge_pct: 10.0,
  trust: 0.72,
  liquidity_factor: 1.0,
  stale: false,
  suggested_allocation_pct: 2.0,
  calibration_status: "calibrated",
  rationale: "模型看好主胜，调整后边际 +7.20pp。决策建议：act。本分析仅供参考，不构成投资建议。",
  engine_name: "BasketballEngine",
  competition: "nba",
  prediction_timestamp: "2026-07-16T10:00:00Z",
  model_prob: 0.65,
  market_prob: 0.55,
  sources_count: 1,
  captured_at: "2026-07-16T10:00:00Z",
};

describe("RecommendationCard", () => {
  it("renders direction badge", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("direction-m1").textContent).toBe("YES");
  });

  it("renders edge with + sign for positive", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("edge-m1").textContent).toBe("+7.20pp");
  });

  it("renders rationale in expanded mode", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("rationale-m1").textContent).toContain("主胜");
    expect(screen.getByTestId("rationale-m1").textContent).toContain("仅供参考");
  });

  it("renders allocation when > 0", () => {
    render(<RecommendationCard rec={baseRec} />);
    expect(screen.getByTestId("allocation-m1").textContent).toContain("2%");
  });

  it("hides AVOID in summary mode", () => {
    const avoidRec = { ...baseRec, direction: "AVOID" };
    const { container } = render(<RecommendationCard rec={avoidRec} summary={true} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 7: Write OpenDecisionsList tests**

Create `frontend/src/components/sports/recommendations/OpenDecisionsList.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { OpenDecisionsList } from "./OpenDecisionsList";
import type { RecommendationList } from "@/lib/sport-recommendations-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({ fetchOpenDecisions: vi.fn() }));
vi.mock("@/lib/sport-recommendations-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-recommendations-api")>()),
  fetchOpenDecisions: apiMocks.fetchOpenDecisions,
}));

const mockData: RecommendationList = {
  items: [
    {
      match_id: "m1",
      mapped_outcome: "home_win",
      direction: "YES",
      decision: "act",
      confidence: "high",
      risk_level: "low",
      edge_pct: 7.2,
      raw_edge_pct: 10.0,
      trust: 0.72,
      liquidity_factor: 1.0,
      stale: false,
      suggested_allocation_pct: 2.0,
      calibration_status: "calibrated",
      rationale: "模型看好主胜",
      engine_name: "BasketballEngine",
      competition: "nba",
      prediction_timestamp: "2026-07-16T10:00:00Z",
      model_prob: 0.65,
      market_prob: 0.55,
      sources_count: 1,
      captured_at: "2026-07-16T10:00:00Z",
    },
  ],
  total: 1,
};

describe("OpenDecisionsList", () => {
  it("renders list after load", async () => {
    apiMocks.fetchOpenDecisions.mockResolvedValue(mockData);
    render(<OpenDecisionsList />);
    await waitFor(() =>
      expect(screen.getByTestId("open-decisions-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("rec-card-m1")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchOpenDecisions.mockResolvedValue({ items: [], total: 0 });
    render(<OpenDecisionsList />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchOpenDecisions.mockRejectedValue(new Error("boom"));
    render(<OpenDecisionsList />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("renders filter buttons", async () => {
    apiMocks.fetchOpenDecisions.mockResolvedValue(mockData);
    render(<OpenDecisionsList />);
    await waitFor(() =>
      expect(screen.getByTestId("open-decisions-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("filter-all")).toBeInTheDocument();
    expect(screen.getByTestId("filter-act")).toBeInTheDocument();
  });
});
```

- [ ] **Step 8: Run frontend tests**

Run: `cd frontend && npx vitest run src/components/sports/recommendations/ 2>&1 | tail -20`
Expected: PASS for all 9 tests

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/sport-recommendations-api.ts frontend/src/app/sports/recommendations/page.tsx frontend/src/components/sports/recommendations/ frontend/src/components/app-nav.tsx
git commit -m "feat(phase7-c): add sport recommendations frontend (API client + page + components + nav)"
```

---

## Final Verification

- [ ] **Step 1: Run all Subproject C tests**

```bash
cd backend && python -m pytest tests/test_sport_recommendation_service.py tests/test_sport_recommendation_routes.py tests/test_sport_recommendation_cli.py -v --no-header 2>&1 | tail -30
```
Expected: All ~43 tests PASS

- [ ] **Step 2: Run Subproject B regression tests**

```bash
cd backend && python -m pytest tests/test_edge_detector_service.py tests/test_edge_store.py tests/test_sport_edge_routes.py tests/test_sport_edge_cli.py tests/test_edge_db.py tests/test_link_store_matches.py -v --no-header 2>&1 | tail -20
```
Expected: All B tests PASS (zero regression)

- [ ] **Step 3: Run frontend tests**

```bash
cd frontend && npx vitest run src/components/sports/recommendations/ 2>&1 | tail -15
```
Expected: All 9 frontend tests PASS

- [ ] **Step 4: Run diagnosis_service regression**

```bash
cd backend && python -m pytest tests/test_diagnosis_service.py -v --no-header 2>&1 | tail -15
```
Expected: All diagnosis_service tests PASS (zero regression — C only calls `decide()` as a pure function)

---

## Summary

| Task | Files | Tests | Key Deliverable |
|------|-------|-------|-----------------|
| 1 | config.py, sport_recommendation_service.py, test_sport_recommendation_service.py | ~32 | Stateless service with direction/decision/confidence/risk/allocation computation |
| 2 | sport_recommendations.py, router.py, test_sport_recommendation_routes.py | 8 | 3 GET endpoints (match/open/discrepancies) |
| 3 | sport_recommendation_cli.py, test_sport_recommendation_cli.py | 3 | CLI with match/open/picks subcommands |
| 4 | sport-recommendations-api.ts, page.tsx, RecommendationCard.tsx, OpenDecisionsList.tsx, app-nav.tsx, 2 test files | 9 | Frontend page with filterable open decisions list |
| **Total** | 15 files (11 new + 4 modified) | ~52 | Complete stateless recommendation layer |
