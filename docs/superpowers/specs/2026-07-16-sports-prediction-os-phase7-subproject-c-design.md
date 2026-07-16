# Sports Prediction OS — Phase 7 Subproject C: Sport Recommendation Engine

Date: 2026-07-16

## Goal

Build the recommendation layer that consumes Subproject B's edge data (`EdgeResult` / `EdgeDetectionSummary`) and produces structured, actionable recommendations for sports matches. Each recommendation carries a direction (YES/NO/AVOID/WAIT), a decision gate (act/provisional_act/watch/skip), confidence, risk level, suggested allocation, and a Chinese rationale.

**Subproject C deliverable:** `SportRecommendationService` (stateless) + `SportActionableRecommendation` dataclass + 3 API endpoints + frontend `/sports/recommendations` page + CLI. Produces per-match recommendations that close the Phase 7 "Full-Stack Fusion" decision layer.

**Consumed by:** Frontend recommendations page, CLI tools, and (future) Subproject D market calibration feedback loop.

## Background: Current State

| Dimension | Prediction-Market Pipeline (events) | Sports Kernel (Phase 1-6) | Subproject B (done) |
|-----------|--------------------------------------|---------------------------|---------------------|
| Edge data | `raw_edge` / `adjusted_edge` (0-100 pp) | None | `EdgeResult.adjusted_edge` (0-1 scale) |
| Decision gate | `diagnosis_service.decide()` → act/watch/skip | None | None (B explicitly defers to C) |
| Recommendation | `ActionableRecommendation` (YES/NO/AVOID/WAIT) | None | None (B explicitly defers to C) |
| Calibration source | `calibration_service_event.skill_score` | `KernelCalibration.avg_accuracy` | `EdgeResult.trust` (0-1, computed from `KernelCalibration`) |

**Core gap:** Subproject B produces raw edge data but explicitly does NOT produce decisions or recommendations (B spec non-goals lines 49-52, global constraints 11-12). The sports pipeline has no equivalent of the event pipeline's `ActionableRecommendation`. Subproject C closes this gap by building a stateless recommendation layer on top of B's persisted edges.

## Non-goals

- Do NOT modify Subproject B's code (`edge_detector_service.py`, `edge_store.py`, `sport_edges.py` routes, `kernel_sport_edges` table).
- Do NOT modify the event pipeline's `ActionableRecommendation` model (`backend/app/models/event.py`), its producer (`event_intelligence_service._build_actionable_recommendation`), or its consumers (`decision_report_service`, `decision-card.tsx`).
- Do NOT modify `diagnosis_service.py` — C calls `decide()` as a pure function, no changes to the function itself.
- Do NOT modify `decision_quality_service.py` — it depends on `evidence_breakdown` (event-pipeline concept) and must not be invoked for sports.
- Do NOT modify `PredictionKernel`, `LearningService`, the 3 learning tables, or the learning dashboard.
- Do NOT persist recommendations in a new table — C is stateless, computed on-demand from B's persisted edges.
- Do NOT add a new scheduler job — recommendations are computed at API request time, not scheduled.
- Do NOT implement automated trading/order placement — informational assistance only.
- Do NOT feed recommendations back into the learning loop — that is Subproject D.

## Architecture

```
kernel_sport_edges (B's persisted output, append-only time-series)
        │
        ▼  read-only
┌─────────────────────────────────────────────────┐
│  SportRecommendationService (new, stateless)     │
│  ├─ EdgeStore.get_latest_edges(match_id)         │
│  ├─ EdgeStore.get_top_discrepancies(limit, min)  │
│  ├─ get_latest_prediction(match_id) → engine     │
│  ├─ get_calibration(engine, comp) → sample_count │
│  ├─ _select_primary_outcome (max |adjusted_edge|)│
│  ├─ _derive_direction (raw_edge sign + stale)    │
│  ├─ _compute_risk_level (liquidity + trust)      │
│  ├─ _compute_confidence (|adj_edge| × trust)     │
│  ├─ _compute_allocation (edge + risk)            │
│  ├─ diagnosis_service.decide() → act/watch/skip  │
│  └─ _build_rationale (Chinese text)              │
└─────────────────────────────────────────────────┘
        │
        ▼
   SportActionableRecommendation
   (per-match: direction + decision + edge_pct + ...)
        │
        ▼
┌─────────────────────────────────────────────────┐
│  API: /api/sport-recommendations/*               │
│  ├─ GET /{match_id}     single match rec         │
│  ├─ GET /open            open decisions list     │
│  └─ GET /discrepancies   top edge picks (proxy)  │
└─────────────────────────────────────────────────┘
        │
        ▼
   Frontend: /sports/recommendations
   (open decisions list + recommendation cards)
```

### Data flow

1. **Read**: `SportRecommendationService` reads B's persisted edges from `kernel_sport_edges` via `EdgeStore` (no writes).
2. **Select**: For a given match, pick the primary `mapped_outcome` with the largest `|adjusted_edge|`.
3. **Enrich**: Query `get_latest_prediction(match_id)` for `engine`/`competition`, then `get_calibration(engine, competition)` for `sample_count` to determine `qualified`.
4. **Convert**: Multiply B's 0-1 `adjusted_edge` by 100 → pp scale (matching `DECISION_ACT_EDGE` / `DECISION_WATCH_EDGE`).
5. **Decide**: Call `diagnosis_service.decide(adjusted_edge_pct, qualified=..., act_edge=..., watch_edge=..., cold_start_bypass_enabled=...)` → act/provisional_act/watch/skip.
6. **Derive**: Compute `direction` (YES/NO/WAIT/AVOID), `confidence`, `risk_level`, `suggested_allocation_pct`, `calibration_status`, `rationale`.
7. **Return**: Assemble `SportActionableRecommendation` and return (no persistence).

### Hard constraints

- **Stateless**: C never writes to the database. All recommendation data is computed at request time from B's persisted edges + calibration tables.
- **Zero-invasion on B**: C reads `EdgeStore` and `EdgeDetectorService` public methods only; does not modify their code.
- **Zero-invasion on event pipeline**: C creates a parallel `SportActionableRecommendation` dataclass; does not touch `ActionableRecommendation`.
- **Zero-invasion on `diagnosis_service`**: C calls `decide()` as a pure function; no modifications to the function or its thresholds.
- **Zero-invasion on `decision_quality_service`**: C does not invoke `build_decision_quality` (it requires `evidence_breakdown`, a concept absent in sports).
- **Feature flag**: `PHASE7_SPORT_RECOMMENDATION_ENABLED` defaults to OFF. When false, all new endpoints return 503.
- **Unit convention**: B's edges are 0-1; C converts to 0-100 pp before calling `decide()` and when populating `edge_pct`. This matches B spec line 92: "Subproject C will multiply by 100 when converting to `ActionableRecommendation.edge`."
- **No new table**: C does not create any new database table. Reuses `kernel_sport_edges` (read-only), `kernel_predictions`, `kernel_calibration`.
- **No new scheduler job**: Recommendations are computed on-demand. B's existing `_job_detect_sport_edges` scheduler keeps edges fresh; C reads them.

## Data Model

### `SportActionableRecommendation` (new dataclass, not persisted)

```python
@dataclass(frozen=True)
class SportActionableRecommendation:
    """Per-match actionable recommendation derived from Subproject B's edge data.

    Parallel to the event pipeline's ActionableRecommendation but with
    sports-specific fields (mapped_outcome, decision, engine_name, competition).
    Never persisted — computed on-demand from kernel_sport_edges.
    """
    # Identity
    match_id: str
    mapped_outcome: str              # "home_win" | "draw" | "away_win" (primary outcome)

    # Decision vocabulary 1: direction (what to bet)
    direction: str                   # "YES" | "NO" | "AVOID" | "WAIT"

    # Decision vocabulary 2: decision gate (whether to act)
    decision: str                    # "act" | "provisional_act" | "watch" | "skip"

    # Confidence & risk
    confidence: str                  # "high" | "medium" | "low"
    risk_level: str                  # "low" | "medium" | "high"

    # Edge data (0-100 pp scale, converted from B's 0-1)
    edge_pct: float                  # adjusted_edge * 100 (signed)
    raw_edge_pct: float              # raw_edge * 100 (signed)

    # Trust & liquidity (from B, 0-1 scale)
    trust: float                     # 0-1, from EdgeResult.trust
    liquidity_factor: float          # 0-1, from EdgeResult.liquidity_factor
    stale: bool                      # from EdgeResult.stale

    # Allocation
    suggested_allocation_pct: float  # 0-25 (realistically 0-2)

    # Calibration
    calibration_status: str          # "calibrated" | "uncalibrated_provisional"

    # Explanation
    rationale: str                   # Chinese rationale text

    # Metadata (from KernelPrediction via get_latest_prediction)
    engine_name: str | None          # from KernelPrediction.engine
    competition: str | None          # from KernelPrediction.competition
    prediction_timestamp: datetime | None  # from KernelPrediction.created_at

    # Edge metadata
    model_prob: float                # 0-1, from EdgeResult.model_prob
    market_prob: float               # 0-1, from EdgeResult.market_prob
    sources_count: int               # from EdgeResult.sources_count
    captured_at: datetime            # from EdgeResult.captured_at
```

### Vocabulary separation rationale

The event pipeline maintains two separate decision vocabularies:
- `ActionableRecommendation.direction` ∈ {YES, NO, AVOID, WAIT} — "what to bet"
- `prediction.decision` ∈ {act, provisional_act, watch, skip} — "whether to act"

These are distinct concerns: a match can have `direction=YES` (model favors an outcome) but `decision=skip` (edge is too small to act on). Subproject C preserves this separation in `SportActionableRecommendation` by carrying both fields.

### Direction derivation

```python
def _derive_direction(raw_edge: float, stale: bool, risk_level: str) -> str:
    """Derive YES/NO/WAIT/AVOID from edge sign, staleness, and risk."""
    if stale:
        return "WAIT"
    if risk_level == "high":
        return "AVOID"
    if raw_edge > 0:
        return "YES"   # model probability > market probability for this outcome
    if raw_edge < 0:
        return "NO"    # model probability < market probability for this outcome
    return "WAIT"
```

**Semantics**:
- `direction="YES"` on `mapped_outcome="home_win"` means: the model thinks home_win is more likely than the market does — bet YES on home_win.
- `direction="NO"` on `mapped_outcome="home_win"` means: the model thinks home_win is less likely than the market does — bet NO on home_win (i.e., bet on away_win/draw).

### Primary outcome selection

For matches with multiple outcomes (football: home_win/draw/away_win), C picks the outcome with the largest `|adjusted_edge|` as the recommendation target. This focuses the recommendation on the outcome where model-market divergence is greatest.

```python
def _select_primary_outcome(edges: list[EdgeResult]) -> EdgeResult | None:
    """Pick the edge with the largest |adjusted_edge|."""
    if not edges:
        return None
    return max(edges, key=lambda e: abs(e.adjusted_edge))
```

### Decision gate (reuse `diagnosis_service.decide`)

```python
from app.services.diagnosis_service import decide

adjusted_edge_pct = primary_edge.adjusted_edge * 100  # 0-1 → 0-100 pp
qualified = calibration_sample_count >= settings.CALIBRATION_FEEDBACK_MIN_SAMPLES

decision = decide(
    adjusted_edge=adjusted_edge_pct,
    qualified=qualified,
    act_edge=settings.DECISION_ACT_EDGE,          # 6.0 pp
    watch_edge=settings.DECISION_WATCH_EDGE,       # 2.0 pp
    cold_start_bypass_enabled=settings.COLD_START_BYPASS_ENABLED,
)
# Returns: "act" | "provisional_act" | "watch" | "skip"
```

### Risk level computation

```python
def _compute_risk_level(liquidity_factor: float, trust: float, stale: bool) -> str:
    """High risk when liquidity or trust is low, or data is stale."""
    if stale or liquidity_factor < 0.2 or trust < 0.2:
        return "high"
    if liquidity_factor < 0.5 or trust < 0.5:
        return "medium"
    return "low"
```

### Confidence computation

```python
def _compute_confidence(adjusted_edge_pct: float, trust: float) -> str:
    """Confidence scales with both edge magnitude and trust."""
    score = abs(adjusted_edge_pct) * trust
    if score >= 4.0:    # e.g., 6pp edge × 0.67 trust, or 8pp × 0.5 trust
        return "high"
    if score >= 2.0:    # e.g., 4pp × 0.5 trust, or 2pp × 1.0 trust
        return "medium"
    return "low"
```

### Suggested allocation computation

```python
def _compute_allocation(adjusted_edge_pct: float, risk_level: str, decision: str) -> float:
    """Kelly-inspired fractional allocation, capped at 2% of bankroll.

    Returns value in 0-25 scale (realistically 0-2).
    Zero when decision is skip or risk is high.
    """
    if decision == "skip" or risk_level == "high":
        return 0.0
    # Fractional Kelly: edge / act_edge × base_unit, capped at 2%
    base = min(abs(adjusted_edge_pct) / settings.DECISION_ACT_EDGE, 1.0) * 2.0
    if risk_level == "medium":
        base *= 0.5
    return round(base, 2)
```

### Calibration status mapping

```python
calibration_status = "calibrated" if qualified else "uncalibrated_provisional"
```

Where `qualified = (calibration_sample_count >= CALIBRATION_FEEDBACK_MIN_SAMPLES)`. This mirrors the event pipeline's `decision_report_service` override pattern.

### Rationale generation (Chinese)

```python
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
        return f"数据过期，建议等待最新市场快照后再决策。"

    if direction == "AVOID":
        return f"市场流动性不足或模型可信度低，建议规避。"

    if direction == "WAIT":
        return f"模型与市场概率接近，无明显边际优势，建议观望。"

    action = "看好" if direction == "YES" else "看淡"
    confidence_desc = "高置信" if trust >= 0.7 else "中等置信" if trust >= 0.5 else "低置信"
    liquidity_desc = "流动性充足" if liquidity_factor >= 0.5 else "流动性一般" if liquidity_factor >= 0.2 else "流动性不足"

    return (
        f"模型{action}{outcome_label}，"
        f"调整后边际 {edge_pct:+.2f}pp，"
        f"{confidence_desc}（trust={trust:.2f}），"
        f"{liquidity_desc}。"
        f"决策建议：{decision}。"
        f"本分析仅供参考，不构成投资建议。"
    )
```

## Service Interface

### `SportRecommendationService` (new, stateless)

**File**: `backend/app/kernel/sport_recommendation_service.py`

```python
class SportRecommendationService:
    """Stateless service that computes SportActionableRecommendation from B's edges.

    Reads-only: never writes to the database. All data comes from
    EdgeStore (B's persisted edges) + KernelPrediction + KernelCalibration.
    """

    def __init__(self) -> None:
        from app.kernel.edge_store import EdgeStore
        self._edge_store = EdgeStore()

    def get_recommendation(self, match_id: str) -> SportActionableRecommendation | None:
        """Compute recommendation for a single match.

        Returns None when:
        - No persisted edges exist for this match
        - No calibration/prediction metadata found (edge exists but prediction gone)
        """

    def get_open_decisions(
        self,
        limit: int = 20,
        decision: str | None = None,
    ) -> list[SportActionableRecommendation]:
        """List matches with open decisions (act/provisional_act/watch).

        - Fetches top discrepancies from EdgeStore (over-fetches 3× limit to
          account for multiple outcomes per match, then deduplicates).
        - Filters by decision after computation.
        - When decision param is provided, filters to that specific decision.
        """

    def get_top_picks(
        self,
        limit: int = 20,
        min_abs_edge_pct: float = 0.0,
    ) -> list[SportActionableRecommendation]:
        """List top edge picks (largest |adjusted_edge|), regardless of decision.

        Unlike get_open_decisions, this includes 'skip' decisions — useful for
        scanning all model-market divergences.
        """
```

### Internal helpers (private)

```python
def _select_primary_outcome(edges: list[dict]) -> dict | None: ...
def _derive_direction(raw_edge: float, stale: bool, risk_level: str) -> str: ...
def _compute_risk_level(liquidity_factor: float, trust: float, stale: bool) -> str: ...
def _compute_confidence(adjusted_edge_pct: float, trust: float) -> str: ...
def _compute_allocation(adjusted_edge_pct: float, risk_level: str, decision: str) -> float: ...
def _build_rationale(direction, mapped_outcome, edge_pct, trust, liquidity_factor, stale, decision) -> str: ...
def _get_qualified(match_id: str) -> tuple[bool, str | None, str | None, datetime | None]: ...
    # Returns (qualified, engine_name, competition, prediction_timestamp)
    # Queries get_latest_prediction + get_calibration
def _edge_dict_to_recommendation(edge: dict, match_id: str) -> SportActionableRecommendation: ...
```

## API Endpoints

**New route file**: `backend/app/api/routes/sport_recommendations.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/sport-recommendations/{match_id}` | GET | Single match recommendation |
| `/api/sport-recommendations/open` | GET | Open decisions list (act/provisional_act/watch) |
| `/api/sport-recommendations/discrepancies` | GET | Top edge picks (all decisions) |

### Endpoint specifications

```python
router = APIRouter(prefix="/sport-recommendations", tags=["Sport Recommendations"])

def _ensure_enabled() -> None:
    """Raise 503 when PHASE7_SPORT_RECOMMENDATION_ENABLED is false."""
    if not config.settings.PHASE7_SPORT_RECOMMENDATION_ENABLED:
        raise HTTPException(status_code=503, detail="Sport recommendations disabled.")

@router.get("/{match_id}")
def get_recommendation(match_id: str) -> dict:
    """Single match recommendation. Returns 404 when no edges exist."""
    _ensure_enabled()
    service = SportRecommendationService()
    rec = service.get_recommendation(match_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No edges found for match.")
    return _rec_to_dict(rec)

@router.get("/open")
def get_open_decisions(
    limit: int = Query(20, ge=1, le=100),
    decision: str | None = Query(None, regex="^(act|provisional_act|watch)$"),
) -> dict:
    """Open decisions list. Filterable by decision type."""
    _ensure_enabled()
    service = SportRecommendationService()
    recs = service.get_open_decisions(limit=limit, decision=decision)
    return {"items": [_rec_to_dict(r) for r in recs], "total": len(recs)}

@router.get("/discrepancies")
def get_top_picks(
    limit: int = Query(20, ge=1, le=100),
    min_abs_edge: float = Query(0.0, ge=0.0, le=1.0),
) -> dict:
    """Top edge picks. min_abs_edge is on 0-1 scale (B's convention)."""
    _ensure_enabled()
    service = SportRecommendationService()
    recs = service.get_top_picks(limit=limit, min_abs_edge_pct=min_abs_edge * 100)
    return {"items": [_rec_to_dict(r) for r in recs], "total": len(recs)}
```

### Serializer

```python
def _rec_to_dict(rec: SportActionableRecommendation) -> dict:
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
```

**Constraints**:
- All 3 GET endpoints are gated by `PHASE7_SPORT_RECOMMENDATION_ENABLED`; return 503 when false.
- All endpoints are read-only (no `require_write_key`).
- `/{match_id}` returns 404 when no edges exist for the match.
- `/open` filters to act/provisional_act/watch by default (excludes skip).
- `/discrepancies` `min_abs_edge` parameter is on 0-1 scale (B's convention) for consistency with B's `/sport-edges/discrepancies`.

## Frontend

**New route**: `/sports/recommendations` (independent of learning dashboard and markets page)

**New components** (in `frontend/src/components/sports/recommendations/`):
1. `RecommendationCard.tsx` — single recommendation card (direction badge + edge + confidence + rationale + allocation)
2. `OpenDecisionsList.tsx` — open decisions list (table/card layout, filterable by decision)

**Navigation entry**: `app-nav.tsx` inserts `体育推荐 → /sports/recommendations` after `/sports/markets`, before `/sports/learning`.

**API client**: `frontend/src/lib/sport-recommendations-api.ts` (independent of `sport-markets-api.ts` and `learning-api.ts` for职责分离).

### Component specifications

**`RecommendationCard.tsx`**:
- Direction badge (YES=green, NO=red, WAIT=gray, AVOID=orange)
- Decision badge (act=green, provisional_act=yellow, watch=blue, skip=gray)
- Edge display (`edge_pct` with + sign for positive)
- Confidence indicator (high/medium/low)
- Trust & liquidity bars
- Rationale text (full Chinese rationale)
- Suggested allocation (when > 0)
- Hidden when `direction === "AVOID"` in summary mode (inherit event-pipeline `decision-card.tsx` pattern)

**`OpenDecisionsList.tsx`**:
- Filter dropdown (all / act / provisional_act / watch)
- Pagination (limit/offset)
- Empty state ("暂无开放决策")
- Each row embeds `RecommendationCard` in expanded mode

## Configuration

**New items in `config.py`** (added before `settings = Settings()`):

```python
PHASE7_SPORT_RECOMMENDATION_ENABLED: bool = _env_bool("PHASE7_SPORT_RECOMMENDATION_ENABLED", "false")
# Reuses existing thresholds — no new decision thresholds:
# - DECISION_ACT_EDGE (6.0 pp)
# - DECISION_WATCH_EDGE (2.0 pp)
# - COLD_START_BYPASS_ENABLED (true)
# - CALIBRATION_FEEDBACK_MIN_SAMPLES (8)
# - DIAGNOSIS_TRUST_FLOOR (0.1)
# - DIAGNOSIS_DORMANT_TRUST (0.5)
# - DIAGNOSIS_LIQUIDITY_FLOOR (5000.0)
```

All defaults to OFF / conservative values to maintain backward compatibility.

## CLI

**New file**: `backend/scripts/sport_recommendation_cli.py`

Follows `sport_edge_cli.py` pattern:

```bash
python -m scripts.sport_recommendation_cli match --match-id ID
python -m scripts.sport_recommendation_cli open [--limit N] [--decision act|provisional_act|watch]
python -m scripts.sport_recommendation_cli picks [--limit N] [--min-abs-edge 0.05]
```

## Integration Points

### Zero-invasion modules (NOT modified in Subproject C)

- `backend/app/kernel/edge_detector_service.py` (Subproject B)
- `backend/app/kernel/edge_store.py` (Subproject B)
- `backend/app/api/routes/sport_edges.py` (Subproject B)
- `backend/app/models/event.py` (`ActionableRecommendation`)
- `backend/app/services/event_intelligence_service.py`
- `backend/app/services/decision_report_service.py`
- `backend/app/services/decision_quality_service.py`
- `backend/app/services/diagnosis_service.py` (C calls `decide()` read-only)
- `backend/app/kernel/prediction_kernel.py`
- `backend/app/kernel/learning_service.py`
- 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`)
- Learning dashboard components
- `polymarket_event_source` / `kalshi_event_source`

### Modules C reads from (read-only)

- `EdgeStore.get_latest_edges(match_id)` — B's persisted edges per match
- `EdgeStore.get_top_discrepancies(limit, min_abs_edge)` — B's top edges globally
- `get_latest_prediction(match_id)` — for engine/competition/prediction_timestamp
- `get_calibration(engine, competition)` — for sample_count → qualified
- `diagnosis_service.decide()` — pure function for act/watch/skip gate

### New module manifest

```
backend/app/
├── api/routes/sport_recommendations.py          # 3 GET endpoints
└── kernel/
    └── sport_recommendation_service.py           # stateless recommendation engine

backend/scripts/
└── sport_recommendation_cli.py                   # CLI tool

frontend/src/
├── app/sports/recommendations/
│   └── page.tsx                                   # recommendations page
├── components/sports/recommendations/
│   ├── RecommendationCard.tsx
│   └── OpenDecisionsList.tsx
└── lib/sport-recommendations-api.ts

backend/tests/
├── test_sport_recommendation_service.py           # ~18 tests
└── test_sport_recommendation_routes.py            # ~8 tests

frontend/src/components/sports/recommendations/
├── RecommendationCard.test.tsx                    # ~5 tests
└── OpenDecisionsList.test.tsx                     # ~4 tests
```

## Testing Strategy

### Backend TDD (strict RED → GREEN)

| Test file | Coverage | Key scenarios |
|-----------|----------|---------------|
| `test_sport_recommendation_service.py` | `SportRecommendationService` | direction derivation (YES/NO/WAIT/AVOID), risk level (low/medium/high), confidence (high/medium/low), allocation computation, primary outcome selection, unit conversion (0-1→0-100), qualified determination, calibration_status mapping, stale→WAIT, no edges→None, get_open_decisions filtering, get_top_picks, rationale generation |
| `test_sport_recommendation_routes.py` | API endpoints | 503 when disabled, 404 when no edges, single match rec, open decisions list, decision filter, discrepancies, limit clamp, min_abs_edge filter |

**Test constraints**:
- Backend DB tests use `tmp_path` real SQLite, no mocks (inherit B's test pattern)
- `diagnosis_service.decide()` is called with real thresholds (no mocking)
- Edge data is seeded via `EdgeStore.append_edge` (B's public API)
- Calibration data is seeded via direct `KernelCalibration` insert (test helper)
- Prediction data is seeded via `KernelPrediction` insert (test helper)

### Frontend tests

- `RecommendationCard.test.tsx`: direction badge rendering, AVOID hidden in summary, rationale display, allocation display
- `OpenDecisionsList.test.tsx`: list rendering, empty state, filter interaction
- `next/link` mock (inherit `trades/page.test.tsx` pattern)

**Estimate**: ~26 backend tests + ~9 frontend tests = ~35 new tests

## Phase Boundaries

### In scope (Subproject C)

- `SportActionableRecommendation` dataclass
- `SportRecommendationService` (stateless, reads B's edges)
- 3 API endpoints (`/{match_id}`, `/open`, `/discrepancies`)
- Frontend recommendations page + components
- CLI tool
- Config flag `PHASE7_SPORT_RECOMMENDATION_ENABLED`

### Out of scope (deferred to D and later)

- Market settlement price feedback into learning loop → Subproject D
- Automated trading/order placement → never (project principle)
- Real-time recommendation push (WebSocket) → current design uses on-demand computation
- Recommendation history persistence → C is stateless; if needed, add a table in a future phase
- LLM-enhanced rationale → current rationale is deterministic; LLM enhancement is a future option

## Success Criteria

1. **Functional completeness**:
   - `SportRecommendationService.get_recommendation(match_id)` returns a complete `SportActionableRecommendation` for matches with persisted edges
   - `get_open_decisions()` returns matches with act/provisional_act/watch decisions
   - Direction/decision/confidence/risk/allocation all computed correctly
   - Unit conversion (0-1 → 0-100) applied at the right boundary

2. **Zero regression**:
   - Subproject B tests pass with zero modifications
   - Event pipeline `ActionableRecommendation` tests pass with zero modifications
   - `diagnosis_service` tests pass with zero modifications
   - `decision_quality_service` tests pass with zero modifications
   - `PredictionKernel` / `LearningService` / learning dashboard zero modifications

3. **Safety gating**:
   - `PHASE7_SPORT_RECOMMENDATION_ENABLED=false` → all 3 endpoints return 503
   - C never writes to the database (stateless)
   - C never modifies B's persisted edges

4. **Operability**:
   - CLI can query single match / open decisions / top picks
   - Frontend page renders open decisions with filtering
   - Recommendations update in real-time as B's edges update (no stale cache)

## Estimate

- **New files**: 4 backend + 4 frontend + 4 tests + 1 CLI = 13 files
- **Modified files**: 3 (config / router / app-nav)
- **Code volume**: +~1,800 lines
- **Tests**: ~35 new tests
