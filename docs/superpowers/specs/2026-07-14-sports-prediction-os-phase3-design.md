# Sports Prediction OS — Phase 3 Design: Unified Learning Loop

**Date:** 2026-07-14
**Status:** Reviewed
**Depends on:** Phase 1 (`backend/app/kernel/`), Phase 2 (`backend/app/sports/football/adapters/`)
**Predecessor:** `docs/superpowers/specs/2026-07-14-sports-prediction-os-phase2-design.md`

---

## 1. Goal

Implement the complete closed-loop learning system: `outcome → error → calibration → weight update → engine score → next prediction`. This is the system's core competitive advantage — the mechanism that drives prediction accuracy from ~67% to 72-75%+.

Phase 3 activates 3 dormant database tables (`KernelPredictionHistory`, `KernelEngineScore`, `KernelFactor`) with schema migrations, implements 2 stub methods (`update_calibration`, `update_weights`), adds `ContributionItem.predicted_outcome` for per-factor accuracy tracking, closes the weight→prediction gap, and enables dynamic engine selection.

### Success Criteria

1. `process_outcome(match_id)` executes the full 5-step loop: record_outcome → compute_error → update_calibration → update_weights → engine_score
2. `update_weights` adjusts `elo_weight` and `odds_weight` per competition using EWMA, persisted to `KernelFactor` table with composite `(factor_id, competition)` key
3. `EloOddsEngine.predict()` reads weights from `FactorRegistry` instead of hardcoded 0.30/0.70, and sets `predicted_outcome` in explanation for per-factor accuracy tracking
4. `update_calibration` fits a linear regression model, persisted to new `KernelCalibration` table
5. `engine_score()` persists to `KernelEngineScore` table (with new `confidence_calibration` column) using calibration model
6. `EngineRegistry.select("auto")` dynamically selects the best engine based on `EngineScore`
7. `record_prediction` writes to `KernelPredictionHistory` (with new `feature_version` column) for version tracking
8. `PHASE3_LEARNING_ENABLED` defaults to OFF; when OFF, `process_outcome` keeps existing behavior
9. `init_kernel_db()` detects and migrates 3 dormant tables with old schema (drop and recreate)
10. `ContributionItem.predicted_outcome` field added with default `None` — backward compatible
11. All Phase 1 + Phase 2 tests pass (zero regression)

---

## 2. Scope

### In Scope

- Implement `update_calibration` — linear regression calibration model
- Implement `update_weights` — EWMA weight adjustment per competition
- FactorRegistry DB persistence (load from + write to `KernelFactor` table)
- EloOddsEngine weight reading from FactorRegistry
- EngineScore persistence to `KernelEngineScore` table
- Dynamic engine selection in `EngineRegistry.select("auto")`
- KernelPredictionHistory writing in `record_prediction`
- `process_outcome` full loop orchestration
- New `KernelCalibration` database table
- `PHASE3_LEARNING_ENABLED` feature flag and learning parameters
- `ContributionItem.predicted_outcome` field (backward-compatible addition to domain.py)
- DB migration: drop and recreate 3 dormant tables with new schema (`KernelFactor`, `KernelEngineScore`, `KernelPredictionHistory`)
- 44 new tests across 9 test files

### Out of Scope

- Applying calibration to prediction output (Phase 4+ — `calibrated_prob = slope × raw_prob + intercept`)
- Multi-engine support beyond EloOddsEngine (only one engine exists today)
- Frontend changes (Phase 3 is backend-only)
- NBA / basketball (Phase 4)
- Online learning / streaming updates (batch update per match outcome only)

---

## 3. Architecture

### 3.1 Closed Loop Overview

```
process_outcome(match_id)
  │
  ├─ 1. record_outcome(outcome)                    ✅ existing
  ├─ 2. compute_error(match_id)                     ✅ existing
  ├─ 3. update_calibration(competition, engine)     ← NEW: linear regression
  ├─ 4. update_weights(competition)                 ← NEW: EWMA adjustment
  ├─ 5. engine_score(engine, competition)           ← ENHANCED: persist to DB
  └─ 6. next predict() → engine reads FactorRegistry  ← NEW: closes the loop
```

### 3.2 Module Layout

```
backend/app/kernel/
├── learning_service.py      # MODIFIED: implement update_calibration + update_weights + persist engine_score + write prediction history
├── prediction_kernel.py     # MODIFIED: process_outcome full loop + Phase 3 flag gating
├── engines/elo_odds_engine.py  # MODIFIED: accept FactorRegistry, read weights, set predicted_outcome in explanation
├── factor_registry.py       # MODIFIED: DB persistence + init_default_factors()
├── engine_registry.py       # MODIFIED: select("auto") dynamic selection + LearningService injection
├── kernel_db.py             # MODIFIED: new KernelCalibration table + KernelFactor schema fix + KernelEngineScore add confidence_calibration + KernelPredictionHistory add feature_version
├── domain.py                # MODIFIED: add predicted_outcome field to ContributionItem (backward-compatible, default None)
├── protocols.py             # UNCHANGED (interfaces already complete)
└── (all other files UNCHANGED)

backend/app/api/routes/
└── predictions.py            # MODIFIED: inject FactorRegistry into EloOddsEngine + share LearningService instance + Phase 3 flag

backend/app/core/
└── config.py                # MODIFIED: PHASE3_LEARNING_ENABLED + learning parameters
```

### 3.2.1 DB Migration for Dormant Tables

Three dormant tables need schema changes before Phase 3 use. Since all three are dormant (never written to in Phase 1/2), they contain no production data. The `init_kernel_db()` function must drop and recreate them if the old schema is detected:

- **`kernel_factors`**: Change PK from `factor_id` (single column) to auto-increment `id` + unique constraint `(factor_id, competition)` — required to store competition-specific weights for the same factor
- **`kernel_engine_scores`**: Add `confidence_calibration` column (Float, default 0.0)
- **`kernel_prediction_history`**: Add `feature_version` column (String, nullable)

Detection: check if `kernel_factors` has `factor_id` as its primary key (old schema) via `PRAGMA table_info`, and if so, `DROP TABLE` all three before `create_all` runs. This is safe because the tables are empty.

### 3.3 New Database Table: KernelCalibration

```python
class KernelCalibration(KernelBase):
    __tablename__ = "kernel_calibration"
    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String(50), nullable=False)
    competition = Column(String(50), nullable=False)
    slope = Column(Float, nullable=False)
    intercept = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    avg_confidence = Column(Float, nullable=False, default=0.0)
    avg_accuracy = Column(Float, nullable=False, default=0.0)
    last_updated = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("engine", "competition", name="uq_calibration_engine_competition"),
    )
```

### 3.4 Key Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `LEARNING_WINDOW_SIZE` | 30 | Number of recent matches for aggregation |
| `EWMA_ALPHA` | 0.1 | Weight update decay factor (10% new, 90% old) |
| `MIN_SAMPLES_FOR_CALIBRATION` | 10 | Minimum samples before fitting calibration |
| `MIN_SAMPLES_FOR_ENGINE_SELECT` | 5 | Minimum samples for dynamic engine selection |
| `WEIGHT_FLOOR` | 0.05 | Minimum weight value |
| `WEIGHT_CEILING` | 0.95 | Maximum weight value |
| `CALIBRATION_SLOPE_MIN` | 0.0 | Minimum calibration slope |
| `CALIBRATION_SLOPE_MAX` | 2.0 | Maximum calibration slope |
| `CALIBRATION_INTERCEPT_MIN` | -0.5 | Minimum calibration intercept (hardcoded constant) |
| `CALIBRATION_INTERCEPT_MAX` | 0.5 | Maximum calibration intercept (hardcoded constant) |

> **Note:** `CALIBRATION_SLOPE_MIN/MAX` and `CALIBRATION_INTERCEPT_MIN/MAX` are hardcoded constants in `learning_service.py`, not configurable env vars. They are mathematical bounds that rarely need tuning, unlike the operational parameters above which are exposed via `config.py`.

---

## 4. Weight Update — EWMA

### 4.1 Algorithm

```python
def update_weights(self, competition: str) -> None:
    # 1. Query recent N outcomes with error data for this competition
    #    Join KernelMatchOutcome with KernelPrediction to get explanation
    records = query_recent_outcomes_with_predictions(competition, limit=LEARNING_WINDOW_SIZE)
    if len(records) < MIN_SAMPLES_FOR_CALIBRATION:
        return

    # 2. Compute per-factor accuracy using ContributionItem.predicted_outcome
    #    Each prediction's explanation stores what outcome each factor predicted:
    #    elo ContributionItem.predicted_outcome = "home_win"/"draw"/"away_win"
    #    odds ContributionItem.predicted_outcome = "home_win"/"draw"/"away_win"
    elo_correct = 0
    elo_total = 0
    odds_correct = 0
    odds_total = 0

    for record in records:
        actual = record.outcome  # "home_win"/"draw"/"away_win"
        for item in record.explanation:  # list of ContributionItem dicts
            if item["factor"] == "elo" and item.get("predicted_outcome"):
                elo_total += 1
                if item["predicted_outcome"] == actual:
                    elo_correct += 1
            elif item["factor"] == "odds" and item.get("predicted_outcome"):
                odds_total += 1
                if item["predicted_outcome"] == actual:
                    odds_correct += 1

    if elo_total == 0 or odds_total == 0:
        return  # Insufficient per-factor data

    elo_acc = elo_correct / elo_total
    odds_acc = odds_correct / odds_total

    # 3. Compute target weights (proportional to accuracy)
    total_acc = elo_acc + odds_acc
    if total_acc == 0:
        return  # Both wrong, no adjustment
    w_elo_target = elo_acc / total_acc
    w_odds_target = odds_acc / total_acc

    # 4. EWMA smooth update
    w_elo_old = factor_registry.get_weight("elo", competition)
    w_odds_old = factor_registry.get_weight("odds", competition)
    w_elo_new = clamp(EWMA_ALPHA * w_elo_target + (1 - EWMA_ALPHA) * w_elo_old,
                      WEIGHT_FLOOR, WEIGHT_CEILING)
    w_odds_new = clamp(1.0 - w_elo_new, WEIGHT_FLOOR, WEIGHT_CEILING)

    # 5. Persist to KernelFactor table
    factor_registry.update_weight("elo", competition, w_elo_new, source="ewma")
    factor_registry.update_weight("odds", competition, w_odds_new, source="ewma")
```

### 4.2 Per-Factor Predicted Outcome Tracking

To compute per-factor accuracy, `ContributionItem` gains a new `predicted_outcome: str | None = None` field (backward-compatible default). `EloOddsEngine.predict()` sets this field for each factor:

- **Elo**: `predicted_outcome = max(elo_probs, key=elo_probs.get)` — the outcome with highest Elo probability
- **Odds**: `predicted_outcome = max(market_probs, key=market_probs.get)` — the outcome with highest market probability
- When a factor is unavailable, `predicted_outcome = None`

The `update_weights` implementation queries `KernelPrediction` (which stores explanation as JSON) and extracts `predicted_outcome` for each factor. If `predicted_outcome` is `None` or missing (e.g., predictions made before Phase 3), that match is skipped in the per-factor accuracy count.

The `KernelPrediction.explanation` column is JSON type, so the `predicted_outcome` field is automatically serialized/deserialized with the rest of the `ContributionItem` dict. No additional schema change to `KernelPrediction` is needed.

---

## 5. FactorRegistry Persistence

### 5.1 DB-Backed FactorRegistry

```python
class FactorRegistry:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_kernel_session
        # Key: (factor_id, competition | None) -> FactorConfig
        # competition=None means global default (same pattern as existing code)
        self._factors: dict[tuple[str, str | None], FactorConfig] = {}
        self._load_from_db()
        if not self._factors:
            self._init_default_factors()

    def _load_from_db(self) -> None:
        """Load all factors from KernelFactor table.
        
        Reads rows where each row has (factor_id, competition, weight, ...).
        competition=None rows are global defaults.
        Stores as (factor_id, competition) -> FactorConfig in memory.
        """

    def _init_default_factors(self) -> None:
        """Register elo (0.30) and odds (0.70) as global defaults if DB is empty.
        
        Writes to KernelFactor table with competition=None.
        """

    def update_weight(self, factor_id: str, competition: str,
                      new_weight: float, source: str = "manual") -> None:
        # Update in-memory + upsert to KernelFactor table
        # Uses (factor_id, competition) composite key in DB
```

### 5.1.1 KernelFactor Schema Change

The existing `KernelFactor` table uses `factor_id` as its sole primary key, which prevents storing both a global default (competition=None) and competition-specific weights for the same factor. Phase 3 changes the schema:

```python
class KernelFactor(KernelBase):
    __tablename__ = "kernel_factors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_id = Column(String, nullable=False)
    category = Column(String, nullable=False)
    version = Column(String, nullable=False)
    weight = Column(Float, default=1.0)
    competition = Column(String)  # NULL = global
    enabled = Column(Integer, default=1)
    source = Column(String, default="manual")
    updated_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("factor_id", "competition", name="uq_factor_id_competition"),
    )
```

See Section 3.2.1 for migration strategy (drop and recreate, since table is dormant/empty).

### 5.2 Default Factors

| factor_id | category | default_weight | description |
|-----------|----------|---------------|-------------|
| `elo` | `elo_rating` | 0.30 | Elo rating contribution |
| `odds` | `market_odds` | 0.70 | Market odds contribution |

---

## 6. EloOddsEngine Weight Reading

### 6.1 Constructor Injection

```python
class EloOddsEngine:
    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        if self._factor_registry:
            elo_w = self._factor_registry.get_weight("elo", match.season.competition.code)
            odds_w = self._factor_registry.get_weight("odds", match.season.competition.code)
        else:
            elo_w, odds_w = 0.30, 0.70

        # ... existing BTD + odds fusion logic uses elo_w, odds_w ...

        # Explanation now includes predicted_outcome per factor (NEW)
        explanation = [
            ContributionItem(
                factor="elo", direction="support" if elo_available else "neutral",
                weight=elo_w, available=elo_available,
                detail=f"Elo {elo_home} vs {elo_away}" if elo_available else "Elo unavailable",
                predicted_outcome=max(elo_probs, key=elo_probs.get) if elo_available else None,
            ),
            ContributionItem(
                factor="odds", direction="support" if odds_available else "neutral",
                weight=odds_w, available=odds_available,
                detail=f"Odds {odds_h}/{odds_d}/{odds_a}" if odds_available else "Odds unavailable",
                predicted_outcome=max(market_probs, key=market_probs.get) if odds_available else None,
            ),
        ]
        # ...
```

The `predicted_outcome` field records which outcome (home_win/draw/away_win) each factor independently favored, enabling per-factor accuracy computation in `update_weights`.

### 6.2 Protocol Compatibility

`PredictionEngine` Protocol signature `predict(features, match)` is unchanged. `FactorRegistry` is injected via constructor, not passed through Protocol. The `PredictionKernel` constructs `EloOddsEngine` with the `FactorRegistry` instance.

---

## 7. Calibration Model — Linear Regression

### 7.1 Algorithm

```python
def update_calibration(self, competition: str, engine: str) -> None:
    records = query_recent_predictions_with_outcomes(competition, engine, LEARNING_WINDOW_SIZE)
    if len(records) < MIN_SAMPLES_FOR_CALIBRATION:
        return

    x = [r.predicted_home_prob for r in records]
    y = [1.0 if r.actual_outcome == "home_win" else 0.0 for r in records]

    n = len(x)
    sum_x, sum_y = sum(x), sum(y)
    sum_xx = sum(xi * xi for xi in x)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    denominator = n * sum_xx - sum_x * sum_x
    if abs(denominator) < 1e-10:
        return

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n

    slope = clamp(slope, CALIBRATION_SLOPE_MIN, CALIBRATION_SLOPE_MAX)
    intercept = clamp(intercept, CALIBRATION_INTERCEPT_MIN, CALIBRATION_INTERCEPT_MAX)

    upsert_calibration(engine, competition, slope, intercept,
                       sample_count=n, avg_confidence=sum_x/n, avg_accuracy=sum_y/n)
```

### 7.2 Calibration Application

Phase 3 stores calibration parameters and computes `EngineScore.confidence_calibration` but does NOT apply calibration to prediction output. The `confidence_calibration` metric is:

```python
confidence_calibration = avg_accuracy / max(avg_confidence, 1e-6)
# ratio ≈ 1.0: well-calibrated
# ratio > 1.0: underconfident (actual accuracy higher than predicted confidence)
# ratio < 1.0: overconfident (actual accuracy lower than predicted confidence)
```

Future Phase 4+ may apply `calibrated_prob = slope × raw_prob + intercept` in `EloOddsEngine.predict()`.

---

## 8. EngineScore Persistence

### 8.1 Enhanced `engine_score()`

```python
def engine_score(self, engine: str, competition: str | None = None) -> EngineScore | None:
    # 1. Real-time aggregation (existing logic)
    score = self._aggregate_score(engine, competition)

    # 2. Read confidence_calibration from KernelCalibration
    cal = query_calibration(engine, competition)
    if cal:
        score.confidence_calibration = cal.avg_accuracy / max(cal.avg_confidence, 1e-6)

    # 3. Persist to KernelEngineScore table (NEW)
    upsert_engine_score(score)

    return score
```

### 8.2 KernelEngineScore Schema Change

The existing `KernelEngineScore` table lacks a `confidence_calibration` column. Phase 3 adds it:

```python
class KernelEngineScore(KernelBase):
    __tablename__ = "kernel_engine_scores"
    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String, nullable=False)
    competition = Column(String)  # NULL = global
    accuracy = Column(Float)
    avg_mae = Column(Float)
    brier_score = Column(Float)
    sample_count = Column(Integer, default=0)
    confidence_calibration = Column(Float, default=0.0)  # NEW
    last_updated = Column(DateTime)
```

See Section 3.2.1 for migration strategy.

### 8.3 KernelEngineScore Usage

- `engine_score()` writes to this table on every `process_outcome` call
- `EngineRegistry.select("auto")` reads from this table for dynamic selection
- API endpoint `/api/predictions/engines` can read from this table for historical tracking

---

## 9. Dynamic Engine Selection

### 9.1 Enhanced `EngineRegistry.select()`

```python
def select(self, engine_name: str, competition: str | None = None) -> PredictionEngine:
    if engine_name != "auto":
        return self._engines[engine_name]

    best_engine = None
    best_accuracy = -1.0
    for name, engine in self._engines.items():
        score = self._learning_service.engine_score(name, competition)
        if score and score.sample_count >= MIN_SAMPLES_FOR_ENGINE_SELECT:
            if score.accuracy > best_accuracy:
                best_accuracy = score.accuracy
                best_engine = engine

    return best_engine or self._default_engine
```

### 9.2 Signature Change

`select(engine_name: str)` → `select(engine_name: str, competition: str | None = None)`. The `competition` parameter is optional with default `None`, so existing callers that pass only `engine_name` continue to work unchanged.

### 9.3 EngineRegistry LearningService Injection

`EngineRegistry` needs access to `LearningService` for dynamic selection. This is injected via constructor:

```python
class EngineRegistry:
    def __init__(self, learning_service: LearningService | None = None) -> None:
        self._engines: dict[str, PredictionEngine] = {}
        self._learning_service = learning_service
        self._default_engine: PredictionEngine | None = None
```

When `learning_service` is None (e.g., in unit tests), `select("auto")` always returns the default engine.

---

## 10. Prediction History

### 10.1 `record_prediction` Enhancement

```python
def record_prediction(self, match: MatchIdentity, prediction: PredictionResult) -> None:
    # Existing: upsert KernelPrediction
    # ...

    # NEW: write to KernelPredictionHistory
    history = KernelPredictionHistory(
        match_id=match.match_id,
        engine=prediction.engine_name,
        predicted_scores=prediction.predicted_scores,  # dict — JSON column auto-serializes
        outcome_probabilities=prediction.outcome_probabilities,  # dict — JSON column auto-serializes
        confidence=prediction.confidence,
        feature_version=prediction.feature_version,  # NEW column (see 10.1.1)
        trigger="initial",
        created_at=datetime.now(timezone.utc),
    )
    session.add(history)
    session.commit()
```

> **Note:** `predicted_scores` and `outcome_probabilities` are passed as dicts directly, not `json.dumps()`. The SQLAlchemy `JSON` column type handles serialization automatically — same pattern as the existing `KernelPrediction` upsert in `record_prediction`.

### 10.1.1 KernelPredictionHistory Schema Change

The existing `KernelPredictionHistory` table lacks a `feature_version` column. Phase 3 adds it:

```python
class KernelPredictionHistory(KernelBase):
    __tablename__ = "kernel_prediction_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    predicted_scores = Column(JSON)
    outcome_probabilities = Column(JSON)
    confidence = Column(Float)
    feature_version = Column(String)  # NEW
    trigger = Column(String)
    created_at = Column(DateTime)
```

See Section 3.2.1 for migration strategy.

### 10.2 Trigger Values

| trigger | when |
|---------|------|
| `initial` | First prediction for this match |
| `recalibration` | Re-prediction after weight update (future use) |
| `manual` | Manual re-prediction via API (future use) |

---

## 11. `process_outcome` Full Loop

### 11.1 Enhanced Orchestration

```python
def process_outcome(self, match_id: str) -> None:
    outcome = self._adapter.fetch_outcome(match_id)
    if outcome is None:
        return
    self._learning.record_outcome(outcome)
    error = self._learning.compute_error(match_id)
    if error is None:
        return

    match = self._adapter.get_match_identity(match_id)
    competition = match.season.competition.code
    engine = error.engine

    if config.settings.PHASE3_LEARNING_ENABLED:
        self._learning.update_calibration(competition, engine)
        self._learning.update_weights(competition)
        self._learning.engine_score(engine, competition)
```

### 11.2 Feature Flag Gating

When `PHASE3_LEARNING_ENABLED=false`:
- `process_outcome` executes `record_outcome` + `compute_error` only (existing behavior)
- `EloOddsEngine` reads from `FactorRegistry` (but FactorRegistry is never updated, so it always returns default 0.30/0.70)
- All Phase 1/2 behavior unchanged

---

## 12. Configuration

### 12.1 New Settings in `config.py`

```python
PHASE3_LEARNING_ENABLED: bool = _env_bool("PHASE3_LEARNING_ENABLED", "false")
LEARNING_WINDOW_SIZE: int = int(os.getenv("LEARNING_WINDOW_SIZE", "30"))
EWMA_ALPHA: float = float(os.getenv("EWMA_ALPHA", "0.1"))
MIN_SAMPLES_FOR_CALIBRATION: int = int(os.getenv("MIN_SAMPLES_FOR_CALIBRATION", "10"))
MIN_SAMPLES_FOR_ENGINE_SELECT: int = int(os.getenv("MIN_SAMPLES_FOR_ENGINE_SELECT", "5"))
WEIGHT_FLOOR: float = float(os.getenv("WEIGHT_FLOOR", "0.05"))
WEIGHT_CEILING: float = float(os.getenv("WEIGHT_CEILING", "0.95"))
```

### 12.2 `_get_kernel()` Changes

```python
# In _get_kernel():
factor_registry = FactorRegistry()  # Now loads from DB
engine = EloOddsEngine(factor_registry=factor_registry)  # Inject FactorRegistry
learning = KernelLearningService()  # Single shared instance
reg = EngineRegistry(learning_service=learning)  # Share with PredictionKernel
reg.register(engine)
# ...
_get_kernel._instance = PredictionKernel(
    adapter=multi,
    feature_builder=FootballFeatureBuilder(),
    engine_registry=reg,
    factor_registry=factor_registry,
    feature_registry=FeatureRegistry(),
    learning=learning,  # Same instance as EngineRegistry's
)
```

> **Note:** The same `KernelLearningService` instance is shared between `EngineRegistry` (for dynamic engine selection) and `PredictionKernel` (for `process_outcome`). This avoids two independent DB session pools and ensures `EngineRegistry.select("auto")` sees the same calibration/score state that `process_outcome` updates.

---

## 13. Testing Strategy

### 13.1 Test Files

| File | Tests | Description |
|------|-------|-------------|
| `test_learning_weights.py` | 8 | EWMA update, clamp boundaries, insufficient samples skip, DB persistence, per-factor accuracy via predicted_outcome |
| `test_learning_calibration.py` | 7 | Linear regression fit, slope/intercept clamp, insufficient samples, DB persistence |
| `test_engine_score_persistence.py` | 5 | EngineScore DB write, confidence_calibration from calibration table, aggregation |
| `test_engine_dynamic_selection.py` | 4 | Dynamic selection, insufficient samples fallback, multi-engine comparison |
| `test_prediction_history.py` | 3 | record_prediction writes history with feature_version, trigger field correctness |
| `test_process_outcome_full.py` | 5 | Full loop execution, Phase 3 OFF behavior, outcome=None skip |
| `test_elo_engine_weights.py` | 5 | Engine reads FactorRegistry weights, no registry fallback, competition-specific, predicted_outcome in explanation |
| `test_factor_registry_persistence.py` | 5 | DB load, DB write, init_default_factors, competition fallback, KernelFactor composite key |
| `test_db_migration.py` | 2 | Dormant table drop-and-recreate on old schema detection, tables preserved on new schema |

**Total: ~44 tests**

### 13.2 Mocking Strategy

- DB operations tested with in-memory SQLite (per-test isolation, same pattern as Phase 1 kernel tests)
- `query_recent_outcomes` and `query_recent_predictions_with_outcomes` tested with controlled seed data
- `FactorRegistry` tested with real DB session (not mocked) to verify persistence
- `EloOddsEngine` tested with mock `FactorRegistry` for weight reading

---

## 14. Constraints

1. `LearningService` Protocol signatures unchanged (6 methods)
2. `PredictionEngine` Protocol signature unchanged (`predict(features, match)`)
3. `EngineRegistry.select` extends to `select(engine_name, competition=None)` — backward compatible
4. `PHASE3_LEARNING_ENABLED` defaults to OFF
5. `EloOddsEngine` without `FactorRegistry` falls back to 0.30/0.70
6. New table `KernelCalibration` uses `kernel_` prefix
7. Weight clamp range: [0.05, 0.95]
8. Calibration slope clamp: [0.0, 2.0], intercept clamp: [-0.5, 0.5] — hardcoded constants, not configurable
9. Frontend pages must NOT be modified
10. All Phase 1 + Phase 2 tests pass (zero regression)
11. `record_prediction` history write must not affect existing `KernelPrediction` upsert
12. `FactorRegistry` constructor remains compatible with no-arg construction (session_factory has default)
13. `ContributionItem.predicted_outcome` is a new field with default `None` — backward compatible with existing code that creates `ContributionItem` without it
14. `KernelFactor` schema changes from single `factor_id` PK to auto-increment `id` + unique `(factor_id, competition)` — dormant table, safe to drop and recreate
15. `KernelEngineScore` adds `confidence_calibration` column — dormant table, safe to drop and recreate
16. `KernelPredictionHistory` adds `feature_version` column — dormant table, safe to drop and recreate
17. The same `KernelLearningService` instance must be shared between `EngineRegistry` and `PredictionKernel` in `_get_kernel()`
18. `predicted_scores` and `outcome_probabilities` are stored as dicts (JSON column auto-serializes), not `json.dumps()` strings

---

## 15. Future Extensions

### Phase 4+: Calibration Application
Apply `calibrated_prob = slope × raw_prob + intercept` in `EloOddsEngine.predict()` when calibration data exists.

### Phase 4+: Multi-Engine Support
When multiple engines exist (e.g., `PoissonEngine`, `XGBoostEngine`), the dynamic selection and EngineScore tracking become more valuable. The infrastructure built in Phase 3 supports this natively.

### Phase 4+: Streaming/Online Learning
Phase 3 uses batch updates (one update per match outcome). Future work could implement streaming updates for real-time weight adjustment during live matches.

### Phase 5+: Per-Team Calibration
Currently calibration is per competition+engine. Future work could add per-team calibration for teams with systematic biases (e.g., overperforming underdogs).
