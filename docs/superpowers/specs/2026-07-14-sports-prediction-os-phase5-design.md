# Sports Prediction OS — Phase 5: MLB/NHL Integration Design Spec

> **Date:** 2026-07-14
> **Phase:** 5 (MLB + NHL Integration)
> **Goal:** Integrate MLB (baseball) and NHL (hockey) as the third and fourth sports, validating the Kernel's multi-sport architecture at scale. Adds ~3,742 matches/season (MLB 2,430 + NHL 1,312), bringing annual coverage from ~3,130 to ~6,872 matches — exceeding the Roadmap goal of 3,100+.
> **Design approach:** Parallel sports modules + shared Elo calculator. MLB implemented first (Task 1-7), NHL second (Task 8-12), mirroring the Phase 2/2b incremental pattern.

---

## 1. Architecture Overview

### 1.1 Design Principle

Phase 5 is **additive** — it validates that the multi-sport architecture established in Phase 4 (NBA) scales without modification to the Kernel core. The `MultiFeatureBuilder` prefix-dispatch pattern and `MultiAdapter` routing automatically handle new sport prefixes. New sports are added as parallel `sports/<sport>/` modules with zero changes to `PredictionKernel`, `domain.py`, `protocols.py`, `learning_service.py`, `multi_feature_builder.py`, `kernel_db.py`, or the frontend.

### 1.2 New Components (14 new files: 9 source + 5 `__init__.py`)

```
backend/app/sports/
├── _shared/                          # NEW: cross-sport stateless utilities
│   ├── __init__.py
│   └── elo_calculator.py             # Stateless Elo functions (copy of NBA's)
├── baseball/                         # NEW: MLB baseball module
│   ├── __init__.py
│   ├── mlb_stats_client.py           # statsapi.mlb.com HTTP client (no key, 1 req/s)
│   ├── mlb_adapter.py                # MLBAdapter (DataAdapter Protocol, mlb- prefix)
│   ├── feature_builder.py            # BaseballFeatureBuilder (FeatureBuilder Protocol)
│   └── engines/
│       ├── __init__.py
│       └── baseball_engine.py        # BaseballEngine (5-factor Bradley-Terry)
└── hockey/                           # NEW: NHL hockey module
    ├── __init__.py
    ├── nhl_stats_client.py           # api-web.nhle.com HTTP client (no key, 1 req/s)
    ├── nhl_adapter.py                # NHLAdapter (DataAdapter Protocol, nhl- prefix)
    ├── feature_builder.py            # HockeyFeatureBuilder (FeatureBuilder Protocol)
    └── engines/
        ├── __init__.py
        └── hockey_engine.py          # HockeyEngine (5-factor Bradley-Terry)
```

### 1.3 Modified Components (4 files)

| File | Change |
|------|--------|
| `backend/app/core/config.py` | +12 config fields (2 flags + 10 Elo/league params) |
| `backend/.env.example` | +Phase 5 section |
| `backend/app/kernel/factor_registry.py` | `ensure_competition_factors` adds `"mlb"` / `"nhl"` branches |
| `backend/app/api/routes/predictions.py` | `_get_kernel()` adds MLB/NHL registration blocks |

### 1.4 Zero-Modification Components

- `PredictionKernel` — Protocol interfaces only
- `domain.py` — `FeatureSet.custom` dict handles sport-specific features
- `protocols.py` — Protocol definitions unchanged
- `learning_service.py` — Phase 3/4 already generalized for dynamic outcomes + factors
- `multi_feature_builder.py` — Prefix-dispatch auto-routes new prefixes
- `kernel_db.py` — Reuses existing `kernel_match_fixtures`, `kernel_match_results`, `kernel_elo_ratings` tables
- Frontend pages — zero modification

### 1.5 Task Dependency Graph

```
Task 1 (Config) ─────────────────────────────────┐
Task 2 (_shared/elo_calculator) ─────┐            │
                                     ▼            ▼
Task 3 (MLB Stats Client) ────────────────────────┤
                                     │            │
Task 4 (MLBAdapter) ◄────────────────┘            │
                                     │            │
Task 5 (BaseballFeatureBuilder) ◄─────────────────┤
                                     │            │
Task 6 (BaseballEngine, 5 factors) ◄── Task 2     │
                                     │            │
Task 7 (MLB API integration + FactorRegistry) ◄───┘
                                     │
─────── MLB complete, NHL begins ─── │
                                     │
Task 8  (NHL Stats Client) ──────────┤
Task 9  (NHLAdapter) ◄───────────────┤
Task 10 (HockeyFeatureBuilder) ──────┤
Task 11 (HockeyEngine, 5 factors) ◄──┤
Task 12 (NHL API integration + FactorRegistry) ┘
```

---

## 2. Global Constraints

1. `PHASE5_MLB_ENABLED` and `PHASE5_NHL_ENABLED` default to OFF (false) — when false, `mlb-`/`nhl-` prefix match_ids return 404
2. MLB/NHL Stats APIs require no API key — graceful degradation when API is unreachable (`sync_schedule` returns 0, no exceptions)
3. Rate limit 1 req/s for both MLB and NHL clients (polite usage, not API-enforced)
4. Reuse `kernel_match_fixtures` and `kernel_match_results` tables with `competition = "mlb"` / `"nhl"` — no new fixture/result tables
5. Reuse `kernel_elo_ratings` table with `sport = "baseball"` / `"hockey"` and `competition = "mlb"` / `"nhl"` — no new Elo tables
6. `learning_service.py` zero modification — Phase 3/4 already generalized for dynamic outcome keys and dynamic factor iteration
7. `FactorRegistry._init_default_factors()` football defaults unchanged; MLB/NHL factors seeded via `ensure_competition_factors("mlb")` / `("nhl")`
8. `PredictionKernel` zero modification
9. Frontend pages zero modification
10. `domain.py` zero modification — `FeatureSet.custom` dict handles sport-specific features
11. `BaseballEngine` and `HockeyEngine` read HFA, K-factors, season carry, and league avg from `config.settings` at call time (not module load)
12. `ContributionItem.predicted_outcome` values are `"home_win"` / `"away_win"` for both MLB and NHL (binary, no draw)
13. Elo parameters: MLB HFA=50, K_regular=20, K_playoff=30, season_carry=0.7; NHL HFA=55, K_regular=20, K_playoff=30, season_carry=0.75
14. Subagent-driven task execution with independent sub-agents per task and inter-task reviews
15. `BaseballFeatureBuilder` `feature_version = "mlb-1.0"`; `HockeyFeatureBuilder` `feature_version = "nhl-1.0"` (distinct from football's `"1.0"` and basketball's `"nba-1.0"`)
16. Data quality: `"real"` if Elo exists, `"partial"` if missing — odds absence does NOT downgrade quality (same as basketball)
17. All test files go in `backend/tests/` directory
18. All tests use in-memory or temp SQLite DB with per-test isolation (same pattern as existing `test_learning_weights.py`)
19. MLB match_id prefix is `mlb-` (e.g., `mlb-778812`); NHL match_id prefix is `nhl-` (e.g., `nhl-2023020001`)
20. MLB base URL: `https://statsapi.mlb.com/api/v1`; NHL base URL: `https://api-web.nhle.com`
21. `sports/_shared/elo_calculator.py` is a copy of NBA's `sports/basketball/elo_calculator.py` — the NBA original file is NOT modified or moved
22. NHL overtime/shootout information stored in `FeatureSet.custom` (`went_to_overtime`, `went_to_shootout`) — does NOT affect `MatchOutcome.outcome` which remains binary (`home_win`/`away_win`)

---

## 3. Shared Component Design

### 3.1 `sports/_shared/elo_calculator.py`

Stateless Elo computation functions, copied verbatim from `sports/basketball/elo_calculator.py`. The NBA original remains untouched (zero risk). MLB and NHL import from `_shared` and pass sport-specific parameters.

**Functions:**

```python
def compute_expected_score(elo_home: float, elo_away: float, hfa: int = 100) -> float:
    """E_home = 1 / (1 + 10^((elo_away - elo_home - hfa) / 400))"""

def update_elo(elo: float, expected: float, actual: float, k: int = 20) -> float:
    """new_elo = elo + k * (actual - expected)"""

def apply_season_regression(elo: float, mean: float = 1500.0, carry: float = 0.75) -> float:
    """new_elo = carry * elo + (1 - carry) * mean"""

def seed_elo_from_games(
    games: list[dict],
    hfa: int = 100,
    k_regular: int = 20,
    k_playoff: int = 30,
) -> dict[str, float]:
    """Process games chronologically, apply season regression at boundaries.
    Returns {team_name: final_elo}."""
```

**Input contract for `seed_elo_from_games`:** Each game dict must contain `home_team`, `away_team`, `home_score`, `away_score`, `is_playoff`, `season` (int). Games must be in chronological order — season boundaries trigger regression.

---

## 4. MLB Integration Design

### 4.1 MLB Stats Client (`sports/baseball/mlb_stats_client.py`)

| Dimension | Spec |
|-----------|------|
| Base URL | `https://statsapi.mlb.com/api/v1` |
| Authentication | None (official free API) |
| Rate limit | 1 req/s via module-level `_last_request_time` + `_enforce_rate_limit()` |
| Key endpoints | `schedule` (by date range), `game/{gamePk}/feed/live` (game details), `teams`, `people/{personId}` (player/pitcher) |
| Exception | `MLBStatsClientError` (network error / non-200 / timeout) |
| Pagination | `schedule` uses `startDate`/`endDate` params, no cursor needed |

**Exported functions:**
- `fetch_mlb_schedule(start_date: str, end_date: str) -> list[dict]`
- `fetch_mlb_game_feed(game_pk: int) -> dict`
- `fetch_mlb_pitcher(person_id: int) -> dict` — returns ERA, WHIP stats

### 4.2 MLBAdapter (`sports/baseball/mlb_adapter.py`)

| Dimension | Spec |
|-----------|------|
| match_id format | `mlb-{gamePk}` (e.g., `mlb-778812`) |
| DataAdapter Protocol | 8 methods (same structure as NBAAdapter) |
| DB tables | `kernel_match_fixtures` (`competition="mlb"`), `kernel_match_results` (`competition="mlb"`), `kernel_elo_ratings` (`sport="baseball"`, `competition="mlb"`) |
| Stage mapping | regular season → `"regular_season"`, postseason → `"playoff"` |
| Status mapping | `"Final"` → `"finished"`, else → `"scheduled"` |
| Starting pitcher | `fetch_all_data()` calls `fetch_mlb_pitcher()` for both teams' starters, writes ERA/WHIP to `raw["custom"]` |
| Graceful degradation | `PHASE5_MLB_ENABLED=false` → not instantiated; API unreachable → stub identity |
| Stateless utilities | `parse_mlb_game()`, `query_fixture()`, `query_result()`, `build_match_outcome()`, `save_fixture()` (mirror NBA pattern) |

**`fetch_all_data()` output structure:**
```python
{
    "general": {"rest_days_home": int, "rest_days_away": int, "days_since_last_match": int},
    "team": {"elo_home": float|None, "elo_away": float|None, "form_home": float, "form_away": float},
    "market": {},  # empty — no odds source
    "player": {"starting_pitcher_home": str|None, "starting_pitcher_away": str|None},
    "environment": {"venue": str, "is_home_advantage": True},
    "custom": {
        "pitcher_era_home": float, "pitcher_era_away": float,
        "pitcher_whip_home": float, "pitcher_whip_away": float,
        "team_batting_avg_home": float, "team_batting_avg_away": float,
        "team_era_home": float, "team_era_away": float,
        "pythagorean_win_pct_home": float, "pythagorean_win_pct_away": float,
    }
}
```

### 4.3 BaseballFeatureBuilder (`sports/baseball/feature_builder.py`)

| FeatureSet Layer | Filled | Source |
|------------------|--------|--------|
| `general` | `rest_days_home/away`, `days_since_last_match` | raw["general"] |
| `team` | `elo_rating_home/away`, `form_home/away` | raw["team"] |
| `market` | All None | No odds source |
| `player` | `key_players_available_home/away` (pitcher availability) | raw["player"] |
| `environment` | `venue`, `is_home_advantage` | raw["environment"] |
| `custom` | `pitcher_era_*`, `pitcher_whip_*`, `team_batting_avg_*`, `team_era_*`, `pythagorean_win_pct_*` | raw["custom"] |

| Dimension | Spec |
|-----------|------|
| `feature_version` | `"mlb-1.0"` |
| `data_quality` | `"real"` if `elo_home is not None`, else `"partial"` |
| `sport()` | `SportIdentity(code="baseball", name="Baseball")` |

### 4.4 BaseballEngine (`sports/baseball/engines/baseball_engine.py`)

**5-factor Bradley-Terry binary model** (home_win/away_win, no draw):

| Factor | Weight | Formula | Description |
|--------|--------|---------|-------------|
| `elo` | 0.30 | `compute_expected_score(elo_home, elo_away, hfa)` | Standard Elo, HFA=50 |
| `home_court` | 0.10 | `p = 0.54` (constant) | MLB historical home win rate (lower than NBA 0.58) |
| `rest` | 0.15 | `p = 0.5 + clamp(rest_diff, -3, 3) * 0.03` | Rest days differential |
| `form` | 0.20 | `p = 0.5 + clamp(form_diff, -0.3, 0.3) * 0.5` | Last-10 win rate differential |
| `starting_pitcher` | 0.25 | `p = 0.5 + clamp(era_diff, -2.0, 2.0) * 0.1` where `era_diff = era_away - era_home` | Starting pitcher ERA differential (home pitcher better → lower ERA → era_diff > 0 → p > 0.5) |

**Weight redistribution:** When a factor is unavailable, its weight is proportionally redistributed to available factors (same pattern as BasketballEngine).

**MLB Elo parameters** (read from `config.settings` at call time):
- `MLB_ELO_HFA = 50` (low home advantage in baseball)
- `MLB_ELO_K_REGULAR = 20`
- `MLB_ELO_K_PLAYOFF = 30`
- `MLB_ELO_SEASON_CARRY = 0.7` (long season, slightly more regression)
- `MLB_LEAGUE_AVG_TOTAL = 8.5` (league average total score for score conversion)

**Output structure:** Same as BasketballEngine — `outcome_probabilities`, `predicted_scores`, `confidence`, `explanation`. `ContributionItem.predicted_outcome` is `"home_win"` or `"away_win"`.

**Score conversion:** `margin = (elo_home - elo_away + hfa) * 0.03`, `home_score = league_avg/2 + margin/2`, `away_score = league_avg/2 - margin/2`.

**Confidence:** `min(max(p_home, p_away) * 0.95, 0.95)` (same formula as BasketballEngine).

---

## 5. NHL Integration Design

### 5.1 NHL Stats Client (`sports/hockey/nhl_stats_client.py`)

| Dimension | Spec |
|-----------|------|
| Base URL | `https://api-web.nhle.com` (main) + `https://api.nhle.com/stats/rest` (stats) |
| Authentication | None (official free API) |
| Rate limit | 1 req/s via module-level `_last_request_time` + `_enforce_rate_limit()` |
| Key endpoints | `v1/schedule` (by season), `v1/game/{id}/feed/live` (game details), `v1/standings`, `v1/roster/{teamId}/current` (roster/goalie) |
| Exception | `NHLStatsClientError` (network error / non-200 / timeout) |
| Pagination | `schedule` by season, no cursor needed |

**Exported functions:**
- `fetch_nhl_schedule(season: str) -> list[dict]`
- `fetch_nhl_game_feed(game_id: int) -> dict`
- `fetch_nhl_team_roster(team_id: int) -> dict` — returns goalie save% stats

### 5.2 NHLAdapter (`sports/hockey/nhl_adapter.py`)

| Dimension | Spec |
|-----------|------|
| match_id format | `nhl-{gameId}` (e.g., `nhl-2023020001`) |
| DataAdapter Protocol | 8 methods (same structure as MLBAdapter/NBAAdapter) |
| DB tables | `kernel_match_fixtures` (`competition="nhl"`), `kernel_match_results` (`competition="nhl"`), `kernel_elo_ratings` (`sport="hockey"`, `competition="nhl"`) |
| Stage mapping | regular season → `"regular_season"`, playoffs → `"playoff"` |
| Status mapping | `"OFF FINAL"` / `"FINAL"` → `"finished"`, else → `"scheduled"` |
| Goalie data | `fetch_all_data()` calls `fetch_nhl_team_roster()` for starting goalie save%, writes to `raw["custom"]` |
| Overtime/Shootout | `build_match_outcome()` always outputs binary (`home_win`/`away_win`); overtime/shootout flags written to `raw["custom"]["went_to_overtime"]` / `["went_to_shootout"]` |
| Graceful degradation | `PHASE5_NHL_ENABLED=false` → not instantiated; API unreachable → stub identity |
| Stateless utilities | `parse_nhl_game()`, `query_fixture()`, `query_result()`, `build_match_outcome()`, `save_fixture()` (mirror NBA pattern) |

**`fetch_all_data()` output structure:**
```python
{
    "general": {"rest_days_home": int, "rest_days_away": int, "days_since_last_match": int},
    "team": {"elo_home": float|None, "elo_away": float|None, "form_home": float, "form_away": float},
    "market": {},  # empty — no odds source
    "player": {"starting_goalie_home": str|None, "starting_goalie_away": str|None},
    "environment": {"venue": str, "is_home_advantage": True},
    "custom": {
        "goalie_save_pct_home": float, "goalie_save_pct_away": float,
        "team_gf_home": float, "team_gf_away": float,
        "team_ga_home": float, "team_ga_away": float,
        "corsi_pct_home": float|None, "corsi_pct_away": float|None,
        "pdo_home": float|None, "pdo_away": float|None,
        "went_to_overtime": bool, "went_to_shootout": bool,
    }
}
```

### 5.3 HockeyFeatureBuilder (`sports/hockey/feature_builder.py`)

| FeatureSet Layer | Filled | Source |
|------------------|--------|--------|
| `general` | `rest_days_home/away`, `days_since_last_match` | raw["general"] |
| `team` | `elo_rating_home/away`, `form_home/away` | raw["team"] |
| `market` | All None | No odds source |
| `player` | `key_players_available_home/away` (goalie availability) | raw["player"] |
| `environment` | `venue`, `is_home_advantage` | raw["environment"] |
| `custom` | `goalie_save_pct_*`, `team_gf_*`, `team_ga_*`, `corsi_pct_*`, `pdo_*`, `went_to_overtime`, `went_to_shootout` | raw["custom"] |

| Dimension | Spec |
|-----------|------|
| `feature_version` | `"nhl-1.0"` |
| `data_quality` | `"real"` if `elo_home is not None`, else `"partial"` |
| `sport()` | `SportIdentity(code="hockey", name="Hockey")` |

### 5.4 HockeyEngine (`sports/hockey/engines/hockey_engine.py`)

**5-factor Bradley-Terry binary model** (home_win/away_win):

| Factor | Weight | Formula | Description |
|--------|--------|---------|-------------|
| `elo` | 0.35 | `compute_expected_score(elo_home, elo_away, hfa)` | Standard Elo, HFA=55 |
| `home_court` | 0.15 | `p = 0.55` (constant) | NHL historical home win rate |
| `rest` | 0.15 | `p = 0.5 + clamp(rest_diff, -3, 3) * 0.03` | Rest days differential |
| `form` | 0.20 | `p = 0.5 + clamp(form_diff, -0.3, 0.3) * 0.5` | Last-10 win rate differential |
| `goalie` | 0.15 | `p = 0.5 + clamp(sv_pct_diff, -0.1, 0.1) * 2.0` where `sv_pct_diff = sv_pct_home - sv_pct_away` | Starting goalie save% differential (home goalie better → higher save% → sv_pct_diff > 0 → p > 0.5) |

**NHL Elo parameters** (read from `config.settings` at call time):
- `NHL_ELO_HFA = 55`
- `NHL_ELO_K_REGULAR = 20`
- `NHL_ELO_K_PLAYOFF = 30`
- `NHL_ELO_SEASON_CARRY = 0.75`
- `NHL_LEAGUE_AVG_TOTAL = 5.5` (low-scoring sport)

**Overtime/Shootout design decision:**
- `MatchOutcome.outcome` is always binary (`home_win`/`away_win`) — no new outcome values introduced
- Overtime/shootout info stored in `FeatureSet.custom` for future analysis, does not participate in prediction
- Does NOT modify `domain.py` (Constraint 10)

---

## 6. Config and API Integration

### 6.1 Config Fields (12 new)

```python
# Phase 5 — MLB/NHL Integration (default OFF)
PHASE5_MLB_ENABLED: bool = _env_bool("PHASE5_MLB_ENABLED", "false")
PHASE5_NHL_ENABLED: bool = _env_bool("PHASE5_NHL_ENABLED", "false")

# MLB Elo parameters
MLB_ELO_HFA: int = int(os.getenv("MLB_ELO_HFA", "50"))
MLB_ELO_K_REGULAR: int = int(os.getenv("MLB_ELO_K_REGULAR", "20"))
MLB_ELO_K_PLAYOFF: int = int(os.getenv("MLB_ELO_K_PLAYOFF", "30"))
MLB_ELO_SEASON_CARRY: float = float(os.getenv("MLB_ELO_SEASON_CARRY", "0.7"))
MLB_LEAGUE_AVG_TOTAL: float = float(os.getenv("MLB_LEAGUE_AVG_TOTAL", "8.5"))

# NHL Elo parameters
NHL_ELO_HFA: int = int(os.getenv("NHL_ELO_HFA", "55"))
NHL_ELO_K_REGULAR: int = int(os.getenv("NHL_ELO_K_REGULAR", "20"))
NHL_ELO_K_PLAYOFF: int = int(os.getenv("NHL_ELO_K_PLAYOFF", "30"))
NHL_ELO_SEASON_CARRY: float = float(os.getenv("NHL_ELO_SEASON_CARRY", "0.75"))
NHL_LEAGUE_AVG_TOTAL: float = float(os.getenv("NHL_LEAGUE_AVG_TOTAL", "5.5"))
```

### 6.2 .env.example Addition

```env
# === Phase 5: MLB/NHL Integration ===
PHASE5_MLB_ENABLED=false
PHASE5_NHL_ENABLED=false
# MLB Elo parameters (self-computed from historical games)
MLB_ELO_HFA=50
MLB_ELO_K_REGULAR=20
MLB_ELO_K_PLAYOFF=30
MLB_ELO_SEASON_CARRY=0.7
MLB_LEAGUE_AVG_TOTAL=8.5
# NHL Elo parameters
NHL_ELO_HFA=55
NHL_ELO_K_REGULAR=20
NHL_ELO_K_PLAYOFF=30
NHL_ELO_SEASON_CARRY=0.75
NHL_LEAGUE_AVG_TOTAL=5.5
```

### 6.3 FactorRegistry Extension

`ensure_competition_factors()` adds two new branches:

```python
if competition == "nba":
    defaults = [("elo", "elo_rating", 0.45), ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15), ("form", "recent_form", 0.25)]
elif competition == "mlb":
    defaults = [("elo", "elo_rating", 0.30), ("home_court", "home_advantage", 0.10),
                ("rest", "rest_days", 0.15), ("form", "recent_form", 0.20),
                ("starting_pitcher", "pitcher_matchup", 0.25)]
elif competition == "nhl":
    defaults = [("elo", "elo_rating", 0.35), ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15), ("form", "recent_form", 0.20),
                ("goalie", "goalie_matchup", 0.15)]
else:
    return
```

### 6.4 `_get_kernel()` Registration

```python
# After Phase 4 NBA block, add:
if config.settings.PHASE5_MLB_ENABLED:
    from app.sports.baseball.mlb_adapter import MLBAdapter
    from app.sports.baseball.feature_builder import BaseballFeatureBuilder
    from app.sports.baseball.engines.baseball_engine import BaseballEngine
    adapters["mlb-"] = MLBAdapter()
    reg.register(BaseballEngine(factor_registry=factor_registry))
    factor_registry.ensure_competition_factors("mlb")
    builders["mlb-"] = BaseballFeatureBuilder()

if config.settings.PHASE5_NHL_ENABLED:
    from app.sports.hockey.nhl_adapter import NHLAdapter
    from app.sports.hockey.feature_builder import HockeyFeatureBuilder
    from app.sports.hockey.engines.hockey_engine import HockeyEngine
    adapters["nhl-"] = NHLAdapter()
    reg.register(HockeyEngine(factor_registry=factor_registry))
    factor_registry.ensure_competition_factors("nhl")
    builders["nhl-"] = HockeyFeatureBuilder()
```

---

## 7. Testing Strategy

### 7.1 New Test Files (8 files, ~48 tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_shared_elo_calculator.py` | 4 | Shared Elo functions consistency with NBA version |
| `test_mlb_stats_client.py` | 4 | HTTP client, rate limit, exception handling |
| `test_mlb_adapter.py` | 6 | Protocol conformance, parse_mlb_game, fixture/outcome, Elo read |
| `test_baseball_feature_builder.py` | 5 | Protocol conformance, FeatureSet mapping, data_quality, custom fields |
| `test_baseball_engine.py` | 7 | Protocol conformance, 5 factors, weight redistribution, pitcher factor, binary output |
| `test_nhl_stats_client.py` | 4 | HTTP client, rate limit, exception handling |
| `test_nhl_adapter.py` | 6 | Protocol conformance, parse_nhl_game, overtime handling, Elo read |
| `test_hockey_feature_builder.py` | 5 | Protocol conformance, FeatureSet mapping, overtime custom fields |
| `test_hockey_engine.py` | 7 | Protocol conformance, 5 factors, weight redistribution, goalie factor, binary output |

### 7.2 Test Additions to Existing Files (6 test classes)

| File | Addition | Tests |
|------|----------|-------|
| `test_config.py` | `TestPhase5Config` (MLB + NHL config defaults) | 12 |
| `test_kernel_factor_registry.py` | `TestEnsureMLBFactors` + `TestEnsureNHLFactors` | 8 |
| `test_kernel_prediction_kernel.py` | `TestPhase5MLBRegistration` + `TestPhase5NHLRegistration` | 2 |

### 7.3 Total: ~70 new tests + 6 test class additions

All tests follow the established pattern: in-memory or temp SQLite DB with per-test isolation, TDD (RED → GREEN → regression check), protocol conformance via `isinstance` checks.

---

## 8. Phase Roadmap Position

- **Phase 1 (done):** Extract Prediction Kernel + WorldCup Adapter
- **Phase 2/2b (done):** Extend football leagues (UCL → EPL → La Liga → Bundesliga → Serie A → Ligue 1)
- **Phase 3 (done):** Unified learning loop (outcome → error → calibration → weight update → engine score → next prediction)
- **Phase 4 (done):** NBA integration + BasketballEngine — validated multi-sport architecture with binary outcomes
- **Phase 5 (current):** MLB/NHL integration — scales to 4 sports, ~6,872 matches/year, validates architecture at scale
- **Phase 6 (future):** Additional sports (NFL, MLS, ATP/WTA) — `MultiFeatureBuilder` pattern makes this additive

**Goal:** Scale from ~3,130 to ~6,872 matches/year, maintaining 72-75%+ accuracy across all sports through the unified learning loop.
