# Changelog

## Unreleased

### ADR-004 Esports/LoL Accepted
- First title: LoL; sport `lol`, prefix `lol-`
- Production adapter blocked on official/partner API gates
- Design note: `docs/superpowers/specs/2026-07-22-esports-lol-adapter-design.md`
- ESPORTS_BOUNDARY synced; no adapter code in this change


### Kernel list ↔ 竞猜联赛闭环
- `/sports` CompetitionChips for kernel leagues (sport-scoped)
- Match list badge deep-links to `/sports/betting/{id}` when catalog known
- Catalog helpers: normalizeCompetitionCode, getCompetitionByCode, kernelCompetitionChips


### Operator key deep-link for 竞猜落地页
- `#operator-key` opens nav OperatorKeyControl edit form
- Landing listens for credentials event so 同步赛程 appears after authorize
- Link「打开顶部授权」from landing when write key missing


### 竞猜落地页：同步按钮补全 + 今日赛程预览
- Wire operator 同步赛程 button (was imported but incomplete)
- Match preview list (up to 5) with deep links
- Runtime adapter prefix from GET /betting/status


### GET /api/betting/status diagnostic
- Read-only flags + MultiAdapter `registered_prefixes` (no write key)
- Hub shows runtime adapter list when status is available
- verify_local_stack + RUNBOOK smoke include `/betting/status`


### Operator schedule sync + esports ADR-004
- `POST /api/predictions/schedule/sync` (write key; optional sport/competition)
- MultiAdapter.sync_schedule accepts ScheduleFilter short-circuit
- Landing: 同步赛程 button when operator key present
- Catalog flags aligned to PHASE2 / PHASE4_NBA / PHASE5_*
- ADR-004 esports data adapter (Proposed)


### 竞猜落地页 match count + RUNBOOK 联赛启用
- Client `CompetitionLanding`: live catalog adapter status + today's match count via `useMatches`
- `useMatches(null)` skips fetch (coming_soon / non-kernel)
- RUNBOOK: Betting / 联赛赛程 flags + smoke curls


### 竞猜 hub live catalog merge + adapter badges
- Hub is client: merges static catalog with `GET /api/betting/catalog`
- Cards show adapter_likely badges; flag strip (Kernel / EPL / 五大联赛)
- Offline fallback keeps static catalog when API is down


### MultiAdapter competition/sport schedule short-circuit
- Shared `kernel/competition_codes.py` (aliases + prefix maps)
- `ScheduleFilter.sport` optional field
- `MultiAdapter.fetch_schedule` only hits matching league adapters
- Catalog payload: `flags` + per-competition `adapter_likely`
- list_matches passes sport/competition into ScheduleFilter early


### 竞猜 competition 过滤 + catalog API
- `GET /api/predictions/matches?competition=` (+ aliases: pl→epl, wc→world_cup, serie-a→serie_a)
- `GET /api/betting/catalog` and `/catalog/{id}` from `kernel/betting_catalog.py`
- FE: `useMatches({ sport, competition })`, Kernel list `?competition=` chip, landing/kernel links
- `docs/dev/ESPORTS_BOUNDARY.md` — no fake esports markets until data sources exist


### 竞猜模块 IA 起步
- `lib/betting/competition-catalog.ts`: static catalog (world cup, big-five aliases, NBA/MLB/NHL, esports placeholder, tool links)
- `/sports/betting` hub: sectioned cards + dual-track note
- `/sports/betting/[competitionId]`: competition landing (kernel link / world-cup / coming_soon, no fake odds)
- Nav: 竞猜中心 first in Sports group; world-cup nav entry removed (route kept)
- Kernel list: `?sport=` query sync; banners link 竞猜中心


### Ops runbook + local smoke + CI typecheck
- RUNBOOK: Prometheus series table, Grafana import path, DRIFT_ALERT_* /
  SCHEDULER_FAILURE_ALERT_* dispatch notes, matching-eval CLI snippet
- `verify_local_stack.py`: probe `/metrics`, sport-markets pending,
  quality-metrics summary/drift/alerts; hint lines for bridge + alert flags
- CI frontend job: run `npm run typecheck` before tests (catches casing/import drift)


### Frontend typecheck fixes + alert env docs
- World Cup page: import missing `SportTrackBanner`
- FuturesDashboard: import missing `ScrollableTable`
- predictions-api: import `getOperatorApiKey` / `getOperatorId`
- match-detail-panel: use `prediction.engine` (not non-existent `engine_name`)
- app-nav HotNewsTicker: annotate `display`/`loop` as `TickerItem[]` so fallback items keep optional `delta`/`href`
- Align edge/settlement/realtime imports to on-disk lowercase filenames (TS1261 casing)
- `.env.example`: document `DRIFT_ALERT_*` and `SCHEDULER_FAILURE_ALERT_*` (default OFF)


### Three-layer matching eval set + scheduler failure alerts (P1-SB1 / E8)
- `scripts/eval_sport_market_matching.py`: rule / LLM / manual matcher eval against labeled JSONL with precision / recall / F1
- `data/eval/sport_market_link_eval.sample.jsonl`: 6-case sample covering rule hit / partial / LLM hit / reject / pending / manual
- `app/services/scheduler_failure_alert_dispatcher.py`: webhook + Sentry breadcrumb + log, gated by `SCHEDULER_FAILURE_ALERT_ENABLED` (default OFF), per-job_name cooldown
- `scheduler.py._finish_run` calls dispatcher on failed status (metrics + Sentry capture unchanged)
- `docs/ops/grafana/pmrf-overview.json`: Grafana dashboard for scheduler health / drift / overlay latency / LLM cost / decision quality
- Config: `SCHEDULER_FAILURE_ALERT_ENABLED`, `SCHEDULER_FAILURE_ALERT_WEBHOOK_URL`, `SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS`
- Tests: `test_eval_sport_market_matching.py` (16/16), `test_scheduler_failure_alert_dispatcher.py` (7/7)


### Pending auto-verify UI + registry altitude + conditional cal API
- PendingReviewQueue: dry-run / apply auto-verify buttons
- Football factor registry seeds `altitude` (soft-only list)
- `POST /predictions/calibration/conditional` fits conf+stage buckets


### PPDA soft + stage calibration + auto-verify (P1-F6 / V5 / V2)
- MultiFactor possession channel accepts `ppda_home/away` (lower = better press)
- Stage-bucket calibration keys `{comp}#s_{regular|knockout}`; meta.stage in explanation
- `POST /sport-markets/pending/auto-verify` + store helper (flag OFF by default)


### Edge factor attribution (P1-V3)
- `extract_factor_drivers` ranks prediction explanation by outcome impact
- Edge detect/latest attach `factor_drivers` + `factor_attribution`
- Edge detail FE chips; recommendation rationale may include 主导因子


### Reliability ECE + market price audit (P1-X1 / P1-V1)
- `compute_reliability_bins` returns `ece`, `max_calibration_error`, `sample_count`
- FE ReliabilityChart shows ECE badge
- `GET /sport-markets/links/{id}/audit` and `/matches/{id}/audit` price-path summary
- Match detail `MarketPriceAuditPanel`


### Soft totals all sports + rec guardrails + altitude (P1-O1/SB3/F7)
- Basketball/Hockey/Baseball engines emit soft O/U via `soft_totals_from_scores`
- Sport recommendations: `guardrail_flags` / `policy_notes` (stale/liquidity/trust demote)
- Football multi-factor soft `altitude` when `venue_altitude_m` ≥ 1500


### Soft totals/BTTS + baseball platoon (P1-O1 / P1-M4)
- `soft_totals_btts_analysis` (independent Poisson) in multi-factor + EloOdds `betting_analysis`
- FE `SoftTotalsPanel` on match detail
- BaseballEngine soft `platoon` from `platoon_ops_*` / `platoon_advantage_home` (weight 0.05)


### Model vs market disagreement diagnosis (P1-V3)
- Sport recommendation rationale appends 分歧诊断 (gap / trust / liquidity / stale)
- Edge detail FE shows diagnosis line when |Δ| ≥ 3pp (or high priority)


### Football rest density + injury custom (P1-F2 / P1-F3)
- Adapter flags `b2b_*` (rest≤1) and `schedule_congested_*` (rest≤2)
- MultiFactor rest: extra edge penalty for b2b / midweek congestion asymmetry
- Injury factor reads `custom.injury_impact_*` when player fields empty


### Referee feed + conditional calibration apply (P1-F8 / P1-V5)
- Football adapter `enrich_referee_features`: pass-through rate/bias + optional static map
- `KERNEL_CONDITIONAL_CALIBRATION_ENABLED` (default OFF): Kernel applies bucket slope/intercept to home_win then renormalizes
- Annotation in `betting_analysis.conditional_calibration`


### Football referee soft factor (P1-F8)
- FootballMultiFactorEngine: soft `referee` from `custom.referee_home_win_rate` / `referee_home_bias`
- Weight 0.02 (profiles rebalanced); unavailable when no referee stats
- FactorRegistry seed + FE label 裁判倾向


### Conditional calibration by confidence (P1-V5)
- `confidence_bucket` low/mid/high; store rows as `{competition}#c_{bucket}`
- `KernelLearningService.update_calibration_by_confidence` + `get_conditional_calibration`
- Global `update_calibration` best-effort refreshes buckets after fit


### NBA playoff stage HFA (P1-B5)
- BasketballEngine: playoff/postseason uses `NBA_ELO_HFA_PLAYOFF` (default 90) and
  `NBA_HOME_COURT_PLAYOFF` (default 0.55) vs regular 100 / 0.58
- Config env vars: `NBA_ELO_HFA_PLAYOFF`, `NBA_HOME_COURT_PLAYOFF`


### Confidence breakdown API + FE (P1-X3)
- confidence_breakdown() returns decision_strength / completeness / agreement / market_damp
- Injected into FootballMultiFactor / EloOdds / Basketball / Baseball / Hockey via betting_analysis
- FE SportConfidencePanel prefers API breakdown; shows market damp when <1


### Odds traditional vs market summary (P1-O4)
- TraditionalOddsChart: latest implied-prob divergence table (传统−市场, ≥5pp highlight)
- Chinese outcome labels on series titles

### Football possession form proxy (P1-F6 feed)
- Adapter soft-fills `possession_*` from form share when true stats missing


### FactorRegistry soft seeds + sport confidence UI (P1-E2 / P1-X3)
- Seed football multi-factor soft factors: travel/xg/market_value/possession (no global elo/odds)
- NBA/MLB/NHL seeds include travel/injury/net_rating/park/bullpen/weather/attack_share
- Competition aliases: seriea↔serie_a, ligue1↔ligue_1
- NHL adapter: form-scaled GF + xg_for proxy for attack_share
- FE `SportConfidencePanel` on match detail (strength / completeness / agreement)


### Football possession soft factor (P1-F6)
- FootballMultiFactorEngine: soft `possession` from custom possession_* or shots_*
- Weights/profiles rebalanced to include possession (sum ≈ 1.0)

### Hockey attack share (P1-H1)
- HockeyEngine: soft `attack_share` factor (corsi% preferred, else xg_for, else GF proxy)
- Default weights rebalanced; FE label 进攻份额

### Football xG + market value soft factors (P1-F5)
- FootballMultiFactorEngine: soft `xg` (custom.xg_* attack-rate share) and `market_value` (log squad-value ratio)
- Default weights + competition profiles include xg/market_value (sum ≈ 1.0)
- Adapter: cache-only Transfermarkt `get_cached_market_value` → team/custom market_value_*
- FE FactorBreakdownTable labels for xg / market_value
- Tests updated for 9-factor explanation + xG/MV soft behaviour

### Edge review priority soft feedback (P1-O5)
- SportRecommendationService: compute `review_priority` from edge signals; demote act tiers (critical/high); raise risk; rationale prefix
- API `_rec_to_dict` includes `review_priority`; RecommendationCard shows priority badge when non-normal
- Tests: `test_recommendation_priority_soft.py`

### Multi-sport soft factors (P1-B1/B3, P1-M1–M3, P1-H3, P1-F7)
- Shared `app/sports/_shared/team_geo.py`: NBA/NHL/MLB + national football city coords, haversine km, timezone offset, `travel_prob_home`
- NBA/NHL/MLB/football adapters inject `travel_km_away` / `timezone_offset_hours_away` into FeatureSet custom
- BasketballEngine: `travel` + soft `injury` factors (weight redistribute when missing)
- BaseballEngine: wire `park` + `bullpen` (+ soft `weather` temp/wind); MLB adapter bullpen_era proxy from team ERA
- HockeyEngine: `travel` factor for cross-zone (incl. Canada) fatigue
- FootballMultiFactorEngine: new `travel` factor + competition profile weights
- Feature builders pass through `travel_distance_km`; FE factor labels for travel/bullpen/weather
- Edge discrepancies list API: `review_priority` / trust / liquidity + sort by priority then |edge|
- FE Edge table priority column; history payload includes review fields
- Tests: `test_team_geo.py`, `test_sport_factors_travel_park.py`

### Football multi-factor engine (P1-E1)
- Added `FootballMultiFactorEngine` (`football_multi_factor`): fuses elo, odds, form, rest, injury, h2h with missing-factor weight redistribution
- Feature flag `FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED` (default OFF); requires `KERNEL_PREDICTION_ENABLED`
- `FactorRegistry.ensure_competition_factors` seeds form/rest/injury/h2h for football competitions without changing global elo/odds 0.30/0.70
- New `get_competition_weight()` avoids multi-factor fusion picking up EloOddsEngine global weights
- Docs: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` tracks remaining backlog

### Dixon-Coles / GBM / Ensemble into Kernel (P1-E5–E7)
- `DixonColesEngine` (`dixon_coles`) + `DIXON_COLES_ENGINE_ENABLED`
- `GbmEngine` (`gbm`) wraps legacy LightGBM path + `GBM_ENGINE_ENABLED`
- `EnsembleEngine` (`ensemble`) inverse-Brier fusion + `FOOTBALL_ENSEMBLE_ENGINE_ENABLED`
- All default OFF; register only when Kernel is enabled

### Phase 9 optimization wiring (P1-A3)
- `POST /api/sport-optimization/run` now loads historical matches from kernel DB,
  time-series splits, and runs `ParameterOptimizer.optimize_sync` in a background task
- `app/kernel/backtest/match_loader.py` for DB → backtest match dicts
- CLI: `backend/scripts/run_phase9_optimize.py`

### Situational feature feed + Phase 15 UX (P1-F* / P1-FE*)
- Football adapters enrich form / h2h / rest / xG-proxy after Elo+odds fetch
- Match detail: engine selector, 503 Kernel disabled banner, predict error alert
- Optimization dashboard: NBA/MLB/NHL/all only; Phase 9 disabled banner on 503

### WebSocket URL + Edge detect + club form
- `buildWsUrl`: Next dev (:3000) connects WS to backend :8000; prod same-origin
- RealtimePriceTable: richer columns, disabled/error empty states
- `POST /api/sport-edges/{match_id}/detect` on-demand edge compute (write key)
- EdgeDetailPanel: "重新计算 Edge" button
- Club form fallback from `kernel_match_results` when international CSV misses

### Recommendations / settlements UX + local verify
- Shared `FeatureDisabledBanner` for Phase7 503 states
- OpenDecisionsList: act/watch filters, links to match, empty-path guidance
- SettlementHistoryTable: manual match_id reprocess; row actions
- Match detail: inline recommendation panel + process settlement
- `scripts/verify_local_stack.py` smoke-checks health + key Phase endpoints

### Event decision diagnosis UI (P2-FE10)
- `DecisionReport` types include quality overlays + final_displayed_direction
- DecisionCard expand: calibration status, decision/market/source quality downgrades
- verify script also probes `/api/events/decisions/open` and `/api/events/calibration`

### Calibration copy + quality alert deep links (P2-FE11)
- History AccuracySummary: labels event-layer (EIP) vs Kernel / settlements
- Learning tabs: Kernel scope banner + link to /history
- Anomalies API attaches sample `event_ids` + `href`; AnomalyBanner links to events
- Quality report errors: event_id → /events/{id}

### History segment compare (P2-FE12)
- `GET /api/events/predictions/calibration/buckets` exposes edge×confidence diagnostics
- History page: SegmentComparePanel (领域 / act 类目 / Edge 桶 / 置信度桶 + 交叉表)
- Chart of skill by category remains; table enables sort and side-by-side comparison

### Hot news ticker from real movers (P3-FE13)
- AppNav ticker fetches `/events/movers` and shows live titles + delta (links to event)
- Falls back to placeholder copy when empty/unavailable; label 异动快讯 vs 示例新闻

### Event vs Sport Edge IA (P2-FE4)
- `DomainScopeBanner` cross-links `/edges` ↔ `/sports/edges` with scope copy
- Nav labels: 事件 Edge / 体育 Edge; page titles clarify EIP vs Kernel

### Apply weight diff + learning weights tab (P2-FE5)
- `OptimizedParamsStore.apply` returns `previous_applied` + `weight_diff`
- OptimizationDashboard: last-apply before/after table + per-row weight preview
- Learning: tab「已应用权重」lists status=applied factor/elo weights

### World Cup vs Kernel track (P2-FE6)
- `SportTrackBanner` on `/sports` and `/sports/world-cup` (API path + cross-links)
- Nav: 世界杯 → `/sports/world-cup`; Kernel list active state excludes named hubs

### Trades edge aligned with EIP raw_edge (P2-SB6)
- Simulated trade rows expose `raw_edge` + `directional_edge` (YES→raw, NO→−raw)
- `trade_stats` adds directional mean + documented `edge_definition` (legacy `|raw|` key kept)
- `/trades` UI: definition banner, raw/方向 columns, links to event vs sport edges

### Futures multi-leg coverage (P2-SB5)
- Expanded Kalshi series prefixes (NFL / EPL / NCAAB / conf); longest-prefix match
- `multi_leg_integrity` (legs, Σp, dupes) on fetch + store APIs
- `GET /futures/meta/series` + `/meta/coverage`; FuturesDashboard coverage panel

### A11y + shared form/table styles (P3-FE7)
- `lib/ui-classes` (`inputCls` / `selectCls`) shared by analyze + manual resolve + optimization
- `ScrollableTable` for mobile horizontal scroll; factor/edge/futures/optimization tables
- Sport filter `aria-pressed` / group label; form controls `htmlFor` + `aria-label`

### Backtest results visualization (P3-FE8)
- `ParameterOptimizer.optimize_sync` returns accuracy/Brier/MAE/sample/train-test + weights
- `BacktestResultsPanel`: bar chart (recharts) + metrics table from task.result or candidates
- OptimizationDashboard shows last-run results and candidate comparison

### Operator credentials hardening (P2-FE9 partial)
- `lib/operator-credentials`: sessionStorage-only key/id, mask, clear, change event
- OperatorKeyControl: clear button, session-only copy, masked tooltip
- api/sports client use shared auth header builder; Runbook documents browser key model + BFF residual

### Odds quality weight dampening (P1-E4) + competition profiles (P1-E3 partial)
- `kernel/engines/odds_quality.py`: multiplier from freshness, overround, `custom.liquidity_factor`
- Applied in `EloOddsEngine` and `FootballMultiFactorEngine` (odds weight only; redistributes via fusion)
- Multi-factor competition profiles for epl / laliga / ucl / wc (registry override still wins)
- `kernel/market_liquidity.py`: injects `liquidity_factor` from sport-market links/snapshots into FeatureSet.custom via MultiFeatureBuilder + football adapter
- NBA/MLB/NHL FeatureBuilders + adapters also inject (same helper; MultiFeatureBuilder remains the common path)

### Situational engine (P1-E8)
- `situational_adjust.py`: soft renormalized lifts for knockout / must-win / group pressure (capped)
- `SituationalEngine`: wraps EloOdds (or other base); explainable `situational` contribution
- Flag `SITUATIONAL_ENGINE_ENABLED` (default off); registered before ensemble
- `group_context_bridge`: WorldCupAdapter + football `_shared` inject must_win/pressure into custom

### Confidence + B2B + odds dispersion (P1-X1 / B2 / H2 / O2)
- `confidence.compute_confidence`: strength + completeness + agreement + market damp
- Wired into EloOdds, MultiFactor, Basketball, Baseball, Hockey engines
- NBA/NHL `b2b_*` + rest-factor penalties
- `odds_dispersion_from_books` + TraditionalOddsStore inject via MultiFeatureBuilder
- `GET /predictions/engines/meta` + frontend `useEnginesMeta`
- Multi-factor profiles: serie_a / bundesliga / ligue_1
- MLB park factor (soft) in BaseballEngine + coarse park map in MLB adapter
- Edge `review_priority` (low/normal/high/critical) + API field
- Match detail: engine ZH labels + situational notes banner
- BasketballEngine: net_rating factor (ORtg/DRtg) + restored b2b rest penalty
- Edge UI: review_priority column + detail badges

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
