# Sports Prediction OS — Phase 4: NBA Integration Design

> **Status:** Reviewed
> **Date:** 2026-07-14
> **Author:** Design phase
> **Depends on:** Phase 1 (Kernel extraction), Phase 2/2b (Football leagues), Phase 3 (Learning loop)
> **Spec location:** `docs/superpowers/specs/2026-07-14-sports-prediction-os-phase4-design.md`

---

## 1. Goal

Integrate NBA basketball as the first non-football sport into the Sports Prediction OS. This validates the Kernel's multi-sport architecture by adding a sport with a different outcome space (binary win/loss vs. three-way home/draw/away), different scoring model (high-score totals vs. low-score Poisson), and no external Elo source (self-computed Elo from historical games).

**Scale target:** Add ~1,230 NBA regular-season games/year (82 games × 30 teams / 2), bringing total annual coverage from ~1,900 to ~3,100 matches.

---

## 2. Architecture Overview

Phase 4 creates a parallel `backend/app/sports/basketball/` module alongside the existing `football/` module. A new `MultiFeatureBuilder` mirrors the existing `MultiAdapter` pattern for sport-aware FeatureBuilder dispatch. The PredictionKernel is unchanged — it only depends on Protocol interfaces.

### 2.1 Data Flow

```
nba-{game_id} → MultiAdapter → NBAAdapter → raw dict
                                              ↓
               MultiFeatureBuilder → BasketballFeatureBuilder → FeatureSet
                                                                      ↓
               EngineRegistry → BasketballEngine → PredictionResult
                                                      ↓
               LearningService ← record_prediction / process_outcome
```

### 2.2 New Components

| Component | Location | Protocol |
|---|---|---|
| NBAAdapter | `app/sports/basketball/nba_adapter.py` | DataAdapter |
| BasketballFeatureBuilder | `app/sports/basketball/feature_builder.py` | FeatureBuilder |
| BasketballEngine | `app/sports/basketball/engines/basketball_engine.py` | PredictionEngine |
| EloCalculator | `app/sports/basketball/elo_calculator.py` | Stateless functions |
| MultiFeatureBuilder | `app/kernel/multi_feature_builder.py` | FeatureBuilder |

### 2.3 Modified Components

| Component | Location | Change |
|---|---|---|
| LearningService | `app/kernel/learning_service.py` | Dynamic outcome keys + dynamic factor iteration |
| FactorRegistry | `app/kernel/factor_registry.py` | Add `ensure_competition_factors()` method for NBA factors |
| Config | `app/core/config.py` | +6 config fields |
| API routes | `app/api/routes/predictions.py` | Register NBA components in `_get_kernel()` |

### 2.4 Unchanged Components

- **PredictionKernel** — zero modification (depends on Protocol interfaces only)
- **Domain model** — zero modification (`FeatureSet.custom` dict handles basketball-specific features)
- **FootballFeatureBuilder** — zero modification
- **EloOddsEngine** — zero modification
- **Football adapters** — zero modification
- **Frontend pages** — zero modification

---

## 3. NBA Adapter

### 3.1 Data Source

**balldontlie.io** REST API — chosen for architectural consistency with Football-Data.org (REST API, API key auth, JSON responses).

- Free tier: 5 req/min, provides Teams / Players / Games endpoints
- API key via `BALLDONTLIE_API_KEY` environment variable (empty = disabled)
- Base URL: `https://api.balldontlie.io/v1`
- Auth header: `Authorization: {BALLDONTLIE_API_KEY}`

### 3.2 Endpoint Mapping

| Adapter method | balldontlie endpoint | Local DB | Notes |
|---|---|---|---|
| `sync_schedule()` | `GET /games?seasons={Y}&per_page=100` (paginated) | Writes `kernel_match_fixtures` | Incremental — only fetches missing games |
| `get_match_identity()` | — | Reads `kernel_match_fixtures` | No API call |
| `fetch_all_data()` | — | Reads `kernel_match_fixtures` + `kernel_elo_ratings` | No API call — all data from local DB |
| `fetch_outcome()` | — | Reads `kernel_match_results` or `kernel_match_fixtures` (status=finished) | No API call if local data exists |
| `fetch_schedule()` | — | Reads `kernel_match_fixtures` | Filtered by ScheduleFilter |

### 3.3 Match ID Format

- Prefix: `nba-`
- Full ID: `nba-{balldontlie_game_id}` (e.g., `nba-0022100001`)
- The balldontlie `game_id` is an integer; prefix ensures no collision with football prefixes

### 3.4 Raw Dict Structure

`fetch_all_data()` returns:

```python
{
    "team": {
        "elo_home": 1650.0,
        "elo_away": 1520.0,
        "form_home": 0.7,       # last-10 win rate
        "form_away": 0.4,
    },
    "general": {
        "rest_days_home": 2,
        "rest_days_away": 1,
        "days_since_last_match": 2,
    },
    "market": {},               # empty — free tier has no odds
    "player": {},               # empty — free tier has no injuries
    "environment": {
        "venue": "TD Garden",
        "is_home_advantage": True,
    },
    "custom": {
        "pace_home": 99.5,      # possessions per 48 min
        "pace_away": 97.2,
        "ortg_home": 112.3,     # offensive rating (points per 100 possessions)
        "ortg_away": 108.1,
        "drtg_home": 105.0,     # defensive rating
        "drtg_away": 110.5,
        "tpct_home": 0.365,     # 3-point percentage
        "tpct_away": 0.342,
    },
}
```

### 3.5 Rate Limiting

- Request interval ≥ 12 seconds (ensures ≤ 5 req/min)
- `sync_schedule()` uses pagination with `per_page=100` and respects rate limit between pages
- `fetch_all_data()` makes no API calls — all data read from local DB tables

### 3.6 API Key Disabled Behavior

When `BALLDONTLIE_API_KEY` is empty:
- `sync_schedule()` returns 0 (no games synced)
- `fetch_all_data()` returns raw dict with `team.elo_home = None` (Elo from DB if previously computed)
- `get_match_identity()` still works (reads from local DB)
- `fetch_outcome()` still works (reads from local DB)
- No exceptions raised — graceful degradation

### 3.7 Stage Mapping

NBA stages mapped from balldontlie `season` and `postseason` fields:

| balldontlie field | stage value |
|---|---|
| Regular season game | `"regular_season"` |
| Playoff game | `"playoff"` |

The `postseason` boolean in balldontlie API determines this. Playoff games use higher K-factor in Elo calculation (see Section 4).

---

## 4. Elo Calculator

### 4.1 Rationale

balldontlie.io does not provide Elo ratings. Unlike football (which has ClubElo.com and national team Elo services), NBA Elo must be self-computed from historical game results.

### 4.2 Algorithm

Standard Elo with basketball-specific parameters:

**Expected score:**
```
E_home = 1 / (1 + 10^((elo_away - elo_home - HFA) / 400))
```

**Parameters:**

| Parameter | Value | Rationale |
|---|---|---|
| HFA (Home Field Advantage) | 100 | Basketball has larger home advantage than football (~55-58% home win rate) |
| K-factor (regular season) | 20 | Standard NBA Elo K-factor |
| K-factor (playoff) | 30 | Playoff games carry more weight |
| Season regression | 0.75 | `new_elo = 0.75 × prev_elo + 0.25 × 1500` at season start |
| Initial Elo | 1500 | All teams start at league average |

**Update after each game:**
```
elo_home_new = elo_home + K × (S_home - E_home)
elo_away_new = elo_away + K × (S_away - E_away)
```
where `S_home = 1` if home wins, `0` otherwise. `S_away = 1 - S_home`.

### 4.3 Seed Data

- Fetch 3 most recent completed seasons from balldontlie.io (e.g., 2022-23, 2023-24, 2024-25)
- Iterate chronologically, updating Elo after each game
- Apply season regression at the start of each new season
- Final Elo ratings are the current team ratings

### 4.4 Storage

New table `kernel_elo_ratings`:

| Column | Type | Notes |
|---|---|---|
| `team_name` | String, PK | Team name (e.g., "Boston Celtics") |
| `sport` | String | `"basketball"` |
| `competition` | String | `"nba"` |
| `elo_rating` | Float | Current Elo rating |
| `source` | String | `"self_computed"` |
| `updated_at` | DateTime | Last update timestamp |

This table follows the `kernel_` prefix convention. It is a general-purpose Elo table that can be reused for future self-computed Elo in other sports.

### 4.5 Incremental Update

After initial seeding, `sync_schedule()` triggers incremental Elo updates:
1. Query `kernel_match_results` for NBA matches with `finished_at` after the last Elo update
2. Apply Elo update for each new completed game
3. Persist updated ratings to `kernel_elo_ratings`

### 4.6 Stateless Functions

`elo_calculator.py` contains stateless functions (matching the `_shared.py` pattern in football):

```python
def compute_expected_score(elo_home: float, elo_away: float, hfa: int = 100) -> float: ...
def update_elo(elo: float, expected: float, actual: float, k: int = 20) -> float: ...
def apply_season_regression(elo: float, mean: float = 1500.0, carry: float = 0.75) -> float: ...
def seed_elo_from_games(games: list[dict], hfa: int = 100, k_regular: int = 20, k_playoff: int = 30) -> dict[str, float]: ...
```

---

## 5. BasketballFeatureBuilder

### 5.1 Mapping

Implements FeatureBuilder Protocol. Mirrors FootballFeatureBuilder structure.

| FeatureSet layer | Source | Basketball fields |
|---|---|---|
| `general` | `raw["general"]` | `rest_days_home`, `rest_days_away`, `days_since_last_match` |
| `team` | `raw["team"]` | `elo_rating_home`, `elo_rating_away`, `form_home`, `form_away`, `h2h_home_win_rate` |
| `team` | Hardcoded | `h2h_draw_rate = None` (basketball has no draws) |
| `team` | N/A | `market_value_home = None`, `market_value_away = None` |
| `market` | N/A | All `None` (free tier has no odds) |
| `player` | N/A | All `None` (free tier has no injuries) |
| `environment` | `raw["environment"]` | `venue`, `is_home_advantage` |
| `environment` | N/A | `weather_temp_c = None`, `weather_condition = None` |
| `custom` | `raw["custom"]` | `pace_home`, `pace_away`, `ortg_home`, `ortg_away`, `drtg_home`, `drtg_away`, `tpct_home`, `tpct_away` |

### 5.2 Data Quality

- `"real"` — Elo exists (`team.elo_home is not None`)
- `"partial"` — Elo missing

Unlike FootballFeatureBuilder, **odds absence does NOT downgrade quality** — basketball free tier has no odds by design, and the BasketballEngine does not use odds.

### 5.3 Feature Version

`feature_version = "nba-1.0"` (distinct from football's `"1.0"`)

### 5.4 Sport Identity

```python
_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
```

---

## 6. MultiFeatureBuilder

### 6.1 Pattern

Mirrors MultiAdapter's prefix-dispatch pattern. Implements FeatureBuilder Protocol transparently.

```python
class MultiFeatureBuilder:
    def __init__(self, builders: dict[str, FeatureBuilder]) -> None:
        self._builders = builders
        self._default = next(iter(builders.values()))

    def _select(self, match_id: str) -> FeatureBuilder:
        for prefix, builder in self._builders.items():
            if match_id.startswith(prefix):
                return builder
        return self._default

    def sport(self) -> SportIdentity:
        return self._default.sport()

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        return self._select(match.match_id).build(match, raw)
```

### 6.2 Registration

In `_get_kernel()`:

```python
fb = FootballFeatureBuilder()
if config.settings.PHASE4_NBA_ENABLED:
    builders = {
        "wc-": fb, "ucl-": fb, "epl-": fb,
        "laliga-": fb, "bundesliga-": fb, "seriea-": fb, "ligue1-": fb,
        "nba-": BasketballFeatureBuilder(),
    }
    feature_builder = MultiFeatureBuilder(builders)
else:
    feature_builder = fb
```

All football prefixes share the same `FootballFeatureBuilder()` instance to avoid redundant instantiation.

---

## 7. BasketballEngine

### 7.1 Model

Bradley-Terry binary outcome model (no draws). Four independent factors each compute `P(home_win)`, then weighted-average fusion.

### 7.2 Factors

| Factor | ID | Calculation | Default Weight |
|---|---|---|---|
| Elo | `elo` | `1 / (1 + 10^((elo_away - elo_home - HFA) / 400))`, HFA from config | 0.45 |
| Home Court | `home_court` | `0.58` (NBA historical home win rate, constant) | 0.15 |
| Rest | `rest` | `0.5 + clamp(rest_home - rest_away, -3, 3) × 0.03` | 0.15 |
| Form | `form` | `0.5 + clamp(form_home - form_away, -0.3, 0.3) × 0.5` | 0.25 |

Weights sum to 1.0. Weights are read from `FactorRegistry.get_weight(factor_id, "nba")` at prediction time, falling back to defaults if FactorRegistry is None.

### 7.3 Factor Availability

Each factor reports availability:
- `elo` — available when `features.team.elo_rating_home is not None`
- `home_court` — always available (constant)
- `rest` — available when `features.general.rest_days_home is not None`
- `form` — available when `features.team.form_home is not None`

When a factor is unavailable, its weight is redistributed proportionally to available factors (same pattern as EloOddsEngine's `_fuse_elo_and_odds` fallback).

### 7.4 Fusion

```python
available_factors = [(f, p, w) for f, p, w in factors if available[f]]
total_w = sum(w for _, _, w in available_factors)
p_home = sum(p * (w / total_w) for _, p, w in available_factors)
p_away = 1.0 - p_home
outcome_probabilities = {"home_win": round(p_home, 4), "away_win": round(p_away, 4)}
```

### 7.5 Score Conversion

Basketball-specific (replaces football's Poisson model):

```python
league_avg_total = config.settings.NBA_LEAGUE_AVG_TOTAL  # 220.0
# 100 Elo difference ≈ 3 point margin (empirical NBA calibration)
margin = (elo_home - elo_away + HFA) * 0.03
home_score = league_avg_total / 2 + margin / 2
away_score = league_avg_total / 2 - margin / 2
predicted_scores = {"home": round(home_score, 1), "away": round(away_score, 1)}
```

### 7.6 Confidence

```python
confidence = round(min(max(p_home, p_away) * 0.95, 0.95), 4)
```

Identical formula to EloOddsEngine for consistency.

### 7.7 ContributionItem

Each factor produces one ContributionItem with `predicted_outcome` set:
- `"home_win"` if the factor's `P(home_win) >= 0.5`
- `"away_win"` otherwise

This is consumed by `LearningService.update_weights()` for EWMA per-factor accuracy tracking.

### 7.8 Engine Registration

```python
class BasketballEngine:
    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "basketball"

    def supported_sports(self) -> list[str]:
        return ["basketball"]
```

Registered in `EngineRegistry` alongside `EloOddsEngine`. `EngineRegistry.select("auto", "nba")` will dynamically pick the engine with the highest accuracy (from Phase 3 learning loop) once sufficient samples exist.

---

## 8. Learning Service Generalization

### 8.1 Problem

Two hardcoded assumptions in `learning_service.py` prevent basketball support:

1. `compute_error()` — Brier score iterates over hardcoded `["home_win", "draw", "away_win"]`
2. `update_weights()` — iterates over hardcoded `elo` and `odds` factor names

### 8.2 Fix: Dynamic Outcome Keys in `compute_error()`

**Before:**
```python
brier = sum(
    (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
    for k in ["home_win", "draw", "away_win"]
)
```

**After:**
```python
outcome_keys = list(probs.keys())
brier = sum(
    (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
    for k in outcome_keys
)
```

**Backward compatibility:** Football predictions have keys `["home_win", "draw", "away_win"]` — dynamic iteration produces identical results.

### 8.3 Fix: Dynamic Factor Iteration in `update_weights()`

**Before:** Hardcoded `elo` and `odds` counters.

**After:** Dynamic collection of all factors from explanation:

```python
factor_stats: dict[str, dict[str, int]] = {}  # {factor_id: {correct, total}}
for pred, outcome in results:
    actual = outcome.outcome
    for item in pred.explanation or []:
        if not isinstance(item, dict):
            continue
        factor = item.get("factor")
        predicted = item.get("predicted_outcome")
        if not factor or not predicted:
            continue
        if factor not in factor_stats:
            factor_stats[factor] = {"correct": 0, "total": 0}
        factor_stats[factor]["total"] += 1
        if predicted == actual:
            factor_stats[factor]["correct"] += 1

# Skip if any factor has 0 samples
if any(s["total"] == 0 for s in factor_stats.values()):
    return

# Compute accuracy per factor, normalize to target weights
accuracies = {f: s["correct"] / s["total"] for f, s in factor_stats.items()}
total_acc = sum(accuracies.values())
if total_acc == 0:
    return
target_weights = {f: acc / total_acc for f, acc in accuracies.items()}

# EWMA update for each factor
alpha = config.settings.EWMA_ALPHA
for factor_id, target_w in target_weights.items():
    old_w = self._factor_registry.get_weight(factor_id, competition)
    new_w = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING,
               alpha * target_w + (1 - alpha) * old_w))
    self._factor_registry.update_weight(factor_id, competition, new_w, source="ewma")

# Normalize last factor to ensure weights sum to 1.0
# (handles clamp rounding drift)
factors = list(target_weights.keys())
if len(factors) > 1:
    sum_w = sum(self._factor_registry.get_weight(f, competition) for f in factors[:-1])
    last_w = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING, 1.0 - sum_w))
    self._factor_registry.update_weight(factors[-1], competition, last_w, source="ewma")
```

**Backward compatibility:** Football explanations contain `elo` and `odds` factors — dynamic iteration produces identical results to the hardcoded version.

**Edge case:** Factors that never appear in any explanation (because they were unavailable in all games in the window) are excluded from `factor_stats` entirely. Their weights remain unchanged. The engine normalizes weights at prediction time (Section 7.4), so stored weights need not sum to 1.0.

### 8.4 Regression Safety

All 174 existing tests must pass with zero modifications. The dynamic iteration is a strict generalization:
- Football outcome keys are a subset of what `probs.keys()` returns
- Football factor names (`elo`, `odds`) are discovered dynamically instead of hardcoded

---

## 9. FactorRegistry Extension

### 9.1 NBA Default Factors

`_init_default_factors()` must seed NBA-specific factors when the DB is empty. However, `_init_default_factors()` currently only runs when the entire DB is empty. NBA factors need to be seeded per-competition.

**Approach:** Add a method `ensure_competition_factors(competition: str)` that seeds default factors for a competition if none exist:

```python
def ensure_competition_factors(self, competition: str) -> None:
    """Seed default factors for a competition if none exist."""
    existing = [f for (fid, comp) in self._factors if comp == competition]
    if existing:
        return  # Already seeded

    if competition == "nba":
        defaults = [
            ("elo", "elo_rating", 0.45),
            ("home_court", "home_advantage", 0.15),
            ("rest", "rest_days", 0.15),
            ("form", "recent_form", 0.25),
        ]
    else:
        return  # Unknown competition — no defaults

    now = datetime.now(timezone.utc)
    session = self._session_factory()
    try:
        for factor_id, category, weight in defaults:
            fc = FactorConfig(factor_id, category, "1.0", weight, competition, True, "default", now)
            self._factors[(factor_id, competition)] = fc
            row = KernelFactor(
                factor_id=fc.factor_id, category=fc.category,
                version=fc.version, weight=fc.weight,
                competition=fc.competition, enabled=1,
                source=fc.source, updated_at=now,
            )
            session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

Called in `_get_kernel()` after FactorRegistry creation when `PHASE4_NBA_ENABLED` is true.

### 9.2 Football Defaults Unchanged

The global defaults (`elo=0.30`, `odds=0.70`) remain unchanged. NBA factors are competition-specific (`competition="nba"`), so `get_weight("elo", "epl")` still returns the football weight, not the NBA weight.

---

## 10. Config

### 10.1 New Settings

```python
PHASE4_NBA_ENABLED: bool = _env_bool("PHASE4_NBA_ENABLED", "false")
BALLDONTLIE_API_KEY: str = os.getenv("BALLDONTLIE_API_KEY", "")
NBA_ELO_HFA: int = int(os.getenv("NBA_ELO_HFA", "100"))
NBA_ELO_K_REGULAR: int = int(os.getenv("NBA_ELO_K_REGULAR", "20"))
NBA_ELO_K_PLAYOFF: int = int(os.getenv("NBA_ELO_K_PLAYOFF", "30"))
NBA_LEAGUE_AVG_TOTAL: float = float(os.getenv("NBA_LEAGUE_AVG_TOTAL", "220.0"))
```

### 10.2 Feature Flag Behavior

`PHASE4_NBA_ENABLED = false` (default):
- `nba-` prefix match_ids return 404 (same pattern as `PHASE2_LEAGUES_ENABLED`)
- NBAAdapter and BasketballEngine are not instantiated
- FootballFeatureBuilder is used directly (no MultiFeatureBuilder wrapper)

`PHASE4_NBA_ENABLED = true`:
- NBAAdapter registered in MultiAdapter with `nba-` prefix
- BasketballEngine registered in EngineRegistry
- MultiFeatureBuilder wraps Football + Basketball builders
- FactorRegistry seeds NBA default factors

### 10.3 .env.example

```env
# Phase 4: NBA Integration
PHASE4_NBA_ENABLED=false
BALLDONTLIE_API_KEY=
NBA_ELO_HFA=100
NBA_ELO_K_REGULAR=20
NBA_ELO_K_PLAYOFF=30
NBA_LEAGUE_AVG_TOTAL=220.0
```

---

## 11. API Integration

### 11.1 `_get_kernel()` Changes

Modified section in `predictions.py`:

```python
# Phase 2: register UCL and EPL adapters when enabled
if config.settings.PHASE2_LEAGUES_ENABLED:
    ...

# Phase 4: register NBA adapter + engine when enabled
fb = FootballFeatureBuilder()

if config.settings.PHASE4_NBA_ENABLED:
    from app.sports.basketball.nba_adapter import NBAAdapter
    from app.sports.basketball.feature_builder import BasketballFeatureBuilder
    from app.sports.basketball.engines.basketball_engine import BasketballEngine
    from app.kernel.multi_feature_builder import MultiFeatureBuilder

    adapters["nba-"] = NBAAdapter()
    nba_engine = BasketballEngine(factor_registry=factor_registry)
    reg.register(nba_engine)

    factor_registry.ensure_competition_factors("nba")

    builders = {
        "wc-": fb, "ucl-": fb, "epl-": fb,
        "laliga-": fb, "bundesliga-": fb, "seriea-": fb, "ligue1-": fb,
        "nba-": BasketballFeatureBuilder(),
    }
    feature_builder = MultiFeatureBuilder(builders)
else:
    feature_builder = fb

from app.sports.football.adapters.multi_adapter import MultiAdapter
multi = MultiAdapter(adapters)

_get_kernel._instance = PredictionKernel(
    adapter=multi,
    feature_builder=feature_builder,  # was: FootballFeatureBuilder()
    engine_registry=reg,
    factor_registry=factor_registry,
    feature_registry=FeatureRegistry(),
    learning=learning,
)
```

### 11.2 API Routes

No new API routes. Existing `/api/predictions/{match_id}` and `/api/predictions/{match_id}/predict` endpoints work transparently with `nba-` prefixed match_ids when Phase 4 is enabled.

---

## 12. Database Schema

### 12.1 New Table: `kernel_elo_ratings`

```python
class KernelEloRating(KernelBase):
    __tablename__ = "kernel_elo_ratings"

    team_name = Column(String, primary_key=True)
    sport = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    elo_rating = Column(Float, nullable=False)
    source = Column(String, default="self_computed")
    updated_at = Column(DateTime, nullable=False)
```

### 12.2 Reused Tables

| Table | Usage | competition column value |
|---|---|---|
| `kernel_match_fixtures` | NBA schedule | `"nba"` |
| `kernel_match_results` | NBA game outcomes | (no competition column — match_id prefix identifies sport) |
| `kernel_predictions` | NBA predictions | `"nba"` |
| `kernel_match_outcomes` | NBA outcomes + error metrics | (no competition column) |
| `kernel_factors` | NBA factor weights | `"nba"` |
| `kernel_engine_scores` | BasketballEngine scores | `"nba"` |
| `kernel_calibration` | BasketballEngine calibration | `"nba"` |

### 12.3 No Schema Modifications

Existing tables are not modified. `kernel_elo_ratings` is new and follows the `kernel_` prefix convention. `create_all` in `init_kernel_db()` will create it automatically.

---

## 13. Test Strategy

### 13.1 New Test Files (28 tests, 6 files)

| File | Tests | Coverage |
|---|---|---|
| `test_nba_adapter.py` | 5 | API response parsing, match_id construction, sync_schedule pagination, disabled behavior (no API key), request interval enforcement |
| `test_nba_elo_calculator.py` | 4 | Elo iteration, HFA application, season regression, K-factor difference (regular vs playoff) |
| `test_basketball_feature_builder.py` | 4 | Full feature mapping, custom dict contents, data quality (real/partial), market all-None |
| `test_multi_feature_builder.py` | 4 | Prefix dispatch, default fallback, football path unchanged, basketball path correct |
| `test_basketball_engine.py` | 6 | 4 factor probability calculations, weighted fusion, score conversion, ContributionItem predicted_outcome, FactorRegistry weight reading, no-Elo fallback |
| `test_learning_dynamic_outcomes.py` | 5 | Binary Brier score, 4-factor EWMA update, football 3-way regression unchanged, mixed competition isolation, empty explanation safe handling |

### 13.2 Regression Tests

All 174 existing tests must pass with zero modifications. Verified by running the full test suite before and after Phase 4 implementation.

### 13.3 Test Data

- NBA test data uses mock balldontlie.io API responses (JSON fixtures)
- Elo calculator tests use synthetic game sequences with known expected Elo outcomes
- Learning service tests use both football (3-way) and basketball (binary) prediction/outcome pairs

---

## 14. Constraints

1. `PHASE4_NBA_ENABLED` defaults to OFF — when false, `nba-` prefix match_ids return 404
2. `BALLDONTLIE_API_KEY` empty → NBAAdapter gracefully disables (no exceptions)
3. NBAAdapter request interval ≥ 12 seconds (5 req/min free tier limit)
4. Reuse `kernel_match_fixtures` and `kernel_match_results` tables with `competition = "nba"`
5. New `kernel_elo_ratings` table for self-computed Elo; no modifications to existing `kernel_club_elo_cache`
6. `learning_service.py` changes must keep all 174 existing tests passing with zero modifications
7. `FactorRegistry._init_default_factors()` football defaults unchanged; NBA factors seeded via `ensure_competition_factors("nba")`
8. PredictionKernel zero modification
9. Frontend pages zero modification
10. Domain model (`domain.py`) zero modification — `FeatureSet.custom` dict handles basketball features
11. BasketballEngine reads HFA, K-factors, and league avg from `config.settings` at call time (not module load)
12. `ContributionItem.predicted_outcome` values are `"home_win"` / `"away_win"` for basketball (binary, no draw)
13. NBA Elo uses standard Elo formula with HFA=100, K_regular=20, K_playoff=30, season regression 0.75
14. Subagent-driven task execution with independent sub-agents per task and inter-task reviews

---

## 15. File Structure

```
backend/app/sports/basketball/               # NEW module
├── __init__.py                               # NEW
├── nba_adapter.py                            # NEW: NBAAdapter (DataAdapter Protocol)
├── feature_builder.py                        # NEW: BasketballFeatureBuilder (FeatureBuilder Protocol)
├── elo_calculator.py                         # NEW: Stateless Elo computation functions
└── engines/
    ├── __init__.py                           # NEW
    └── basketball_engine.py                  # NEW: BasketballEngine (PredictionEngine Protocol)

backend/app/kernel/
└── multi_feature_builder.py                  # NEW: MultiFeatureBuilder (FeatureBuilder Protocol)

backend/app/                                   # MODIFIED
├── core/config.py                             # +6 config fields
├── kernel/kernel_db.py                        # +KernelEloRating table model
├── kernel/learning_service.py                 # Dynamic outcome keys + dynamic factor iteration
├── kernel/factor_registry.py                  # +ensure_competition_factors() method
└── api/routes/predictions.py                  # _get_kernel() NBA registration

backend/tests/                                 # NEW: 6 test files, 28 tests
├── test_nba_adapter.py                        # 5 tests
├── test_nba_elo_calculator.py                 # 4 tests
├── test_basketball_feature_builder.py         # 4 tests
├── test_multi_feature_builder.py              # 4 tests
├── test_basketball_engine.py                  # 6 tests
└── test_learning_dynamic_outcomes.py          # 5 tests
```

---

## 16. Roadmap Context

- **Phase 1** (done): Kernel extraction + WorldCupAdapter
- **Phase 2/2b** (done): Football league expansion (UCL, EPL, La Liga, Bundesliga, Serie A, Ligue 1)
- **Phase 3** (done): Unified learning loop (EWMA weights, linear calibration, engine scores)
- **Phase 4** (this spec): NBA integration + BasketballEngine — validates multi-sport architecture
- **Phase 5** (future): MLB/NHL integration — MultiFeatureBuilder pattern makes this additive
- **Goal**: Scale from ~1,900 to 3,100+ matches/year, driving accuracy via cross-sport learning
