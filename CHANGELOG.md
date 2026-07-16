# Changelog

## v0.4.0 (2026-07-16)

### Sports Prediction OS — Phase 1-13

13-phase architectural evolution from a World Cup-only prediction module into a
multi-sport Prediction OS with a Protocol-based kernel, market bridge, learning
loop, and real-time push. ~134 commits, ~6,872 matches/year coverage (10
competitions: World Cup + 6 football leagues + NBA + MLB + NHL), accuracy
target lifted from ~67% toward 72-75%+.

#### Phase 1 — Prediction Kernel Extraction
- Extracted `backend/app/kernel/` with Protocol interfaces (DataAdapter,
  FeatureBuilder, PredictionEngine, LearningService) and frozen dataclass
  domain model (`domain.py`)
- `prediction_kernel.py` is sport-agnostic — zero imports of `world_cup_*` /
  `epl_*` / `nba_*`
- WorldCupAdapter bridges to existing `world_cup_*` modules internally
- `KERNEL_PREDICTION_ENABLED` feature flag defaults OFF; falls back to legacy
  `world_cup_prediction_pipeline` when false
- New `kernel_` prefix DB tables; existing `world_cup_predictions.db` untouched

#### Phase 2/2b — Football Leagues (UCL → EPL → La Liga → Bundesliga → Serie A → Ligue 1)
- `PHASE2_LEAGUES_ENABLED` flag (OFF); MultiAdapter routes by match_id prefix
  (`wc-` / `ucl-` / `epl-` / `laliga-` / `bundesliga-` / `seriea-` / `ligue1-`)
- Football-Data.org client parameterized for competition codes
- ClubElo.com service with 7-day cache TTL + 1s request interval
- Adapter shared utilities as stateless functions in `_shared.py`

#### Phase 3 — Unified Learning Loop
- Closed loop: outcome → error → calibration → weight update → engine score → next prediction
- `PHASE3_LEARNING_ENABLED` flag (OFF); `FactorRegistry` DB-backed
- `learning_service.py` `compute_error()` and `update_weights()` dynamically
  iterate factor keys (no hardcoded `elo`/`odds`)

#### Phase 4 — NBA Integration + BasketballEngine
- `PHASE4_NBA_ENABLED` flag (OFF); `BALLDONTLIE_API_KEY` empty by default
  (NBAAdapter auto-disabled when not configured)
- NBA Elo self-computed (HFA=100, K_regular=20, K_playoff=30, regression=0.75)
- BasketballEngine: Bradley-Terry binary model, 4 factors
  (elo 0.45 / home_court 0.15 / rest 0.15 / form 0.25)
- MultiFeatureBuilder mirrors MultiAdapter prefix-dispatch pattern

#### Phase 5 — MLB / NHL Integration
- `PHASE5_MLB_ENABLED` / `PHASE5_NHL_ENABLED` flags (OFF)
- MLB: `statsapi.mlb.com` (official free API, no key, 1 req/s)
- NHL: `api-web.nhle.com` (official free API, no key, 1 req/s)
- BaseballEngine: 5 factors (elo 0.30 / home_court 0.10 / rest 0.15 / form 0.20 / starting_pitcher 0.25)
- HockeyEngine: 5 factors (elo 0.35 / home_court 0.15 / rest 0.15 / form 0.20 / goalie 0.15)
- Weight redistribution when factor unavailable
- NHL overtime/shootout stored in `raw["custom"]` (binary outcome preserved)

#### Phase 6 — Learning Dashboard
- 7 tasks via Subagent-Driven Development; 22 files (+2,603 lines)
- 74 tests pass (42 backend + 32 frontend)
- Reliability chart, prediction history, calibration panel, engine scores
- `bins` parameter clamped to 5-20 range
- Tab state syncs with URL `?tab=` parameter

#### Phase 7 — Sport Market Bridge Layer (4 Subprojects)
- **A**: Three-layer matching engine (rule ≥0.9 → LLM ≥0.85 → manual gate);
  fail-closed `/latest` at 2 layers; 40 files (+3,577 lines)
- **B**: Edge Detector — aligns model probs with market-implied probs to
  compute trust-weighted `adjusted_edge`; writes to `kernel_sport_edges` table
- **C**: Sport Recommendation Engine — `SportRecommendationService` + 3 GET endpoints
- **D**: Market Settlement Feedback Loop — parallel-channel design writes only
  to `kernel_market_settlements` + `kernel_market_calibrations` tables (never
  touches Phase 3's `KernelCalibration`); Brier-style error signals;
  calibration regression clamps slope [0.0, 2.0] + intercept [-0.5, 0.5]

#### Phase 8 — Pipeline Completion + Calibration Fusion
- `PHASE8_CALIBRATION_FUSION_ENABLED` flag (OFF); EdgeDetectorService delegates
  to CalibrationFusionService when on
- New `kernel_traditional_odds_snapshots` table (separate from `kernel_market_snapshots`)
- Filled `_job_capture_market_snapshots` + `_job_fetch_traditional_odds` stubs
- Frontend chart upgraded to recharts LineChart with Polymarket comparison

#### Phase 9 — Accuracy Sprint
- `PHASE9_ACCURACY_SPRINT_ENABLED` flag (OFF)
- Backtesting framework (`backtest/runner.py` + `elo_time_machine.py`)
- Bayesian parameter optimization via Optuna TPE (`parameter_optimizer.py`)
- Multi-objective weighted scoring (accuracy + brier + mae)

#### Phase 10 — WebSocket Real-time Price Push
- `PHASE10_REALTIME_PUSH_ENABLED` flag (OFF)
- `ConnectionManager` singleton manages per-match WebSocket subscriber sets
- `/ws/*` endpoints return 503 when disabled

#### Phase 11 — Kalshi Sports Market Integration
- `PHASE11_KALSHI_SPORTS_ENABLED` flag (OFF)
- Kalshi public read-only API at `settings.KALSHI_API_URL` (no auth)
- Fail-closed source; polite 1s request interval

#### Phase 12 — Futures / Championship Markets
- `PHASE12_FUTURES_MARKETS_ENABLED` flag (OFF)
- Multi-leg N-outcome championship markets (one event → N contracts, one per team)
- 2 new DB tables (`kernel_futures_links` + `kernel_futures_snapshots`)
- 3 GET endpoints + 2 scheduler jobs
- Kalshi futures series prefixes (KXNBACHAMP / KXMLBCHAMP / KXNHLCHAMP /
  KXSOCCERWCS / KXSOCCERUCL) → (competition, championship_type) tuples

#### Phase 13 — Pre-existing Test Isolation Fix
- `backend/tests/conftest.py` autouse fixture resets `kernel_db` and
  `connection_manager` module-level singletons before AND after each test
- Purely additive (no existing test files modified)
- 101 tests pass forward AND reverse order

### Project Review Fixes
- **C1**: WebSocket `broadcast_to_match` snapshots set before iterating
  (prevents `RuntimeError: Set changed size during iteration`)
- **C2**: kernel_db 3 query functions missing `finally: session.close()`
  (resource leak)
- **C3/C4**: MLB/NHL adapter pitcher ERA and goalie save_pct defaults changed
  from hardcoded values to None (engine now correctly detects unavailable
  data and redistributes weight per Phase 5 constraint)
- **C5**: `futures_link_store.get_latest_snapshots` N+1 query replaced with
  single GROUP BY + JOIN
- **C7**: kernel_db 8 `except Exception` blocks now log warnings with
  `exc_info=True` (was silently swallowing DB errors)
- **C8**: `_parse_kalshi_price` returns None instead of 0.5 when no price data
  (callers skip contracts instead of injecting synthetic 50% probability)
- **C11/C12/C13**: `.env.example` — added missing `KERNEL_PREDICTION_ENABLED`
  master switch; aligned `EVENT_DISCOVER_LIMIT` (10→100) and
  `WORLD_CUP_SOURCE_ENABLED` (true→false) with `config.py` defaults
- Dead code cleanup: `_FACTOR_NAMES` dict, unused `seed_elo_from_games` imports

### Hard Constraints Honored
- PredictionKernel / domain.py / LearningService / engines/*.py — zero
  modifications across all phases (Protocol-based decoupling)
- All feature flags default OFF (backward compatibility)
- New DB tables use `kernel_` prefix; existing tables untouched
- TDD strictly followed for backend DB functions (RED → GREEN)

---

## v0.3.0 (2026-06-20)

### Production Hardening
- **Security**: CORS origins configurable via `CORS_ALLOWED_ORIGINS`, default localhost-only
- **Security**: API write key authentication (`require_write_key` middleware, `X-API-Key` header)
- **Security**: Rate limiting added (`InMemoryRateLimitMiddleware`, 120 req/60s per client+path)
- **Ops**: `/api/health` endpoint (returns scheduler status, loop health, failed runs)
- **Ops**: Rotating file logging (`RotatingFileHandler`, 10MB×5, configurable)
- **Ops**: `misfire_grace_time` extended to 86400s (24h), configurable
- **Ops**: systemd unit with `Restart=always`
- **Ops**: Daily backup script + systemd timer (`scripts/backup_stores.py`)
- **Ops**: Health check systemd timer (pings `/api/health` every 5 min)
- **Ops**: Dockerfile + docker-compose.yml with healthcheck
- **CI**: GitHub Actions workflow (compileall + unittest)

### Reality Feedback Loop
- Resolve write order hardened: score_prediction before resolve_event (crash-safe)
- Orphan prediction reconciliation before each auto-resolve
- Verified link seeding on prediction freeze
- Trust floor (`DIAGNOSIS_TRUST_FLOOR`, default 0.1) prevents absorbing state
- Loop run ledger (`loop_run_store.py` + `/api/events/loop/status`)

### Refactoring
- DRY: `_now()` unified to `utc_now()` in `utils/helpers.py` (4→1 definitions)
- DRY: `_clamp01` unified to `clamp01()` in `utils/helpers.py` (2→1 definitions)
- Config: `LLM_CONCURRENCY` replaces hardcoded `asyncio.Semaphore(4)`
- Scoring functions extracted to `scoring_service.py`
- Legacy `openai_service.py` client now has `timeout=60.0, max_retries=2`
- Dependencies pinned with `>=lower,<upper` constraints

### Documentation
- New: `docs/dev/ARCHITECTURE.md` (C4 diagrams, data flow, deployment)
- New: `docs/dev/adr/001-json-file-store.md`
- New: `docs/dev/adr/002-nextjs-static-export.md`
- New: `docs/dev/adr/003-fastapi-over-flask.md`
- New: `docs/ops/RUNBOOK.md` (monitoring, backup, process supervision)
- Cleanup: 6 code review files moved from `docs/user/` → `docs/archive/`
- Updated: test count in `Event Intelligence Platform.md` (141→503)

---

## v0.2.0 (2026-Q2)

- Multi-source event discovery (Polymarket + Manifold + Kalshi)
- AI probability analysis pipeline
- Multi-source auto-resolution (contract-first settlement)
- Calibration feedback loop (opt-in)
- Next.js dashboard

---

## v0.1.0 (2025-Q4)

- Initial FastAPI backend
- JSON file event store
- DeepSeek LLM integration
- Polymarket event source
