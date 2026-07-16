# Sports Prediction OS — Phase 9: Accuracy Sprint

> **Status:** Design approved 2026-07-16
> **Spec author:** Trae (brainstorming skill)
> **Predecessors:** Phase 1 (Kernel extraction), Phase 2 (Football leagues), Phase 3 (Learning loop), Phase 4 (NBA), Phase 5 (MLB/NHL), Phase 6 (Learning dashboard), Phase 7 (Sport Market Bridge A→B→C→D), Phase 8 (Pipeline completion + calibration fusion)

## 1. Goal

Build a backtesting + parameter optimization framework that uses 2 seasons of NBA/MLB/NHL historical data to drive factor weight + Elo parameter tuning, and activate the learning loop for ongoing adaptation. Target: drive prediction accuracy from ~67% toward 72-75%+.

## 2. Background

Phases 1-8 built the complete Sports Prediction OS:
- 10 competitions (wc/ucl/epl/laliga/bundesliga/seriea/ligue1/nba/mlb/nhl)
- 4 engines (EloOddsEngine for football, BasketballEngine, BaseballEngine, HockeyEngine)
- Phase 3 learning loop (EWMA weight updates + calibration regression) — exists but defaults OFF
- Phase 7-8 market bridge + calibration fusion

Current engine factor weights are hardcoded defaults (e.g., BasketballEngine: elo=0.45, home_court=0.15, rest=0.15, form=0.25). No backtesting framework exists for the sports kernel. No historical data has been ingested. The learning loop has never been activated.

## 3. Non-Goals

- Do NOT modify `PredictionKernel`, `domain.py`, `LearningService`, or any engine file (`engines/*.py`)
- Do NOT do football backtesting (ClubElo.com provides only current ratings, no historical Elo)
- Do NOT do real-time parameter updates (optimization is a batch offline job)
- Do NOT build an A/B testing framework (candidate for Phase 10)
- Do NOT modify existing frontend pages
- Do NOT push to origin (standing instruction)

## 4. Architecture Overview

```
Historical APIs → HistoricalDataIngestor → kernel_match_fixtures/results (additive)
                    ↓
            EloTimeMachine → kernel_elo_ratings (additive, baseline params)
                    ↓
ParameterOptimizer (async job, Optuna TPE)
    ↓ for each trial (~100-200 trials):
    BacktestRunner (sync, ~seconds):
        1. Elo replay with candidate Elo params (HFA/K/season_carry)
        2. Compute features (rest/form/pitcher/goalie pre-computed)
        3. Run engine with candidate factor weights
        4. Evaluate: accuracy + Brier + MAE
    ↓
    kernel_optimized_params (new table)
                    ↓
FactorRegistry.apply_optimized_params() → KernelFactor (existing table)
                    ↓
PredictionKernel (zero modification, reads updated weights)
```

### Zero-invasion guarantees

- `PredictionKernel` — zero modification (reads FactorRegistry via Protocol)
- `domain.py` — zero modification
- `LearningService` — zero modification (activated via flag, not modified)
- `engines/*.py` — zero modification (read params from FactorRegistry)
- Feature flag `PHASE9_ACCURACY_SPRINT_ENABLED` defaults to OFF
- Feature flag `PHASE9_LEARNING_ACTIVATED` defaults to OFF

## 5. Data Model

### New table: `kernel_optimized_params`

```python
class KernelOptimizedParams(KernelBase):
    __tablename__ = "kernel_optimized_params"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sport = Column(String, nullable=False, index=True)          # "nba" / "mlb" / "nhl"
    competition = Column(String, nullable=False, index=True)    # "nba" / "mlb" / "nhl"
    factor_weights = Column(Text, nullable=False)               # JSON: {"elo":0.45, "home_court":0.15, ...}
    elo_params = Column(Text, nullable=False)                   # JSON: {"hfa":100, "k_regular":20, ...}
    score = Column(Float, nullable=False)                       # multi-objective score
    accuracy = Column(Float, nullable=False)
    brier_score = Column(Float, nullable=False)
    mae = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False)
    trial_number = Column(Integer, nullable=True)               # Optuna trial number
    status = Column(String, default="candidate")                # "candidate" / "applied" / "archived"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("sport", "competition", "status", name="uq_optimized_params_active"),)
```

### Existing tables (read-only / additive data)

| Table | Usage | Operation |
|-------|-------|-----------|
| `kernel_match_fixtures` | Historical match fixtures | Append 2 seasons data |
| `kernel_match_results` | Historical match results | Append 2 seasons data |
| `kernel_elo_ratings` | Elo ratings | Append baseline Elo (read-only during backtest, dynamic replay) |
| `kernel_factor` | Factor weights | Updated via `apply_optimized_params` after optimization |

## 6. Configuration Changes

### New settings (`backend/app/core/config.py`)

```python
PHASE9_ACCURACY_SPRINT_ENABLED: bool = _env_bool("PHASE9_ACCURACY_SPRINT_ENABLED", "false")
PHASE9_LEARNING_ACTIVATED: bool = _env_bool("PHASE9_LEARNING_ACTIVATED", "false")
PHASE9_OPTIMIZATION_TRIALS: int = int(os.getenv("PHASE9_OPTIMIZATION_TRIALS", "150"))
PHASE9_BACKTEST_SEASONS: str = os.getenv("PHASE9_BACKTEST_SEASONS", "2023-24,2024-25")  # comma-separated
PHASE9_OPTIMIZATION_INTERVAL_MIN: int = int(os.getenv("PHASE9_OPTIMIZATION_INTERVAL_MIN", "0"))  # 0 = manual only
PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN: int = int(os.getenv("PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN", "0"))  # 0 = manual only
```

When `PHASE9_LEARNING_ACTIVATED=true`:
- Overrides `PHASE3_LEARNING_ENABLED` to true (learning loop activates)
- Schedules `_job_update_weights_weekly` (if interval > 0)
- Schedules `_job_reoptimize_monthly` (if interval > 0)

## 7. Component Interfaces

### 7.1 HistoricalDataIngestor

```python
# backend/app/services/historical_data_ingestor.py

class HistoricalDataIngestor:
    """Fetches historical matches + results from existing sports APIs."""

    async def ingest_season(self, sport: str, season: str) -> dict:
        """Fetch + store historical matches + results for one season.
        
        Args:
            sport: "nba" / "mlb" / "nhl"
            season: e.g., "2024-25" for NBA/NHL, "2024" for MLB
            
        Returns:
            {"matches": N, "results": N, "errors": [...]}
            
        Delegates to existing sport-specific adapters:
        - NBA: balldontlie.io (Teams/Players/Games endpoints)
        - MLB: statsapi.mlb.com (schedule endpoint)
        - NHL: api-web.nhle.com (schedule endpoint)
        """
```

### 7.2 EloTimeMachine

```python
# backend/app/kernel/backtest/elo_time_machine.py

@dataclass(frozen=True)
class EloParams:
    hfa: float
    k_regular: float
    k_playoff: float
    season_carry: float  # 0.0 = full reset, 1.0 = no regression
    initial: float       # default 1500
    league_avg_total: float  # only used for display, not Elo computation

class EloTimeMachine:
    """Replays Elo ratings from season start with given parameters."""

    def replay(self, sport: str, matches: list[MatchRecord], elo_params: EloParams) -> dict[str, dict[str, float]]:
        """Replay Elo from season start with given params.
        
        Returns:
            {match_id: {"home_elo": float, "away_elo": float}} snapshot before each match.
            
        Algorithm:
            1. Initialize all teams to elo_params.initial
            2. Sort matches by date
            3. For each match:
               a. Record current Elo snapshot
               b. Compute expected = 1 / (1 + 10^((opp_elo + hfa - elo) / 400))
               c. actual = 1.0 if home_win else 0.0
               d. new_elo = old_elo + K * (actual - expected)
               e. At season boundary: elo = elo * season_carry + initial * (1 - season_carry)
        """
```

### 7.3 BacktestRunner

```python
# backend/app/kernel/backtest/runner.py

@dataclass(frozen=True)
class BacktestParams:
    factor_weights: dict[str, float]    # {"elo": 0.45, "home_court": 0.15, ...}
    elo_params: dict[str, float]        # {"hfa": 100, "k_regular": 20, ...}

@dataclass(frozen=True)
class BacktestResult:
    accuracy: float
    brier_score: float
    mae: float
    sample_count: int
    score: float  # 0.5*accuracy + 0.3*(1-brier) + 0.2*(1-mae)
    predictions: list[dict]  # per-match prediction details (optional, for debugging)

class BacktestRunner:
    """Runs backtest with given parameters over historical matches."""

    def run(self, sport: str, season_train: str, season_test: str, params: BacktestParams) -> BacktestResult:
        """Run backtest with given params. Synchronous, ~seconds.
        
        Flow:
            1. Load historical matches (date-sorted): season_train + season_test
            2. Replay Elo with candidate Elo params (EloTimeMachine)
            3. For each match in season_test:
               a. Read Elo at that point (from replay snapshot)
               b. Read pre-computed features (rest/form/pitcher/goalie)
               c. Run engine with candidate factor weights
               d. Record prediction probabilities
            4. Compare predictions vs actual outcomes
            5. Return BacktestResult
            
        Time-series split: season_train for Elo accumulation only, season_test for evaluation.
        Prevents data leakage.
        """
```

### 7.4 ParameterOptimizer

```python
# backend/app/kernel/parameter_optimizer.py

class ParameterOptimizer:
    """Bayesian optimization over factor weights + Elo params using Optuna TPE."""

    async def optimize(self, sport: str, n_trials: int = 150) -> dict:
        """Async optimization job via optimization_task_manager.
        
        Returns:
            {"best_score": float, "best_params": {...}, "trials": int, "sport": str}
            
        Each trial:
            1. Sample factor weights (constrained: sum=1.0) via trial.suggest_float
            2. Sample Elo params within bounds
            3. Run BacktestRunner with candidate params
            4. Return multi-objective score
            
        Search space per sport:
            NBA: 4 factors (elo, home_court, rest, form) + 3 Elo params (HFA, K_reg, K_playoff)
            MLB: 5 factors (elo, home_court, rest, form, starting_pitcher) + 4 Elo params
            NHL: 5 factors (elo, home_court, rest, form, goalie) + 4 Elo params
        """

    def _sample_factor_weights(self, trial: optuna.Trial, sport: str) -> dict[str, float]:
        """Sample factor weights with sum=1.0 constraint using Dirichlet-like approach."""

    def _sample_elo_params(self, trial: optuna.Trial, sport: str) -> dict[str, float]:
        """Sample Elo params within sport-specific bounds."""
```

### 7.5 OptimizedParamsStore

```python
# backend/app/kernel/optimized_params_store.py

class OptimizedParamsStore:
    """Stores and applies optimized parameter sets."""

    def save_candidate(self, sport: str, competition: str, factor_weights: dict,
                       elo_params: dict, score: float, accuracy: float,
                       brier_score: float, mae: float, sample_count: int,
                       trial_number: int | None = None) -> dict:
        """Save a candidate parameter set. Returns the saved record."""

    def get_applied(self, sport: str, competition: str) -> dict | None:
        """Get the currently applied parameter set for a sport/competition."""

    def get_candidates(self, sport: str | None = None, limit: int = 50) -> list[dict]:
        """List candidate parameter sets, optionally filtered by sport."""

    def apply(self, params_id: int) -> dict:
        """Apply a candidate parameter set to FactorRegistry.
        
        1. Archive any currently-applied params for this sport/competition
        2. Update KernelFactor rows via FactorRegistry.update_weight()
        3. Mark this candidate as "applied"
        4. Return the applied record
        """
```

## 8. Scheduler Jobs

### 8.1 `_job_update_weights_weekly`

When `PHASE9_LEARNING_ACTIVATED=true` and `PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN > 0`:

```python
async def _job_update_weights_weekly():
    """Weekly weight update via Phase 3 learning loop.
    
    For each sport/competition:
        1. Query last 30 resolved predictions
        2. Call LearningService.update_weights(engine, competition)
        3. Log weight changes
    """
```

### 8.2 `_job_reoptimize_monthly`

When `PHASE9_LEARNING_ACTIVATED=true` and `PHASE9_OPTIMIZATION_INTERVAL_MIN > 0`:

```python
async def _job_reoptimize_monthly():
    """Monthly re-optimization of parameters.
    
    For each sport (nba/mlb/nhl):
        1. Check if enough new data accumulated (>= 30 new resolved matches)
        2. If yes, trigger ParameterOptimizer.optimize(sport, n_trials=150)
        3. If best_score > current_applied_score, auto-apply
    """
```

## 9. API Endpoints

All endpoints gated by `PHASE9_ACCURACY_SPRINT_ENABLED` (503 when false).

| Method | Path | Function | Auth |
|--------|------|----------|------|
| POST | `/api/sport-optimization/ingest` | Trigger historical data ingestion | require_write_key |
| POST | `/api/sport-optimization/run` | Trigger parameter optimization (async) | require_write_key |
| GET | `/api/sport-optimization/status/{task_id}` | Query optimization task status | — |
| GET | `/api/sport-optimization/params/{sport}` | Get current optimized params for sport | — |
| GET | `/api/sport-optimization/params` | List all sports' optimized params | — |
| POST | `/api/sport-optimization/apply/{params_id}` | Apply optimized params to FactorRegistry | require_write_key |

### Request/Response schemas

```python
class IngestRequest(BaseModel):
    sport: str  # "nba" / "mlb" / "nhl" / "all"
    seasons: list[str]  # e.g., ["2023-24", "2024-25"]

class OptimizationRequest(BaseModel):
    sport: str  # "nba" / "mlb" / "nhl" / "all"
    n_trials: int = 150

class OptimizationStatusResponse(BaseModel):
    task_id: str
    status: str  # "pending" / "running" / "completed" / "failed"
    sport: str
    progress: dict  # {"trials_completed": N, "best_score": float}
    result: dict | None
```

## 10. Frontend

### New components (no existing page modifications)

- `frontend/src/components/sports/optimization/OptimizationDashboard.tsx` — displays optimization task status, current params, historical optimization results
- `frontend/src/lib/optimization-api.ts` — API client

### New route

- `/sports/optimization` — standalone page mounting `OptimizationDashboard`

## 11. Testing Strategy

TDD strictly followed (RED → GREEN → COMMIT per task).

| Component | Tests | Key tests |
|-----------|-------|-----------|
| HistoricalDataIngestor | 5 | ingest 1 season, multi-season, API failure, idempotent re-ingest, data integrity |
| EloTimeMachine | 6 | single-season replay, season-boundary regression, parameter sensitivity, initial Elo, HFA effect, K-factor effect |
| BacktestRunner | 7 | baseline backtest, parameter change impact, time-series split, empty data, single match, multi-season, missing features |
| ParameterOptimizer | 5 | Optuna convergence, search space constraints, async execution, cancellation, multi-objective score |
| OptimizedParamsStore | 6 | save candidate, get applied, apply params, archive old params, idempotent apply, concurrent safety |
| API Routes | 6 | 503 gating, ingest trigger, optimization trigger, status query, params query, apply params |
| Frontend | 4 | loading state, empty state, optimization results display, params table |
| **Total** | **39** | |

## 12. Phase Boundaries

### Phase 9 scope (this spec)
- Historical data ingestion (NBA/MLB/NHL, 2 seasons)
- Backtesting framework (EloTimeMachine + BacktestRunner)
- Parameter optimization (Optuna TPE, async job)
- Optimized params storage + application
- Learning loop activation (flag + scheduler)
- 6 API endpoints
- Frontend monitoring dashboard
- 39 tests

### Out of scope (future phases)
- WebSocket real-time price push (Phase 10 candidate)
- Kalshi sports market integration (Phase 11 candidate)
- Futures/championship markets (Phase 12 candidate)
- A/B testing framework for parameter sets
- Football backtesting (blocked by ClubElo.com having no historical data)
- Real-time parameter updates

## 13. Integration Points

- **FactorRegistry** — `update_weight()` called by `OptimizedParamsStore.apply()`
- **LearningService** — `update_weights()` called by `_job_update_weights_weekly` (no modification, just activated)
- **optimization_task_manager** — reused for async optimization jobs
- **Sport-specific adapters** — reused for historical data ingestion (NBA/MLB/NHL adapters)
- **Engines** — called via Protocol interfaces (zero modification)

## 14. Success Criteria

1. `HistoricalDataIngestor.ingest_season()` successfully fetches 2 seasons × 3 leagues historical data (~7,380 matches total)
2. `BacktestRunner.run()` with baseline params produces `BacktestResult` with accuracy/Brier/MAE
3. `ParameterOptimizer.optimize()` converges after 150 trials, best score > baseline score
4. `OptimizedParamsStore.apply()` updates `KernelFactor`, existing predictions pass with zero regression
5. `PHASE9_LEARNING_ACTIVATED=true` activates learning loop + scheduler jobs; flag=false keeps all existing behavior
6. All existing tests pass (zero regression), 39 new tests pass

## 15. Estimate

- 7 tasks via Subagent-Driven Development
- ~25 files (15 created + 10 modified)
- ~3,500 lines
- 39 tests
