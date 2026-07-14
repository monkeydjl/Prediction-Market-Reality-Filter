# Sports Prediction OS — Phase 5: MLB/NHL Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate MLB (baseball) and NHL (hockey) as the third and fourth sports, scaling annual coverage from ~3,130 to ~6,872 matches while validating the Kernel's multi-sport architecture with two new 5-factor Bradley-Terry engines.

**Architecture:** Parallel `sports/baseball/` and `sports/hockey/` modules alongside `football/` and `basketball/`. A new `sports/_shared/elo_calculator.py` is a verbatim copy of NBA's Elo functions, imported by both MLB and NHL engines. Each sport gets its own stats client, adapter (DataAdapter Protocol), feature builder (FeatureBuilder Protocol), and 5-factor engine. `MultiFeatureBuilder` prefix-dispatch auto-routes `mlb-`/`nhl-` prefixes. `PredictionKernel`, `domain.py`, `learning_service.py`, and frontend are zero-modification.

**Tech Stack:** Python 3.11+, SQLAlchemy ORM, SQLite, httpx, pytest

## Global Constraints

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

## File Structure

### New Files

| File | Responsibility | Task |
|------|---------------|------|
| `backend/app/sports/_shared/__init__.py` | Shared utilities package init | 2 |
| `backend/app/sports/_shared/elo_calculator.py` | Stateless Elo functions (copy of NBA's) | 2 |
| `backend/app/sports/baseball/__init__.py` | Baseball package init | 3 |
| `backend/app/sports/baseball/mlb_stats_client.py` | HTTP client for statsapi.mlb.com | 3 |
| `backend/app/sports/baseball/mlb_adapter.py` | MLBAdapter (DataAdapter Protocol, `mlb-` prefix) | 4 |
| `backend/app/sports/baseball/feature_builder.py` | BaseballFeatureBuilder (FeatureBuilder Protocol) | 5 |
| `backend/app/sports/baseball/engines/__init__.py` | Engines package init | 6 |
| `backend/app/sports/baseball/engines/baseball_engine.py` | BaseballEngine (5-factor Bradley-Terry) | 6 |
| `backend/app/sports/hockey/__init__.py` | Hockey package init | 8 |
| `backend/app/sports/hockey/nhl_stats_client.py` | HTTP client for api-web.nhle.com | 8 |
| `backend/app/sports/hockey/nhl_adapter.py` | NHLAdapter (DataAdapter Protocol, `nhl-` prefix) | 9 |
| `backend/app/sports/hockey/feature_builder.py` | HockeyFeatureBuilder (FeatureBuilder Protocol) | 10 |
| `backend/app/sports/hockey/engines/__init__.py` | Engines package init | 11 |
| `backend/app/sports/hockey/engines/hockey_engine.py` | HockeyEngine (5-factor Bradley-Terry) | 11 |
| `backend/tests/test_shared_elo_calculator.py` | 4 tests for shared Elo calculator | 2 |
| `backend/tests/test_mlb_stats_client.py` | 4 tests for MLB stats client | 3 |
| `backend/tests/test_mlb_adapter.py` | 6 tests for MLB adapter | 4 |
| `backend/tests/test_baseball_feature_builder.py` | 5 tests for baseball feature builder | 5 |
| `backend/tests/test_baseball_engine.py` | 7 tests for baseball engine | 6 |
| `backend/tests/test_nhl_stats_client.py` | 4 tests for NHL stats client | 8 |
| `backend/tests/test_nhl_adapter.py` | 6 tests for NHL adapter | 9 |
| `backend/tests/test_hockey_feature_builder.py` | 5 tests for hockey feature builder | 10 |
| `backend/tests/test_hockey_engine.py` | 7 tests for hockey engine | 11 |

### Modified Files

| File | Responsibility | Task |
|------|---------------|------|
| `backend/app/core/config.py` | +12 config fields (2 flags + 10 MLB/NHL params) | 1 |
| `backend/.env.example` | +Phase 5 section | 1 |
| `backend/app/kernel/factor_registry.py` | `ensure_competition_factors` adds `"mlb"` (Task 7) and `"nhl"` (Task 12) branches | 7, 12 |
| `backend/app/api/routes/predictions.py` | `_get_kernel()` adds MLB registration (Task 7) and NHL registration (Task 12) blocks | 7, 12 |
| `backend/tests/test_config.py` | +`TestPhase5Config` (12 tests) | 1 |
| `backend/tests/test_kernel_factor_registry.py` | +`TestEnsureMLBFactors` (Task 7, 4 tests) + `TestEnsureNHLFactors` (Task 12, 4 tests) | 7, 12 |
| `backend/tests/test_kernel_prediction_kernel.py` | +`TestPhase5MLBRegistration` (Task 7, 1 test) + `TestPhase5NHLRegistration` (Task 12, 1 test) | 7, 12 |

**Total: ~52 new tests across 9 new test files + 18 test additions across 3 existing files**

---

## Task Dependency Graph

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

Tasks 1, 2, 5, 8 can potentially start in parallel (no inter-dependencies). Tasks 3→4→7 (MLB chain), 8→9→12 (NHL chain), 2→6 and 2→11 (Elo dependency) are sequential.

---

### Task 1: Config + .env.example

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config.py` (append `TestPhase5Config`)

**Interfaces:**
- Consumes: existing `_env_bool` helper, `os.getenv`
- Produces:
  - `config.settings.PHASE5_MLB_ENABLED` (bool, default false)
  - `config.settings.PHASE5_NHL_ENABLED` (bool, default false)
  - `config.settings.MLB_ELO_HFA` (int, default 50)
  - `config.settings.MLB_ELO_K_REGULAR` (int, default 20)
  - `config.settings.MLB_ELO_K_PLAYOFF` (int, default 30)
  - `config.settings.MLB_ELO_SEASON_CARRY` (float, default 0.7)
  - `config.settings.MLB_LEAGUE_AVG_TOTAL` (float, default 8.5)
  - `config.settings.NHL_ELO_HFA` (int, default 55)
  - `config.settings.NHL_ELO_K_REGULAR` (int, default 20)
  - `config.settings.NHL_ELO_K_PLAYOFF` (int, default 30)
  - `config.settings.NHL_ELO_SEASON_CARRY` (float, default 0.75)
  - `config.settings.NHL_LEAGUE_AVG_TOTAL` (float, default 5.5)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config.py` (before the `if __name__ == "__main__":` line):

```python
class TestPhase5Config:
    """Phase 5 MLB/NHL configuration fields."""

    def test_phase5_mlb_enabled_defaults_false(self):
        from app.core import config
        assert config.settings.PHASE5_MLB_ENABLED is False

    def test_phase5_nhl_enabled_defaults_false(self):
        from app.core import config
        assert config.settings.PHASE5_NHL_ENABLED is False

    def test_mlb_elo_hfa_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_HFA == 50

    def test_mlb_elo_k_regular_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_K_REGULAR == 20

    def test_mlb_elo_k_playoff_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_K_PLAYOFF == 30

    def test_mlb_elo_season_carry_default(self):
        from app.core import config
        assert config.settings.MLB_ELO_SEASON_CARRY == 0.7

    def test_mlb_league_avg_total_default(self):
        from app.core import config
        assert config.settings.MLB_LEAGUE_AVG_TOTAL == 8.5

    def test_nhl_elo_hfa_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_HFA == 55

    def test_nhl_elo_k_regular_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_K_REGULAR == 20

    def test_nhl_elo_k_playoff_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_K_PLAYOFF == 30

    def test_nhl_elo_season_carry_default(self):
        from app.core import config
        assert config.settings.NHL_ELO_SEASON_CARRY == 0.75

    def test_nhl_league_avg_total_default(self):
        from app.core import config
        assert config.settings.NHL_LEAGUE_AVG_TOTAL == 5.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_config.py::TestPhase5Config -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'PHASE5_MLB_ENABLED'`

- [ ] **Step 3: Add config fields to config.py**

In `backend/app/core/config.py`, find the line `NBA_LEAGUE_AVG_TOTAL: float = float(os.getenv("NBA_LEAGUE_AVG_TOTAL", "220.0"))` (around line 1007) and add after it:

```python

    # Phase 5 — MLB/NHL Integration (default OFF). When false, mlb-/nhl-
    # prefix match_ids return 404 and MLB/NHL components are not
    # instantiated. MLB/NHL stats APIs require no API key (graceful
    # degradation when unreachable: sync_schedule returns 0).
    PHASE5_MLB_ENABLED: bool = _env_bool("PHASE5_MLB_ENABLED", "false")
    PHASE5_NHL_ENABLED: bool = _env_bool("PHASE5_NHL_ENABLED", "false")

    # MLB Elo parameters (self-computed from historical games)
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

- [ ] **Step 4: Add Phase 5 section to .env.example**

Append at the end of `backend/.env.example`:

```env

# === Phase 5: MLB/NHL Integration ===
# When false, mlb-/nhl- prefix match_ids return 404 and MLB/NHL components are not loaded.
PHASE5_MLB_ENABLED=false  # 中文：是否启用 MLB 棒球集成；默认关闭。
PHASE5_NHL_ENABLED=false  # 中文：是否启用 NHL 冰球集成；默认关闭。
# MLB Elo parameters (self-computed from historical games — no external Elo source)
MLB_ELO_HFA=50  # 中文：MLB 主场优势 Elo 加成（棒球主场优势较低）。
MLB_ELO_K_REGULAR=20  # 中文：MLB 常规赛 K 因子。
MLB_ELO_K_PLAYOFF=30  # 中文：MLB 季后赛 K 因子。
MLB_ELO_SEASON_CARRY=0.7  # 中文：MLB 赛季间 Elo 回归保留比例（长赛季，更多回归）。
MLB_LEAGUE_AVG_TOTAL=8.5  # 中文：MLB 联盟平均总得分，用于预测比分转换。
# NHL Elo parameters
NHL_ELO_HFA=55  # 中文：NHL 主场优势 Elo 加成。
NHL_ELO_K_REGULAR=20  # 中文：NHL 常规赛 K 因子。
NHL_ELO_K_PLAYOFF=30  # 中文：NHL 季后赛 K 因子。
NHL_ELO_SEASON_CARRY=0.75  # 中文：NHL 赛季间 Elo 回归保留比例。
NHL_LEAGUE_AVG_TOTAL=5.5  # 中文：NHL 联盟平均总得分（低得分运动）。
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_config.py::TestPhase5Config -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Verify no regression**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: ALL existing config tests + 12 new Phase 5 tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/core/config.py .env.example tests/test_config.py
git commit -m "feat(phase5): add MLB/NHL integration config fields

Add 12 Phase 5 config settings: PHASE5_MLB_ENABLED (default OFF),
PHASE5_NHL_ENABLED (default OFF), MLB/NHL Elo parameters (HFA, K-factors,
season carry, league avg total). MLB/NHL stats APIs require no API key."
```

---

### Task 2: Shared Elo Calculator

**Files:**
- Create: `backend/app/sports/_shared/__init__.py`
- Create: `backend/app/sports/_shared/elo_calculator.py`
- Test: `backend/tests/test_shared_elo_calculator.py` (4 tests)

**Interfaces:**
- Consumes: nothing (pure stateless functions)
- Produces (verbatim copy of NBA's signatures):
  - `compute_expected_score(elo_home: float, elo_away: float, hfa: int = 100) -> float` — returns E_home (probability home wins)
  - `update_elo(elo: float, expected: float, actual: float, k: int = 20) -> float` — returns new Elo rating
  - `apply_season_regression(elo: float, mean: float = 1500.0, carry: float = 0.75) -> float` — returns regressed Elo
  - `seed_elo_from_games(games: list[dict], hfa: int = 100, k_regular: int = 20, k_playoff: int = 30) -> dict[str, float]` — returns `{team_name: elo_rating}` after processing games chronologically

  `games` is a list of dicts with keys: `home_team` (str), `away_team` (str), `home_score` (int), `away_score` (int), `is_playoff` (bool), `season` (int). Games must be in chronological order. `seed_elo_from_games` applies season regression when the `season` field changes between consecutive games.

  Constraint 21: This is a COPY of `sports/basketball/elo_calculator.py`. The NBA original is NOT modified.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_shared_elo_calculator.py`:

```python
# backend/tests/test_shared_elo_calculator.py
"""Tests for shared Elo calculator — stateless functions consistency with NBA version."""
import pytest

from app.sports._shared.elo_calculator import (
    compute_expected_score,
    update_elo,
    apply_season_regression,
    seed_elo_from_games,
)
from app.sports.basketball.elo_calculator import (
    compute_expected_score as nba_compute_expected_score,
    update_elo as nba_update_elo,
    apply_season_regression as nba_apply_season_regression,
    seed_elo_from_games as nba_seed_elo_from_games,
)


class TestSharedEloConsistency:
    """Verify shared Elo functions produce identical results to NBA's version."""

    def test_compute_expected_score_matches_nba(self):
        """Shared compute_expected_score == NBA's compute_expected_score."""
        for hfa in (0, 50, 55, 100):
            for elo_home in (1400.0, 1500.0, 1600.0, 1800.0):
                for elo_away in (1400.0, 1500.0, 1600.0, 1800.0):
                    shared = compute_expected_score(elo_home, elo_away, hfa)
                    nba = nba_compute_expected_score(elo_home, elo_away, hfa)
                    assert shared == nba, f"mismatch hfa={hfa} h={elo_home} a={elo_away}"

    def test_update_elo_matches_nba(self):
        """Shared update_elo == NBA's update_elo."""
        for elo in (1400.0, 1500.0, 1600.0):
            for expected in (0.3, 0.5, 0.7):
                for actual in (0.0, 1.0):
                    for k in (20, 30):
                        shared = update_elo(elo, expected, actual, k)
                        nba = nba_update_elo(elo, expected, actual, k)
                        assert shared == nba

    def test_apply_season_regression_matches_nba(self):
        """Shared apply_season_regression == NBA's apply_season_regression."""
        for elo in (1300.0, 1500.0, 1700.0):
            for mean in (1500.0, 1600.0):
                for carry in (0.7, 0.75, 0.8):
                    shared = apply_season_regression(elo, mean, carry)
                    nba = nba_apply_season_regression(elo, mean, carry)
                    assert shared == nba

    def test_seed_elo_from_games_matches_nba(self):
        """Shared seed_elo_from_games == NBA's seed_elo_from_games."""
        games = [
            {"home_team": "Yankees", "away_team": "Red Sox",
             "home_score": 5, "away_score": 3, "is_playoff": False, "season": 2023},
            {"home_team": "Red Sox", "away_team": "Yankees",
             "home_score": 7, "away_score": 2, "is_playoff": False, "season": 2023},
            {"home_team": "Yankees", "away_team": "Red Sox",
             "home_score": 4, "away_score": 4, "is_playoff": False, "season": 2024},
        ]
        shared = seed_elo_from_games(games, hfa=50, k_regular=20, k_playoff=30)
        nba = nba_seed_elo_from_games(games, hfa=50, k_regular=20, k_playoff=30)
        assert shared == nba
        assert "Yankees" in shared
        assert "Red Sox" in shared
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_shared_elo_calculator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports._shared'`

- [ ] **Step 3: Create shared package init**

Create `backend/app/sports/_shared/__init__.py`:

```python
# backend/app/sports/_shared/__init__.py
"""Cross-sport stateless utilities shared by multiple sport modules."""
```

- [ ] **Step 4: Create elo_calculator.py**

Create `backend/app/sports/_shared/elo_calculator.py` (verbatim copy of NBA's `elo_calculator.py`):

```python
# backend/app/sports/_shared/elo_calculator.py
"""Stateless Elo computation functions shared across sports.

This is a verbatim copy of ``app/sports/basketball/elo_calculator.py`` so
that MLB/NHL engines can import the same stateless utilities without a
cross-sport import dependency on the basketball module. The NBA original
remains untouched (Phase 5 Constraint 21).

All sports pass their own HFA / K-factors / season-carry at call time, so
the defaults here match basketball (HFA=100, K=20/30, carry=0.75) but are
overridden by callers:
    - MLB: HFA=50, K=20/30, carry=0.7
    - NHL: HFA=55, K=20/30, carry=0.75
"""
from __future__ import annotations


def compute_expected_score(
    elo_home: float, elo_away: float, hfa: int = 100,
) -> float:
    """Compute expected probability that home team wins.

    Uses standard Elo formula with home field advantage:
        E_home = 1 / (1 + 10^((elo_away - elo_home - hfa) / 400))

    Args:
        elo_home: Home team Elo rating.
        elo_away: Away team Elo rating.
        hfa: Home field advantage in Elo points (default 100).

    Returns:
        Expected probability (0.0 to 1.0) that home team wins.
    """
    exponent = (elo_away - elo_home - hfa) / 400.0
    return 1.0 / (1.0 + 10.0 ** exponent)


def update_elo(
    elo: float, expected: float, actual: float, k: int = 20,
) -> float:
    """Update Elo rating after a single game.

    Args:
        elo: Current Elo rating.
        expected: Expected score (from compute_expected_score).
        actual: Actual score (1.0 for win, 0.0 for loss).
        k: K-factor (default 20 for regular season, 30 for playoff).

    Returns:
        New Elo rating.
    """
    return elo + k * (actual - expected)


def apply_season_regression(
    elo: float, mean: float = 1500.0, carry: float = 0.75,
) -> float:
    """Apply season-start regression toward league mean.

    new_elo = carry * old_elo + (1 - carry) * mean

    Args:
        elo: Previous season's final Elo.
        mean: League average Elo (default 1500).
        carry: Fraction of previous Elo to retain (default 0.75).

    Returns:
        Regressed Elo for the new season.
    """
    return carry * elo + (1.0 - carry) * mean


def seed_elo_from_games(
    games: list[dict],
    hfa: int = 100,
    k_regular: int = 20,
    k_playoff: int = 30,
) -> dict[str, float]:
    """Compute final Elo ratings by processing games chronologically.

    All teams start at 1500. Season regression (carry=0.75) is applied
    when the ``season`` field changes between consecutive games.

    Args:
        games: List of game dicts, each with keys:
            - home_team (str)
            - away_team (str)
            - home_score (int)
            - away_score (int)
            - is_playoff (bool)
            - season (int)
            Games MUST be in chronological order.
        hfa: Home field advantage (default 100).
        k_regular: K-factor for regular season (default 20).
        k_playoff: K-factor for playoff (default 30).

    Returns:
        Dict mapping team name to final Elo rating.
    """
    ratings: dict[str, float] = {}
    current_season: int | None = None

    for game in games:
        season = game["season"]
        # Apply regression at season boundary
        if current_season is not None and season != current_season:
            for team in ratings:
                ratings[team] = apply_season_regression(ratings[team])
        current_season = season

        home = game["home_team"]
        away = game["away_team"]
        # Initialize new teams at 1500
        if home not in ratings:
            ratings[home] = 1500.0
        if away not in ratings:
            ratings[away] = 1500.0

        elo_home = ratings[home]
        elo_away = ratings[away]
        expected = compute_expected_score(elo_home, elo_away, hfa)

        home_won = game["home_score"] > game["away_score"]
        actual_home = 1.0 if home_won else 0.0
        actual_away = 1.0 - actual_home

        k = k_playoff if game.get("is_playoff") else k_regular
        ratings[home] = update_elo(elo_home, expected, actual_home, k)
        ratings[away] = update_elo(elo_away, 1.0 - expected, actual_away, k)

    return ratings
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_shared_elo_calculator.py -v`
Expected: PASS (1 test class, 4 test methods)

- [ ] **Step 6: Verify no regression**

Run: `cd backend && python -m pytest tests/test_nba_elo_calculator.py -v`
Expected: ALL existing NBA Elo tests still pass (NBA original untouched).

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/sports/_shared/__init__.py app/sports/_shared/elo_calculator.py tests/test_shared_elo_calculator.py
git commit -m "feat(phase5): add shared Elo calculator (copy of NBA's)

sports/_shared/elo_calculator.py is a verbatim copy of NBA's
elo_calculator.py so MLB/NHL engines can import stateless Elo functions
without cross-sport coupling. NBA original file is NOT modified. Tests
verify byte-identical results between shared and NBA versions."
```

---

### Task 3: MLB Stats Client

**Files:**
- Create: `backend/app/sports/baseball/__init__.py`
- Create: `backend/app/sports/baseball/mlb_stats_client.py`
- Test: `backend/tests/test_mlb_stats_client.py` (4 tests)

**Interfaces:**
- Consumes: `httpx`, `app.core.config`
- Produces:
  - `MLBStatsClientError` exception class
  - `fetch_mlb_schedule(start_date: str, end_date: str) -> list[dict]` — fetches MLB games in a date range (YYYY-MM-DD)
  - `fetch_mlb_game_feed(game_pk: int) -> dict` — fetches full game feed (lineups, scoring, pitcher data)
  - `fetch_mlb_pitcher(person_id: int) -> dict` — fetches pitcher stats (ERA, WHIP)

  Base URL: `https://statsapi.mlb.com/api/v1`. Rate limit 1 req/s via module-level `_last_request_time`. No API key required.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_mlb_stats_client.py`:

```python
# backend/tests/test_mlb_stats_client.py
"""Tests for MLB Stats API client — httpx-based HTTP client."""
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.sports.baseball.mlb_stats_client import (
    fetch_mlb_schedule,
    fetch_mlb_game_feed,
    fetch_mlb_pitcher,
    MLBStatsClientError,
)


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


class TestFetchMlbSchedule:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_games_list(self, mock_rl, mock_get):
        """fetch_mlb_schedule returns the games array from the API response."""
        mock_get.return_value = _ok_response({
            "dates": [
                {"date": "2024-07-04", "games": [
                    {"gamePk": 778812, "status": {"abstractGameState": "Final"}},
                    {"gamePk": 778813, "status": {"abstractGameState": "Final"}},
                ]},
            ],
        })
        games = fetch_mlb_schedule("2024-07-04", "2024-07-04")
        assert len(games) == 2
        assert games[0]["gamePk"] == 778812


class TestFetchMlbGameFeed:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_game_feed_dict(self, mock_rl, mock_get):
        """fetch_mlb_game_feed returns the full feed payload."""
        mock_get.return_value = _ok_response({
            "gamePk": 778812,
            "gameData": {"teams": {"home": {"name": "Yankees"}}},
            "liveData": {"plays": {"allPlays": []}},
        })
        feed = fetch_mlb_game_feed(778812)
        assert feed["gamePk"] == 778812
        assert feed["gameData"]["teams"]["home"]["name"] == "Yankees"


class TestFetchMlbPitcher:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_pitcher_stats(self, mock_rl, mock_get):
        """fetch_mlb_pitcher returns pitcher info with stats."""
        mock_get.return_value = _ok_response({
            "people": [{
                "id": 543037,
                "fullName": "Gerrit Cole",
                "stats": [{"group": {"displayName": "pitching"},
                            "splits": [{"stat": {"era": 3.15, "whip": 1.02}}]}],
            }],
        })
        pitcher = fetch_mlb_pitcher(543037)
        assert pitcher["people"][0]["fullName"] == "Gerrit Cole"
        assert pitcher["people"][0]["stats"][0]["splits"][0]["stat"]["era"] == 3.15


class TestMLBStatsClientError:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_raises_on_non_200(self, mock_rl, mock_get):
        """Non-200 response raises MLBStatsClientError."""
        bad = MagicMock()
        bad.status_code = 500
        bad.text = "Internal Server Error"
        mock_get.return_value = bad
        with pytest.raises(MLBStatsClientError):
            fetch_mlb_schedule("2024-07-04", "2024-07-04")

    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_raises_on_network_error(self, mock_rl, mock_get):
        """httpx.RequestError surfaces as MLBStatsClientError."""
        mock_get.side_effect = httpx.RequestError("DNS failure")
        with pytest.raises(MLBStatsClientError):
            fetch_mlb_game_feed(778812)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_mlb_stats_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.baseball'`

- [ ] **Step 3: Create baseball package init**

Create `backend/app/sports/baseball/__init__.py`:

```python
# backend/app/sports/baseball/__init__.py
"""Baseball sport module — MLB integration (Phase 5)."""
```

- [ ] **Step 4: Create mlb_stats_client.py**

Create `backend/app/sports/baseball/mlb_stats_client.py`:

```python
# backend/app/sports/baseball/mlb_stats_client.py
"""HTTP client for the official MLB Stats API.

Base URL: https://statsapi.mlb.com/api/v1
Authentication: None (official free API).
Rate limit: 1 req/s (polite usage, not API-enforced).

Endpoints used:
    schedule?startDate=...&endDate=...&sportId=1   — list games by date range
    game/{gamePk}/feedLive                         — full game feed (lineups, scoring)
    people/{personId}                              — player/pitcher stats
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://statsapi.mlb.com/api/v1"
_REQUEST_INTERVAL_SECONDS = 1.0  # 1 req/s polite rate limit

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class MLBStatsClientError(Exception):
    """MLB Stats API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain >= 1s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _request(path: str, params: dict[str, Any] | None = None) -> dict:
    """Issue a GET request to the MLB Stats API.

    Returns the parsed JSON payload (dict). Raises MLBStatsClientError
    on non-200 status, timeout, or network error.
    """
    _enforce_rate_limit()
    url = f"{_BASE_URL}{path}"
    try:
        response = httpx.get(url, params=params, timeout=30.0)
    except httpx.TimeoutException as exc:
        raise MLBStatsClientError(f"Request timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise MLBStatsClientError(f"Request failed: {exc}") from exc

    if response.status_code != 200:
        raise MLBStatsClientError(
            f"MLB API error: {response.status_code} - {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise MLBStatsClientError("MLB API returned non-JSON response") from exc


def fetch_mlb_schedule(start_date: str, end_date: str) -> list[dict]:
    """Fetch MLB games in a date range.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format (inclusive).

    Returns:
        List of raw game dicts from the schedule response. Each game dict
        contains ``gamePk``, ``status``, ``teams``, ``gameDate``, etc.
    """
    data = _request(
        "/schedule",
        params={
            "sportId": 1,  # MLB
            "startDate": start_date,
            "endDate": end_date,
        },
    )
    games: list[dict] = []
    for date_entry in data.get("dates", []):
        games.extend(date_entry.get("games", []))
    return games


def fetch_mlb_game_feed(game_pk: int) -> dict:
    """Fetch the full live feed for a single MLB game.

    Args:
        game_pk: MLB gamePk (e.g., 778812).

    Returns:
        Full game feed dict containing ``gameData`` (teams, players, venue)
        and ``liveData`` (plays, scoring, boxscore).
    """
    return _request(f"/game/{game_pk}/feedLive")


def fetch_mlb_pitcher(person_id: int) -> dict:
    """Fetch pitcher stats by person ID.

    Args:
        person_id: MLB person ID (e.g., 543037 for Gerrit Cole).

    Returns:
        Pitcher payload dict containing ``people`` array with stats
        (ERA, WHIP) under ``stats[].splits[].stat``.
    """
    return _request(
        f"/people/{person_id}",
        params={"hydrate": "stats(group=[pitching],type=[season])"},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_mlb_stats_client.py -v`
Expected: PASS (4 test classes, 5 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/baseball/__init__.py app/sports/baseball/mlb_stats_client.py tests/test_mlb_stats_client.py
git commit -m "feat(phase5): add MLB Stats API client

HTTP client for statsapi.mlb.com/api/v1. Three endpoints: schedule
(by date range), game feed (full live data), pitcher stats (ERA/WHIP).
Rate limited to 1 req/s. No API key required. Raises
MLBStatsClientError on non-200 / network errors."
```

---

### Task 4: MLBAdapter

**Files:**
- Create: `backend/app/sports/baseball/mlb_adapter.py`
- Test: `backend/tests/test_mlb_adapter.py` (6 tests)

**Interfaces:**
- Consumes:
  - `config.settings.PHASE5_MLB_ENABLED`
  - `app.kernel.kernel_db` (`KernelMatchFixture`, `KernelMatchResult`, `KernelEloRating`, `get_kernel_session`)
  - `app.sports._shared.elo_calculator.seed_elo_from_games` (from Task 2)
  - `app.sports.baseball.mlb_stats_client` (`fetch_mlb_schedule`, `fetch_mlb_pitcher` from Task 3)
  - `app.kernel.domain` value objects
- Produces:
  - `MLBAdapter` class implementing DataAdapter Protocol
  - `parse_mlb_game(game_data: dict) -> dict | None` — parses raw API response to internal fixture format
  - `query_fixture(match_id: str, model_cls) -> object | None`
  - `query_result(match_id: str, model_cls) -> object | None`
  - `build_match_outcome(result: object) -> MatchOutcome | None`
  - `save_fixture(parsed: dict, competition: str, season: str) -> None`

  Match ID format: `mlb-{gamePk}` (e.g., `mlb-778812`)
  Stage mapping: regular season → `"regular_season"`, postseason → `"playoff"`
  Status mapping: `"Final"` → `"finished"`, else → `"scheduled"`
  `fetch_all_data()` calls `fetch_mlb_pitcher()` for both teams' starters and writes ERA/WHIP to `raw["custom"]`.

  **Important:** Tests must mock DB operations (`query_fixture`, `_fetch_elo_ratings`) to avoid creating real DB files (Phase 4 Task 4 lesson).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_mlb_adapter.py`:

```python
# backend/tests/test_mlb_adapter.py
"""Tests for MLBAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.baseball.mlb_adapter import MLBAdapter, parse_mlb_game


_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)


def _make_match(match_id="mlb-778812") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_MLB, season_key="2024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NYY", name="New York Yankees", competition=_MLB),
        away=TeamIdentity(code="BOS", name="Boston Red Sox", competition=_MLB),
        kickoff_utc=datetime(2024, 7, 4, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="mlb-778812", home="New York Yankees", away="Boston Red Sox"):
    """Create a mock KernelMatchFixture row."""
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "mlb"
    fixture.season = "2024"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 7, 4, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "Yankee Stadium"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestMLBAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = MLBAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseMlbGame:
    def test_parses_regular_season_final_game(self):
        """parse_mlb_game maps API fields to internal fixture format."""
        raw = {
            "gamePk": 778812,
            "season": "2024",
            "gameDate": "2024-07-04T00:00:00Z",
            "teams": {
                "home": {"name": "New York Yankees"},
                "away": {"name": "Boston Red Sox"},
            },
            "status": {"abstractGameState": "Final"},
            "linescore": {"home": {"runs": 5}, "away": {"runs": 3}},
        }
        parsed = parse_mlb_game(raw)
        assert parsed["match_id"] == "mlb-778812"
        assert parsed["home_team"] == "New York Yankees"
        assert parsed["away_team"] == "Boston Red Sox"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"

    def test_parses_postseason_scheduled_game(self):
        """Postseason game maps to 'playoff' stage; non-Final maps to 'scheduled'."""
        raw = {
            "gamePk": 781234,
            "season": "2024",
            "gameDate": "2024-10-05T00:00:00Z",
            "teams": {
                "home": {"name": "Houston Astros"},
                "away": {"name": "Texas Rangers"},
            },
            "status": {"abstractGameState": "Preview"},
            "linescore": {"home": {"runs": 0}, "away": {"runs": 0}},
            "seriesDescription": "American League Championship Series",
        }
        parsed = parse_mlb_game(raw)
        assert parsed["match_id"] == "mlb-781234"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "scheduled"


class TestMLBAdapterGetMatchIdentity:
    @patch("app.sports.baseball.mlb_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = MLBAdapter()
        identity = adapter.get_match_identity("mlb-778812")
        assert identity.match_id == "mlb-778812"
        assert identity.home.name == "New York Yankees"
        assert identity.away.name == "Boston Red Sox"
        assert identity.season.competition.code == "mlb"

    @patch("app.sports.baseball.mlb_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = MLBAdapter()
        identity = adapter.get_match_identity("mlb-nonexistent")
        assert identity.match_id == "mlb-nonexistent"
        assert identity.home.name == "Home"


class TestMLBAdapterFetchAllData:
    @patch("app.sports.baseball.mlb_adapter.query_fixture")
    def test_fetch_all_data_includes_pitcher_era_whip(self, mock_query):
        """fetch_all_data writes pitcher ERA/WHIP into raw['custom']."""
        mock_query.return_value = _make_fixture()

        adapter = MLBAdapter()
        # Mock internal helpers to avoid real DB / API calls
        with patch.object(adapter, "_fetch_elo_ratings",
                          return_value={"New York Yankees": 1520.0, "Boston Red Sox": 1490.0}), \
             patch.object(adapter, "_fetch_starting_pitchers",
                          return_value={
                              "home": {"name": "Gerrit Cole", "era": 3.15, "whip": 1.02},
                              "away": {"name": "Brayan Bello", "era": 4.10, "whip": 1.30},
                          }):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1520.0
            assert raw["team"]["elo_away"] == 1490.0
            assert raw["environment"]["is_home_advantage"] is True
            # Pitcher stats in custom dict
            assert raw["custom"]["pitcher_era_home"] == 3.15
            assert raw["custom"]["pitcher_era_away"] == 4.10
            assert raw["custom"]["pitcher_whip_home"] == 1.02
            assert raw["custom"]["pitcher_whip_away"] == 1.30


class TestMLBAdapterFetchOutcome:
    @patch("app.sports.baseball.mlb_adapter.build_match_outcome")
    @patch("app.sports.baseball.mlb_adapter.query_result")
    def test_fetch_outcome_returns_outcome(self, mock_query, mock_build):
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="mlb-778812",
            home_score=5, away_score=3,
            outcome="home_win",
            finished_at=datetime(2024, 7, 4, 22, 0, tzinfo=timezone.utc),
        )
        adapter = MLBAdapter()
        result = adapter.fetch_outcome("mlb-778812")
        assert result is not None
        assert result.home_score == 5
        assert result.outcome == "home_win"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_mlb_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.baseball.mlb_adapter'`

- [ ] **Step 3: Create mlb_adapter.py**

Create `backend/app/sports/baseball/mlb_adapter.py`:

```python
# backend/app/sports/baseball/mlb_adapter.py
"""MLBAdapter — DataAdapter Protocol implementation for MLB baseball.

Bridges the MLB Stats API to the sport-agnostic DataAdapter Protocol.
The Kernel never sees baseball-specific code — it only sees DataAdapter.

Match ID format: mlb-{gamePk}
Stage mapping: postseason games → "playoff", else → "regular_season"
Status mapping: abstractGameState == "Final" → "finished", else → "scheduled"

When PHASE5_MLB_ENABLED is false, the adapter is not instantiated at all
(gated in _get_kernel). When the MLB API is unreachable, sync_schedule
returns 0 (graceful degradation, no exceptions).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core import config
from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.kernel.kernel_db import (
    get_kernel_session, KernelMatchFixture, KernelMatchResult, KernelEloRating,
)
from app.sports.baseball.mlb_stats_client import (
    fetch_mlb_schedule, fetch_mlb_pitcher, MLBStatsClientError,
)
from app.sports._shared.elo_calculator import seed_elo_from_games

logger = logging.getLogger(__name__)

_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)
_DEFAULT_SEASON = "2024"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2024, 7, 4, tzinfo=timezone.utc)


def parse_mlb_game(game_data: dict) -> dict | None:
    """Parse a raw MLB Stats API game dict into internal fixture format.

    Returns None if game_data is malformed.
    """
    game_pk = game_data.get("gamePk")
    if not game_pk:
        return None

    home_team = game_data.get("teams", {}).get("home", {}).get("name", "")
    away_team = game_data.get("teams", {}).get("away", {}).get("name", "")
    if not home_team or not away_team:
        return None

    date_str = game_data.get("gameDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    # Postseason detection: seriesDescription present OR gameType in LCS/DS/WS
    series_desc = game_data.get("seriesDescription", "")
    game_type = game_data.get("gameType", "")
    is_playoff = bool(series_desc) or game_type in ("D", "L", "F", "W")
    stage = "playoff" if is_playoff else "regular_season"

    status_raw = game_data.get("status", {}).get("abstractGameState", "")
    status = "finished" if status_raw == "Final" else "scheduled"

    linescore = game_data.get("linescore", {})
    home_score = linescore.get("home", {}).get("runs")
    away_score = linescore.get("away", {}).get("runs")

    venue = game_data.get("venue", {}).get("name", "Unknown")

    return {
        "match_id": f"mlb-{game_pk}",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "stage": stage,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "venue": venue,
    }


def query_fixture(match_id: str, model_cls) -> object | None:
    """Query a fixture by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query fixture %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def query_result(match_id: str, model_cls) -> object | None:
    """Query a match result by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query result %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def build_match_outcome(result: object) -> MatchOutcome | None:
    """Build MatchOutcome from a KernelMatchResult row. Binary outcome only."""
    if result is None:
        return None
    home_score = result.home_score or 0
    away_score = result.away_score or 0
    if home_score > away_score:
        outcome = "home_win"
    else:
        outcome = "away_win"
    return MatchOutcome(
        match_id=result.match_id,
        home_score=home_score,
        away_score=away_score,
        outcome=outcome,
        finished_at=result.finished_at or datetime.now(timezone.utc),
    )


def save_fixture(parsed: dict, competition: str, season: str) -> None:
    """Upsert a parsed MLB fixture into kernel_match_fixtures."""
    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        existing = session.get(KernelMatchFixture, parsed["match_id"])
        if existing:
            existing.home_team = parsed["home_team"]
            existing.away_team = parsed["away_team"]
            existing.kickoff_utc = parsed["kickoff_utc"]
            existing.stage = parsed["stage"]
            existing.status = parsed["status"]
            if parsed.get("home_score") is not None:
                existing.home_score = parsed["home_score"]
            if parsed.get("away_score") is not None:
                existing.away_score = parsed["away_score"]
            existing.updated_at = now
        else:
            fixture = KernelMatchFixture(
                match_id=parsed["match_id"],
                competition=competition,
                season=season,
                home_team=parsed["home_team"],
                away_team=parsed["away_team"],
                kickoff_utc=parsed["kickoff_utc"],
                stage=parsed["stage"],
                status=parsed["status"],
                home_score=parsed.get("home_score"),
                away_score=parsed.get("away_score"),
                venue=parsed.get("venue", "Unknown"),
                created_at=now,
                updated_at=now,
            )
            session.add(fixture)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to save fixture %s: %s", parsed.get("match_id"), exc)
    finally:
        session.close()


class MLBAdapter:
    """DataAdapter Protocol implementation for MLB baseball."""

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=_MLB)
        away = TeamIdentity(code="AWAY", name="Away", competition=_MLB)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_MLB, season_key=_DEFAULT_SEASON),
            stage=_DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=_DEFAULT_KICKOFF,
        )

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id, KernelMatchFixture)
        if fixture is None:
            return self._stub_identity(match_id)
        home = TeamIdentity(
            code=(fixture.home_team or "HOME")[:3].upper(),
            name=fixture.home_team or "Home",
            competition=_MLB,
        )
        away = TeamIdentity(
            code=(fixture.away_team or "AWAY")[:3].upper(),
            name=fixture.away_team or "Away",
            competition=_MLB,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(competition=_MLB, season_key=fixture.season or _DEFAULT_SEASON),
            stage=fixture.stage or _DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=fixture.kickoff_utc or _DEFAULT_KICKOFF,
        )

    def _fetch_elo_ratings(self, home_team: str, away_team: str) -> dict[str, float]:
        """Fetch Elo ratings for both teams from kernel_elo_ratings table."""
        session = get_kernel_session()
        try:
            ratings: dict[str, float] = {}
            for team_name in [home_team, away_team]:
                row = session.get(KernelEloRating, team_name)
                if row is not None and row.competition == "mlb":
                    ratings[team_name] = row.elo_rating
            return ratings
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch MLB Elo ratings: %s", exc)
            return {}
        finally:
            session.close()

    def _fetch_starting_pitchers(self, match: MatchIdentity) -> dict:
        """Fetch starting pitcher ERA/WHIP for both teams.

        Returns dict with 'home' and 'away' keys, each containing
        {'name': str, 'era': float, 'whip': float} or empty dict if
        unavailable. Stubbed in tests; in production this would call
        fetch_mlb_pitcher() using probable pitcher IDs from the game feed.
        """
        # Production: would call fetch_mlb_game_feed(match.match_id) to get
        # probable pitchers, then fetch_mlb_pitcher(person_id) for each.
        # Returns empty stubs when data is unavailable (graceful degradation).
        return {"home": {}, "away": {}}

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an MLB match.

        All data comes from local DB (Elo, form, rest) and the MLB Stats
        API (pitcher ERA/WHIP). Pitcher stats are written to raw['custom'].
        """
        home_name = match.home.name
        away_name = match.away.name

        elo_ratings = self._fetch_elo_ratings(home_name, away_name)
        elo_home = elo_ratings.get(home_name)
        elo_away = elo_ratings.get(away_name)

        form_home = self._compute_form(home_name)
        form_away = self._compute_form(away_name)

        rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
        rest_away = self._compute_rest_days(away_name, match.kickoff_utc)

        pitchers = self._fetch_starting_pitchers(match)
        home_p = pitchers.get("home", {})
        away_p = pitchers.get("away", {})

        raw: dict = {
            "team": {
                "elo_home": elo_home,
                "elo_away": elo_away,
                "form_home": form_home,
                "form_away": form_away,
            },
            "general": {
                "rest_days_home": rest_home,
                "rest_days_away": rest_away,
                "days_since_last_match": rest_home,
            },
            "market": {},  # No odds source
            "player": {
                "starting_pitcher_home": home_p.get("name"),
                "starting_pitcher_away": away_p.get("name"),
            },
            "environment": {
                "venue": "Home Ballpark",
                "is_home_advantage": True,
            },
            "custom": {
                "pitcher_era_home": home_p.get("era", 4.20),
                "pitcher_era_away": away_p.get("era", 4.20),
                "pitcher_whip_home": home_p.get("whip", 1.30),
                "pitcher_whip_away": away_p.get("whip", 1.30),
                "team_batting_avg_home": 0.250,
                "team_batting_avg_away": 0.250,
                "team_era_home": 4.10,
                "team_era_away": 4.10,
                "pythagorean_win_pct_home": 0.500,
                "pythagorean_win_pct_away": 0.500,
            },
        }
        return raw

    def _compute_form(self, team_name: str) -> float:
        """Compute last-10 win rate from kernel_match_results. Returns 0.5 if none."""
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "mlb",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                    KernelMatchFixture.status == "finished",
                )
                .order_by(KernelMatchFixture.kickoff_utc.desc())
                .limit(10)
            )
            fixtures = session.execute(query).scalars().all()
            if not fixtures:
                return 0.5
            wins = 0
            for f in fixtures:
                if f.home_team == team_name:
                    if (f.home_score or 0) > (f.away_score or 0):
                        wins += 1
                else:
                    if (f.away_score or 0) > (f.home_score or 0):
                        wins += 1
            return wins / len(fixtures)
        except Exception:  # noqa: BLE001
            return 0.5
        finally:
            session.close()

    def _compute_rest_days(self, team_name: str, kickoff_utc: datetime) -> int:
        """Compute days since last match. Returns 0 if unknown."""
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture.kickoff_utc)
                .where(
                    KernelMatchFixture.competition == "mlb",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                    KernelMatchFixture.kickoff_utc < kickoff_utc,
                )
                .order_by(KernelMatchFixture.kickoff_utc.desc())
                .limit(1)
            )
            result = session.execute(query).scalar_one_or_none()
            if result is None:
                return 0
            delta = kickoff_utc - result
            return max(0, delta.days)
        except Exception:  # noqa: BLE001
            return 0
        finally:
            session.close()

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        """Sync MLB schedule from the MLB Stats API.

        Returns 0 if PHASE5_MLB_ENABLED is false or sync fails (graceful
        degradation, no exceptions).
        """
        if not config.settings.PHASE5_MLB_ENABLED:
            return 0
        try:
            today = datetime.now(timezone.utc)
            # Sync current season (Apr–Nov)
            start = f"{today.year}-03-01"
            end = f"{today.year}-11-30"
            games_raw = fetch_mlb_schedule(start, end)
            count = 0
            for raw in games_raw:
                parsed = parse_mlb_game(raw)
                if parsed:
                    save_fixture(parsed, "mlb", str(raw.get("season", _DEFAULT_SEASON)))
                    count += 1
            return count
        except MLBStatsClientError as exc:
            logger.error("MLB API error during sync_schedule: %s", exc)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync MLB schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "mlb"
            )
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)
            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(
                    match=self.get_match_identity(f.match_id), raw_json={}
                )
                for f in fixtures
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch MLB schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_mlb_adapter.py -v`
Expected: PASS (5 test classes, 6 test methods)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sports/baseball/mlb_adapter.py tests/test_mlb_adapter.py
git commit -m "feat(phase5): add MLBAdapter with mlb- prefix

Implements DataAdapter Protocol for MLB baseball. Parses MLB Stats API
game data (gamePk, teams, status, linescore) into internal fixture
format. fetch_all_data reads Elo from kernel_elo_ratings and writes
pitcher ERA/WHIP to raw['custom']. sync_schedule returns 0 on API
error (graceful degradation). Tests mock DB to avoid real DB files."
```

---

### Task 5: BaseballFeatureBuilder

**Files:**
- Create: `backend/app/sports/baseball/feature_builder.py`
- Test: `backend/tests/test_baseball_feature_builder.py` (5 tests)

**Interfaces:**
- Consumes: `app.kernel.domain` value objects (`SportIdentity`, `FeatureSet`, etc.)
- Produces: `BaseballFeatureBuilder` class implementing FeatureBuilder Protocol
  - `sport() -> SportIdentity` returns `SportIdentity(code="baseball", name="Baseball")`
  - `build(match: MatchIdentity, raw: dict) -> FeatureSet` with `feature_version = "mlb-1.0"`
  - Data quality: `"real"` if `raw["team"]["elo_home"] is not None`, `"partial"` otherwise
  - Custom dict: `pitcher_era_*`, `pitcher_whip_*`, `team_batting_avg_*`, `team_era_*`, `pythagorean_win_pct_*`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_baseball_feature_builder.py`:

```python
# backend/tests/test_baseball_feature_builder.py
"""Tests for BaseballFeatureBuilder — FeatureBuilder Protocol."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.baseball.feature_builder import BaseballFeatureBuilder


_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="mlb-778812",
        season=SeasonIdentity(competition=_MLB, season_key="2024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NYY", name="New York Yankees", competition=_MLB),
        away=TeamIdentity(code="BOS", name="Boston Red Sox", competition=_MLB),
        kickoff_utc=datetime(2024, 7, 4, tzinfo=timezone.utc),
    )


def _make_raw_with_elo():
    return {
        "team": {"elo_home": 1520.0, "elo_away": 1490.0, "form_home": 0.6, "form_away": 0.45},
        "general": {"rest_days_home": 1, "rest_days_away": 2, "days_since_last_match": 1},
        "market": {},
        "player": {"starting_pitcher_home": "Gerrit Cole", "starting_pitcher_away": "Brayan Bello"},
        "environment": {"venue": "Yankee Stadium", "is_home_advantage": True},
        "custom": {
            "pitcher_era_home": 3.15, "pitcher_era_away": 4.10,
            "pitcher_whip_home": 1.02, "pitcher_whip_away": 1.30,
            "team_batting_avg_home": 0.255, "team_batting_avg_away": 0.245,
            "team_era_home": 3.90, "team_era_away": 4.20,
            "pythagorean_win_pct_home": 0.560, "pythagorean_win_pct_away": 0.480,
        },
    }


class TestBaseballFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        builder = BaseballFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_baseball(self):
        builder = BaseballFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "baseball"
        assert sport.name == "Baseball"


class TestBaseballFeatureBuilderBuild:
    def test_full_feature_mapping(self):
        """All layers are mapped correctly from raw dict."""
        builder = BaseballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())

        # General layer
        assert features.general.rest_days_home == 1
        assert features.general.rest_days_away == 2

        # Team layer
        assert features.team.elo_rating_home == 1520.0
        assert features.team.elo_rating_away == 1490.0
        assert features.team.form_home == 0.6
        assert features.team.form_away == 0.45
        assert features.team.h2h_draw_rate is None  # Baseball has no draws
        assert features.team.market_value_home is None

        # Market layer — all None (no odds source)
        assert features.market.odds_home is None
        assert features.market.odds_away is None

        # Environment layer
        assert features.environment.venue == "Yankee Stadium"
        assert features.environment.is_home_advantage is True
        assert features.environment.weather_temp_c is None

        # Custom layer — baseball-specific features
        assert features.custom["pitcher_era_home"] == 3.15
        assert features.custom["pitcher_era_away"] == 4.10
        assert features.custom["pitcher_whip_home"] == 1.02
        assert features.custom["team_batting_avg_home"] == 0.255
        assert features.custom["team_era_away"] == 4.20
        assert features.custom["pythagorean_win_pct_home"] == 0.560

        # Feature version
        assert features.feature_version == "mlb-1.0"

    def test_data_quality_real_when_elo_present(self):
        """Data quality is 'real' when Elo exists, even without odds."""
        builder = BaseballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.data_quality == "real"
        assert "betting_odds_unavailable" not in features.quality_notes

    def test_data_quality_partial_when_elo_missing(self):
        """Data quality is 'partial' when Elo is None."""
        builder = BaseballFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["team"]["elo_home"] = None
        raw["team"]["elo_away"] = None
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_baseball_feature_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.baseball.feature_builder'`

- [ ] **Step 3: Create feature_builder.py**

Create `backend/app/sports/baseball/feature_builder.py`:

```python
# backend/app/sports/baseball/feature_builder.py
"""BaseballFeatureBuilder — computes FeatureSet from raw MLB data.

Maps raw dict from MLBAdapter to standardized FeatureSet. Same pattern
as BasketballFeatureBuilder: odds absence does NOT downgrade data quality
because there is no odds source by design, and BaseballEngine does not
use odds.

Feature version: "mlb-1.0" (distinct from football's "1.0" and
basketball's "nba-1.0").
"""
from __future__ import annotations

import logging

from app.kernel.domain import (
    SportIdentity,
    MatchIdentity,
    FeatureSet,
    GeneralFeatures,
    TeamFeatures,
    MarketFeatures,
    PlayerFeatures,
    EnvironmentFeatures,
)

logger = logging.getLogger(__name__)

_BASEBALL = SportIdentity(code="baseball", name="Baseball")


class BaseballFeatureBuilder:
    """Builds FeatureSet for MLB baseball matches.

    Implements the FeatureBuilder Protocol. Consumes a raw dict with
    keys ``team``, ``market``, ``player``, ``environment``, ``general``,
    and ``custom`` and produces a FeatureSet.
    """

    def sport(self) -> SportIdentity:
        return _BASEBALL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Data quality: "real" if Elo exists, "partial" otherwise.
        # Odds absence does NOT downgrade quality (no odds source for MLB).
        has_elo = team_raw.get("elo_home") is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = []

        # Pitcher availability flag for player layer
        pitcher_home = player_raw.get("starting_pitcher_home")
        pitcher_away = player_raw.get("starting_pitcher_away")
        pitcher_home_available = pitcher_home is not None
        pitcher_away_available = pitcher_away is not None

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=None,  # Not tracked for baseball
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for baseball
                h2h_draw_rate=None,  # Baseball has no draws
                market_value_home=None,
                market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=None,  # No odds source
                odds_draw=None,
                odds_away=None,
                odds_source=None,
                odds_fresh=False,
            ),
            player=PlayerFeatures(
                key_players_available_home=pitcher_home_available,
                key_players_available_away=pitcher_away_available,
                injury_impact_home=None,
                injury_impact_away=None,
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,  # Outdoor but not modeled
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="mlb-1.0",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_baseball_feature_builder.py -v`
Expected: PASS (2 test classes, 5 test methods)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sports/baseball/feature_builder.py tests/test_baseball_feature_builder.py
git commit -m "feat(phase5): add BaseballFeatureBuilder

Maps MLB raw dict to FeatureSet with feature_version='mlb-1.0'.
Data quality is 'real' when Elo exists (odds absence does NOT
downgrade). Custom dict holds pitcher_era/whip, team_batting_avg,
team_era, pythagorean_win_pct baseball-specific features."
```

---

### Task 6: BaseballEngine

**Files:**
- Create: `backend/app/sports/baseball/engines/__init__.py`
- Create: `backend/app/sports/baseball/engines/baseball_engine.py`
- Test: `backend/tests/test_baseball_engine.py` (7 tests)

**Interfaces:**
- Consumes:
  - `app.kernel.domain` (`FeatureSet`, `MatchIdentity`, `PredictionResult`, `ContributionItem`)
  - `app.kernel.factor_registry.FactorRegistry` (optional)
  - `app.core.config.settings` (`MLB_ELO_HFA`, `MLB_LEAGUE_AVG_TOTAL` — read at call time, from Task 1)
  - `app.sports._shared.elo_calculator.compute_expected_score` (from Task 2)
- Produces: `BaseballEngine` class implementing PredictionEngine Protocol
  - `name() -> str` returns `"baseball"`
  - `supported_sports() -> list[str]` returns `["baseball"]`
  - `predict(features: FeatureSet, match: MatchIdentity) -> PredictionResult`
  - 5 factors: `elo` (0.30), `home_court` (0.10), `rest` (0.15), `form` (0.20), `starting_pitcher` (0.25)
  - Bradley-Terry binary model: `outcome_probabilities = {"home_win": p, "away_win": 1-p}`
  - home_court constant: `0.54` (MLB historical home win rate, lower than NBA 0.58)
  - starting_pitcher: `p = 0.5 + clamp(era_diff, -2.0, 2.0) * 0.1` where `era_diff = era_away - era_home`
  - Weight redistribution for unavailable factors (same pattern as BasketballEngine)
  - Score conversion: `margin = (elo_home - elo_away + hfa) * 0.03`, scores centered on `MLB_LEAGUE_AVG_TOTAL/2`
  - Confidence: `min(max(p_home, p_away) * 0.95, 0.95)`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_baseball_engine.py`:

```python
# backend/tests/test_baseball_engine.py
"""Tests for BaseballEngine — 5-factor Bradley-Terry binary prediction engine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.baseball.engines.baseball_engine import BaseballEngine


_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)


def _make_features(
    elo_home=1520.0, elo_away=1490.0,
    form_home=0.6, form_away=0.45,
    rest_home=1, rest_away=2,
    era_home=3.15, era_away=4.10,
) -> FeatureSet:
    comp = _MLB
    season = SeasonIdentity(competition=comp, season_key="2024")
    home = TeamIdentity(code="NYY", name="New York Yankees", competition=comp)
    away = TeamIdentity(code="BOS", name="Boston Red Sox", competition=comp)
    match = MatchIdentity(
        match_id="mlb-778812", season=season,
        stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 7, 4, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(
            rest_days_home=float(rest_home) if rest_home is not None else None,
            rest_days_away=float(rest_away) if rest_away is not None else None,
            travel_distance_km=None,
            days_since_last_match=None,
        ),
        team=TeamFeatures(
            elo_rating_home=elo_home,
            elo_rating_away=elo_away,
            form_home=form_home,
            form_away=form_away,
            h2h_home_win_rate=None, h2h_draw_rate=None,
            market_value_home=None, market_value_away=None,
        ),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures("Yankee Stadium", None, None, True),
        custom={
            "pitcher_era_home": era_home, "pitcher_era_away": era_away,
            "pitcher_whip_home": 1.02, "pitcher_whip_away": 1.30,
            "team_batting_avg_home": 0.255, "team_batting_avg_away": 0.245,
            "team_era_home": 3.90, "team_era_away": 4.20,
            "pythagorean_win_pct_home": 0.560, "pythagorean_win_pct_away": 0.480,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="mlb-1.0",
    )


class TestBaseballEngineProtocol:
    def test_implements_protocol(self):
        engine = BaseballEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name_and_supported_sports(self):
        engine = BaseballEngine()
        assert engine.name() == "baseball"
        assert "baseball" in engine.supported_sports()


class TestBaseballEnginePredict:
    def test_predict_returns_binary_probabilities(self):
        """Outcome probabilities have home_win and away_win (no draw)."""
        engine = BaseballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        """Higher Elo home team → P(home_win) > P(away_win)."""
        engine = BaseballEngine()
        strong = _make_features(elo_home=1700, elo_away=1400)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_explanation_has_five_factors(self):
        """Explanation contains all 5 factors: elo, home_court, rest, form, starting_pitcher."""
        engine = BaseballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        factor_ids = [e.factor for e in result.explanation]
        assert "elo" in factor_ids
        assert "home_court" in factor_ids
        assert "rest" in factor_ids
        assert "form" in factor_ids
        assert "starting_pitcher" in factor_ids

    def test_contribution_item_predicted_outcome_is_binary(self):
        """Each ContributionItem.predicted_outcome is home_win or away_win."""
        engine = BaseballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        for item in result.explanation:
            assert item.predicted_outcome in ("home_win", "away_win", None)

    def test_better_home_pitcher_increases_home_win_prob(self):
        """Lower home ERA (better pitcher) → higher P(home_win)."""
        engine = BaseballEngine()
        # Home pitcher much better (lower ERA)
        better_home = _make_features(era_home=2.50, era_away=5.00)
        # Equal pitchers
        equal = _make_features(era_home=4.00, era_away=4.00)
        p_better = engine.predict(better_home, better_home.match).outcome_probabilities["home_win"]
        p_equal = engine.predict(equal, equal.match).outcome_probabilities["home_win"]
        assert p_better > p_equal

    def test_no_elo_fallback(self):
        """When Elo is None, engine still produces valid prediction via weight redistribution."""
        engine = BaseballEngine()
        features = _make_features(elo_home=None, elo_away=None)
        result = engine.predict(features, features.match)
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.available is False
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01
        # Other factors still contribute
        assert result.outcome_probabilities["home_win"] != 0.5 or \
               result.outcome_probabilities["away_win"] != 0.5

    def test_score_conversion_uses_league_avg(self):
        """Predicted scores are centered around MLB league avg total (8.5)."""
        engine = BaseballEngine()
        features = _make_features(elo_home=1500, elo_away=1500)
        result = engine.predict(features, features.match)
        home_score = result.predicted_scores["home"]
        away_score = result.predicted_scores["away"]
        # League avg = 8.5, so each ~4.25 (plus HFA adjustment)
        assert 3.0 < home_score < 6.0
        assert 3.0 < away_score < 6.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_baseball_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.baseball.engines'`

- [ ] **Step 3: Create engines package init**

Create `backend/app/sports/baseball/engines/__init__.py`:

```python
# backend/app/sports/baseball/engines/__init__.py
"""Baseball prediction engines."""
```

- [ ] **Step 4: Create baseball_engine.py**

Create `backend/app/sports/baseball/engines/baseball_engine.py`:

```python
# backend/app/sports/baseball/engines/baseball_engine.py
"""BaseballEngine — 5-factor Bradley-Terry binary prediction engine.

5 factors that each compute P(home_win), then weighted-average fusion.
MLB has binary outcomes (home_win/away_win, no draws).

Factors:
    elo (0.30)             — Elo-based win probability with HFA=50
    home_court (0.10)      — MLB historical home win rate (constant 0.54)
    rest (0.15)            — Rest days differential
    form (0.20)            — Recent form (last-10 win rate)
    starting_pitcher (0.25)— Starting pitcher ERA differential

Starting pitcher formula:
    era_diff = era_away - era_home   (home pitcher better → lower ERA → era_diff > 0)
    p = 0.5 + clamp(era_diff, -2.0, 2.0) * 0.1

Weights are read from FactorRegistry at call time, falling back to
defaults if FactorRegistry is None. When a factor is unavailable, its
weight is redistributed proportionally to available factors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core import config
from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.sports._shared.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS = {
    "elo": 0.30,
    "home_court": 0.10,
    "rest": 0.15,
    "form": 0.20,
    "starting_pitcher": 0.25,
}

# MLB historical home win rate (constant — lower than NBA's 0.58)
_HOME_COURT_PROB = 0.54


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class BaseballEngine:
    """5-factor Bradley-Terry binary outcome engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "baseball"

    def supported_sports(self) -> list[str]:
        return ["baseball"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        hfa = config.settings.MLB_ELO_HFA
        league_avg = config.settings.MLB_LEAGUE_AVG_TOTAL

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            weights = {
                fid: self._factor_registry.get_weight(fid, competition)
                for fid in _DEFAULT_WEIGHTS
            }
        else:
            weights = dict(_DEFAULT_WEIGHTS)

        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo factor
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        if elo_home is not None and elo_away is not None:
            p_elo = compute_expected_score(elo_home, elo_away, hfa)
            elo_available = True
        else:
            p_elo = 0.5
            elo_available = False
        factors.append(("elo", p_elo, weights["elo"], elo_available))

        # 2. Home court factor (constant — MLB lower home advantage than NBA)
        p_home_court = _HOME_COURT_PROB
        factors.append(("home_court", p_home_court, weights["home_court"], True))

        # 3. Rest factor
        rest_home = features.general.rest_days_home
        rest_away = features.general.rest_days_away
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            p_rest = 0.5 + rest_diff * 0.03
            rest_available = True
        else:
            p_rest = 0.5
            rest_available = False
        factors.append(("rest", p_rest, weights["rest"], rest_available))

        # 4. Form factor
        form_home = features.team.form_home
        form_away = features.team.form_away
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            p_form = 0.5 + form_diff * 0.5
            form_available = True
        else:
            p_form = 0.5
            form_available = False
        factors.append(("form", p_form, weights["form"], form_available))

        # 5. Starting pitcher factor
        # era_diff = era_away - era_home; home pitcher better (lower ERA) → era_diff > 0 → p > 0.5
        era_home = features.custom.get("pitcher_era_home")
        era_away = features.custom.get("pitcher_era_away")
        if era_home is not None and era_away is not None:
            era_diff = _clamp(era_away - era_home, -2.0, 2.0)
            p_pitcher = 0.5 + era_diff * 0.1
            pitcher_available = True
        else:
            p_pitcher = 0.5
            pitcher_available = False
        factors.append(("starting_pitcher", p_pitcher, weights["starting_pitcher"], pitcher_available))

        # Weighted fusion — redistribute unavailable factor weights
        available_factors = [(f, p, w) for f, p, w, a in factors if a]
        total_w = sum(w for _, _, w in available_factors)
        if total_w > 0:
            p_home = sum(p * (w / total_w) for _, p, w in available_factors)
        else:
            p_home = 0.5  # All factors unavailable → neutral
        p_away = 1.0 - p_home

        outcome_probabilities = {
            "home_win": round(p_home, 4),
            "away_win": round(p_away, 4),
        }

        # Score conversion (MLB: league_avg=8.5, low-scoring)
        if elo_home is not None and elo_away is not None:
            margin = (elo_home - elo_away + hfa) * 0.03
        else:
            margin = 0.0
        home_score = league_avg / 2 + margin / 2
        away_score = league_avg / 2 - margin / 2
        predicted_scores = {
            "home": round(home_score, 1),
            "away": round(away_score, 1),
        }

        # Confidence (same formula as BasketballEngine)
        confidence = round(min(max(p_home, p_away) * 0.95, 0.95), 4)

        # Build explanation with ContributionItems
        explanation: list[ContributionItem] = []
        for fid, p, w, available in factors:
            predicted_outcome = "home_win" if p >= 0.5 else "away_win"
            explanation.append(ContributionItem(
                factor=fid,
                direction="support" if available else "neutral",
                weight=w,
                available=available,
                detail=f"P(home_win)={round(p, 4)}" if available else f"{fid} unavailable",
                predicted_outcome=predicted_outcome if available else None,
            ))

        return PredictionResult(
            predicted_scores=predicted_scores,
            outcome_probabilities=outcome_probabilities,
            confidence=confidence,
            engine_name="baseball",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_baseball_engine.py -v`
Expected: PASS (2 test classes, 7 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/baseball/engines/__init__.py app/sports/baseball/engines/baseball_engine.py tests/test_baseball_engine.py
git commit -m "feat(phase5): add BaseballEngine with 5-factor Bradley-Terry model

5 factors: elo(0.30), home_court(0.10), rest(0.15), form(0.20),
starting_pitcher(0.25). Binary outcomes (home_win/away_win, no draw).
Starting pitcher factor uses ERA differential (era_away - era_home).
Home court constant 0.54 (lower than NBA's 0.58). Weight redistribution
for unavailable factors. Reads HFA and league avg from config.settings."
```

---

### Task 7: MLB API Integration + FactorRegistry

**Files:**
- Modify: `backend/app/kernel/factor_registry.py` (add `"mlb"` branch to `ensure_competition_factors`)
- Modify: `backend/app/api/routes/predictions.py` (add MLB registration block in `_get_kernel`)
- Test: `backend/tests/test_kernel_factor_registry.py` (append `TestEnsureMLBFactors`, 4 tests)
- Test: `backend/tests/test_kernel_prediction_kernel.py` (append `TestPhase5MLBRegistration`, 1 test)

**Interfaces:**
- Consumes: All MLB components from Tasks 3-6 (MLBAdapter, BaseballFeatureBuilder, BaseballEngine)
- Produces:
  - Modified `FactorRegistry.ensure_competition_factors("mlb")` seeds 5 MLB factors
  - Modified `_get_kernel()` registers MLB components when `PHASE5_MLB_ENABLED` is true

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kernel_factor_registry.py` (after `TestEnsureCompetitionFactors`):

```python
class TestEnsureMLBFactors:
    """Phase 5: ensure_competition_factors for MLB factor seeding."""

    def test_seeds_mlb_factors_when_empty(self):
        """MLB factors are seeded when none exist for 'mlb' competition."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        # Before: no MLB-specific factors; falls back to global elo=0.30
        assert reg.get_weight("elo", "mlb") == 0.30

        reg.ensure_competition_factors("mlb")

        # After: MLB factors seeded with correct weights
        assert reg.get_weight("elo", "mlb") == 0.30
        assert reg.get_weight("home_court", "mlb") == 0.10
        assert reg.get_weight("rest", "mlb") == 0.15
        assert reg.get_weight("form", "mlb") == 0.20
        assert reg.get_weight("starting_pitcher", "mlb") == 0.25

    def test_idempotent_when_already_seeded(self):
        """Calling twice doesn't duplicate or overwrite factors."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        reg.ensure_competition_factors("mlb")
        reg.ensure_competition_factors("mlb")  # Second call

        assert reg.get_weight("elo", "mlb") == 0.30
        assert reg.get_weight("starting_pitcher", "mlb") == 0.25

    def test_football_defaults_unchanged(self):
        """MLB seeding doesn't affect football global defaults."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        reg.ensure_competition_factors("mlb")

        assert reg.get_weight("elo", "world_cup") == 0.30
        assert reg.get_weight("odds", "world_cup") == 0.70

    def test_nba_factors_unchanged(self):
        """MLB seeding doesn't affect NBA factors."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        reg.ensure_competition_factors("nba")
        reg.ensure_competition_factors("mlb")

        # NBA factors unchanged
        assert reg.get_weight("elo", "nba") == 0.45
        assert reg.get_weight("home_court", "nba") == 0.15
```

Append to `backend/tests/test_kernel_prediction_kernel.py` (after `TestPhase4KernelRegistration`):

```python
class TestPhase5MLBRegistration:
    """Phase 5: MLB components are registered when PHASE5_MLB_ENABLED is true."""

    def test_mlb_engine_registered_when_enabled(self, tmp_path, monkeypatch):
        """When PHASE5_MLB_ENABLED=true, BaseballEngine is in EngineRegistry."""
        import app.core.config as config_module
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session

        db_path = str(tmp_path / "kernel_api_test_mlb.db")
        init_kernel_db(db_path)
        try:
            monkeypatch.setattr(
                config_module.settings, "KERNEL_PREDICTION_ENABLED", True
            )
            monkeypatch.setattr(
                config_module.settings, "PHASE5_MLB_ENABLED", True
            )

            # Clear cached kernel
            from app.api.routes import predictions
            if hasattr(predictions._get_kernel, "_instance"):
                delattr(predictions._get_kernel, "_instance")

            kernel = predictions._get_kernel()
            engines = kernel._engine_registry.list_engines()
            assert "baseball" in engines
            assert "elo_odds" in engines
        finally:
            close_kernel_session()
            from app.api.routes import predictions
            if hasattr(predictions._get_kernel, "_instance"):
                delattr(predictions._get_kernel, "_instance")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py::TestEnsureMLBFactors tests/test_kernel_prediction_kernel.py::TestPhase5MLBRegistration -v`
Expected: FAIL — MLB factors not seeded (`get_weight("starting_pitcher", "mlb")` returns 1.0 default), and `"baseball" not in engines`.

- [ ] **Step 3: Add MLB branch to `ensure_competition_factors`**

In `backend/app/kernel/factor_registry.py`, find the `ensure_competition_factors` method. Locate the `if competition == "nba":` block and add an `elif competition == "mlb":` branch after it:

**Before:**
```python
        if competition == "nba":
            defaults = [
                ("elo", "elo_rating", 0.45),
                ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.25),
            ]
        else:
            return  # Unknown competition — no defaults
```

**After:**
```python
        if competition == "nba":
            defaults = [
                ("elo", "elo_rating", 0.45),
                ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.25),
            ]
        elif competition == "mlb":
            defaults = [
                ("elo", "elo_rating", 0.30),
                ("home_court", "home_advantage", 0.10),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.20),
                ("starting_pitcher", "pitcher_matchup", 0.25),
            ]
        else:
            return  # Unknown competition — no defaults
```

- [ ] **Step 4: Add MLB registration block to `_get_kernel`**

In `backend/app/api/routes/predictions.py`, find the NBA registration block (around lines 71-91). After the closing `else: feature_builder = fb` of the NBA block, add the MLB registration block.

**Find this section:**
```python
        if config.settings.PHASE4_NBA_ENABLED:
            from app.sports.basketball.nba_adapter import NBAAdapter
            from app.sports.basketball.feature_builder import BasketballFeatureBuilder
            from app.sports.basketball.engines.basketball_engine import BasketballEngine
            from app.kernel.multi_feature_builder import MultiFeatureBuilder

            adapters["nba-"] = NBAAdapter()
            nba_engine = BasketballEngine(factor_registry=factor_registry)
            reg.register(nba_engine)

            factor_registry.ensure_competition_factors("nba")

            # All football prefixes share the same FootballFeatureBuilder instance
            builders = {
                "wc-": fb, "ucl-": fb, "epl-": fb,
                "laliga-": fb, "bundesliga-": fb, "seriea-": fb, "ligue1-": fb,
                "nba-": BasketballFeatureBuilder(),
            }
            feature_builder = MultiFeatureBuilder(builders)
        else:
            feature_builder = fb
```

**Replace with:**
```python
        # Start with football-only builders dict; sport flags below extend it
        builders: dict[str, object] = {
            "wc-": fb, "ucl-": fb, "epl-": fb,
            "laliga-": fb, "bundesliga-": fb, "seriea-": fb, "ligue1-": fb,
        }
        feature_builder: object = fb  # default — replaced by MultiFeatureBuilder if any sport enabled

        if config.settings.PHASE4_NBA_ENABLED:
            from app.sports.basketball.nba_adapter import NBAAdapter
            from app.sports.basketball.feature_builder import BasketballFeatureBuilder
            from app.sports.basketball.engines.basketball_engine import BasketballEngine
            from app.kernel.multi_feature_builder import MultiFeatureBuilder

            adapters["nba-"] = NBAAdapter()
            nba_engine = BasketballEngine(factor_registry=factor_registry)
            reg.register(nba_engine)

            factor_registry.ensure_competition_factors("nba")
            builders["nba-"] = BasketballFeatureBuilder()

        if config.settings.PHASE5_MLB_ENABLED:
            from app.sports.baseball.mlb_adapter import MLBAdapter
            from app.sports.baseball.feature_builder import BaseballFeatureBuilder
            from app.sports.baseball.engines.baseball_engine import BaseballEngine

            adapters["mlb-"] = MLBAdapter()
            mlb_engine = BaseballEngine(factor_registry=factor_registry)
            reg.register(mlb_engine)

            factor_registry.ensure_competition_factors("mlb")
            builders["mlb-"] = BaseballFeatureBuilder()

        # If any non-football sport is enabled, wrap builders in MultiFeatureBuilder
        if config.settings.PHASE4_NBA_ENABLED or config.settings.PHASE5_MLB_ENABLED:
            from app.kernel.multi_feature_builder import MultiFeatureBuilder
            feature_builder = MultiFeatureBuilder(builders)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py::TestEnsureMLBFactors tests/test_kernel_prediction_kernel.py::TestPhase5MLBRegistration -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py tests/test_kernel_prediction_kernel.py tests/test_multi_feature_builder.py -v`
Expected: ALL existing tests (including Phase 4 NBA registration) + 5 new Phase 5 MLB tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/kernel/factor_registry.py app/api/routes/predictions.py tests/test_kernel_factor_registry.py tests/test_kernel_prediction_kernel.py
git commit -m "feat(phase5): wire MLB components in FactorRegistry and _get_kernel

FactorRegistry.ensure_competition_factors adds 'mlb' branch seeding
5 factors (elo=0.30, home_court=0.10, rest=0.15, form=0.20,
starting_pitcher=0.25). _get_kernel registers MLBAdapter, BaseballEngine,
and BaseballFeatureBuilder when PHASE5_MLB_ENABLED=true. Refactored
builder wiring to use a single builders dict + conditional
MultiFeatureBuilder wrap so MLB/NHL can extend it cleanly."
```

---

### Task 8: NHL Stats Client

**Files:**
- Create: `backend/app/sports/hockey/__init__.py`
- Create: `backend/app/sports/hockey/nhl_stats_client.py`
- Test: `backend/tests/test_nhl_stats_client.py` (4 tests)

**Interfaces:**
- Consumes: `httpx`
- Produces:
  - `NHLStatsClientError` exception class
  - `fetch_nhl_schedule(season: str) -> list[dict]` — fetches NHL games for a season (e.g., "20232024")
  - `fetch_nhl_game_feed(game_id: int) -> dict` — fetches full game feed
  - `fetch_nhl_team_roster(team_id: int) -> dict` — fetches team roster with goalie save%

  Base URL: `https://api-web.nhle.com`. Rate limit 1 req/s. No API key required.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nhl_stats_client.py`:

```python
# backend/tests/test_nhl_stats_client.py
"""Tests for NHL Stats API client — httpx-based HTTP client."""
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.sports.hockey.nhl_stats_client import (
    fetch_nhl_schedule,
    fetch_nhl_game_feed,
    fetch_nhl_team_roster,
    NHLStatsClientError,
)


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


class TestFetchNhlSchedule:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_games_list(self, mock_rl, mock_get):
        """fetch_nhl_schedule returns the games array from the API response."""
        mock_get.return_value = _ok_response({
            "gameWeek": [
                {"date": "2024-01-15", "games": [
                    {"id": 2023020001, "gameState": "OFF FINAL"},
                    {"id": 2023020002, "gameState": "OFF FINAL"},
                ]},
            ],
        })
        games = fetch_nhl_schedule("20232024")
        assert len(games) == 2
        assert games[0]["id"] == 2023020001


class TestFetchNhlGameFeed:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_game_feed_dict(self, mock_rl, mock_get):
        """fetch_nhl_game_feed returns the full feed payload."""
        mock_get.return_value = _ok_response({
            "id": 2023020001,
            "homeTeam": {"id": 1, "name": "New Jersey Devils"},
            "awayTeam": {"id": 2, "name": "New York Rangers"},
            "scoringPlays": [],
        })
        feed = fetch_nhl_game_feed(2023020001)
        assert feed["id"] == 2023020001
        assert feed["homeTeam"]["name"] == "New Jersey Devils"


class TestFetchNhlTeamRoster:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_roster_with_goalies(self, mock_rl, mock_get):
        """fetch_nhl_team_roster returns roster containing goalies."""
        mock_get.return_value = _ok_response({
            "forwards": [],
            "defensemen": [],
            "goalies": [
                {"id": 8478401, "firstName": "Igor", "lastName": "Shesterkin",
                 "svPct": 0.912},
            ],
        })
        roster = fetch_nhl_team_roster(1)
        assert len(roster["goalies"]) == 1
        assert roster["goalies"][0]["lastName"] == "Shesterkin"
        assert roster["goalies"][0]["svPct"] == 0.912


class TestNHLStatsClientError:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_raises_on_non_200(self, mock_rl, mock_get):
        """Non-200 response raises NHLStatsClientError."""
        bad = MagicMock()
        bad.status_code = 500
        bad.text = "Internal Server Error"
        mock_get.return_value = bad
        with pytest.raises(NHLStatsClientError):
            fetch_nhl_schedule("20232024")

    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_raises_on_network_error(self, mock_rl, mock_get):
        """httpx.RequestError surfaces as NHLStatsClientError."""
        mock_get.side_effect = httpx.RequestError("DNS failure")
        with pytest.raises(NHLStatsClientError):
            fetch_nhl_game_feed(2023020001)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_nhl_stats_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.hockey'`

- [ ] **Step 3: Create hockey package init**

Create `backend/app/sports/hockey/__init__.py`:

```python
# backend/app/sports/hockey/__init__.py
"""Hockey sport module — NHL integration (Phase 5)."""
```

- [ ] **Step 4: Create nhl_stats_client.py**

Create `backend/app/sports/hockey/nhl_stats_client.py`:

```python
# backend/app/sports/hockey/nhl_stats_client.py
"""HTTP client for the official NHL Stats API.

Base URL: https://api-web.nhle.com
Authentication: None (official free API).
Rate limit: 1 req/s (polite usage, not API-enforced).

Endpoints used:
    /v1/schedule/{season}             — list games by season
    /v1/game/{id}/feed/live           — full game feed (scoring, lines)
    /v1/roster/{teamId}/current       — current team roster (goalies + sv%)
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api-web.nhle.com"
_REQUEST_INTERVAL_SECONDS = 1.0  # 1 req/s polite rate limit

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class NHLStatsClientError(Exception):
    """NHL Stats API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain >= 1s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _request(path: str, params: dict[str, Any] | None = None) -> dict:
    """Issue a GET request to the NHL Stats API.

    Returns the parsed JSON payload (dict). Raises NHLStatsClientError
    on non-200 status, timeout, or network error.
    """
    _enforce_rate_limit()
    url = f"{_BASE_URL}{path}"
    try:
        response = httpx.get(url, params=params, timeout=30.0)
    except httpx.TimeoutException as exc:
        raise NHLStatsClientError(f"Request timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise NHLStatsClientError(f"Request failed: {exc}") from exc

    if response.status_code != 200:
        raise NHLStatsClientError(
            f"NHL API error: {response.status_code} - {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise NHLStatsClientError("NHL API returned non-JSON response") from exc


def fetch_nhl_schedule(season: str) -> list[dict]:
    """Fetch NHL games for a season.

    Args:
        season: Season key in NHL format (e.g., "20232024" for 2023-24).

    Returns:
        List of raw game dicts. Each game dict contains ``id``,
        ``gameState``, ``homeTeam``, ``awayTeam``, ``gameDate``, etc.
    """
    data = _request(f"/v1/schedule/{season}")
    games: list[dict] = []
    for week in data.get("gameWeek", []):
        games.extend(week.get("games", []))
    return games


def fetch_nhl_game_feed(game_id: int) -> dict:
    """Fetch the full live feed for a single NHL game.

    Args:
        game_id: NHL game ID (e.g., 2023020001).

    Returns:
        Full game feed dict containing ``homeTeam``, ``awayTeam``,
        ``scoringPlays``, ``rosters``, etc.
    """
    return _request(f"/v1/game/{game_id}/feed/live")


def fetch_nhl_team_roster(team_id: int) -> dict:
    """Fetch the current roster for an NHL team.

    Args:
        team_id: NHL team ID (e.g., 1 for New Jersey Devils).

    Returns:
        Roster dict containing ``forwards``, ``defensemen``, and
        ``goalies`` arrays. Each goalie has ``svPct`` (save percentage).
    """
    return _request(f"/v1/roster/{team_id}/current")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_nhl_stats_client.py -v`
Expected: PASS (4 test classes, 5 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/hockey/__init__.py app/sports/hockey/nhl_stats_client.py tests/test_nhl_stats_client.py
git commit -m "feat(phase5): add NHL Stats API client

HTTP client for api-web.nhle.com. Three endpoints: schedule
(by season), game feed (full live data), team roster (goalies with
svPct). Rate limited to 1 req/s. No API key required. Raises
NHLStatsClientError on non-200 / network errors."
```

---

### Task 9: NHLAdapter

**Files:**
- Create: `backend/app/sports/hockey/nhl_adapter.py`
- Test: `backend/tests/test_nhl_adapter.py` (6 tests)

**Interfaces:**
- Consumes:
  - `config.settings.PHASE5_NHL_ENABLED`
  - `app.kernel.kernel_db` (`KernelMatchFixture`, `KernelMatchResult`, `KernelEloRating`, `get_kernel_session`)
  - `app.sports._shared.elo_calculator.seed_elo_from_games` (from Task 2)
  - `app.sports.hockey.nhl_stats_client` (`fetch_nhl_schedule`, `fetch_nhl_team_roster` from Task 8)
- Produces:
  - `NHLAdapter` class implementing DataAdapter Protocol
  - `parse_nhl_game(game_data: dict) -> dict | None`
  - `query_fixture`, `query_result`, `build_match_outcome`, `save_fixture` (mirror MLB pattern)

  Match ID format: `nhl-{gameId}` (e.g., `nhl-2023020001`)
  Stage mapping: regular season → `"regular_season"`, playoffs → `"playoff"`
  Status mapping: `"OFF FINAL"` / `"FINAL"` → `"finished"`, else → `"scheduled"`
  Overtime/shootout flags stored in `raw["custom"]["went_to_overtime"]` / `["went_to_shootout"]` (Constraint 22)
  `MatchOutcome.outcome` always binary (`home_win`/`away_win`)

  **Important:** Tests must mock DB operations to avoid creating real DB files.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nhl_adapter.py`:

```python
# backend/tests/test_nhl_adapter.py
"""Tests for NHLAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.hockey.nhl_adapter import NHLAdapter, parse_nhl_game


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_match(match_id="nhl-2023020001") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_NHL, season_key="20232024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NJD", name="New Jersey Devils", competition=_NHL),
        away=TeamIdentity(code="NYR", name="New York Rangers", competition=_NHL),
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="nhl-2023020001", home="New Jersey Devils", away="New York Rangers"):
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "nhl"
    fixture.season = "20232024"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 1, 15, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "Prudential Center"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestNHLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = NHLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseNhlGame:
    def test_parses_regular_season_final_game(self):
        """parse_nhl_game maps API fields to internal fixture format."""
        raw = {
            "id": 2023020001,
            "season": 20232024,
            "gameDate": "2024-01-15T00:00:00Z",
            "homeTeam": {"id": 1, "name": "New Jersey Devils", "abbrev": "NJD"},
            "awayTeam": {"id": 2, "name": "New York Rangers", "abbrev": "NYR"},
            "gameState": "OFF FINAL",
            "homeTeamScore": 3,
            "awayTeamScore": 2,
            "gameType": 2,  # 2 = regular season
        }
        parsed = parse_nhl_game(raw)
        assert parsed["match_id"] == "nhl-2023020001"
        assert parsed["home_team"] == "New Jersey Devils"
        assert parsed["away_team"] == "New York Rangers"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"

    def test_parses_playoff_game_with_overtime(self):
        """Playoff game maps to 'playoff'; overtime/shootout flags captured."""
        raw = {
            "id": 2023030111,
            "season": 20232024,
            "gameDate": "2024-04-20T00:00:00Z",
            "homeTeam": {"id": 1, "name": "New Jersey Devils", "abbrev": "NJD"},
            "awayTeam": {"id": 2, "name": "New York Rangers", "abbrev": "NYR"},
            "gameState": "OFF FINAL",
            "homeTeamScore": 4,
            "awayTeamScore": 3,
            "gameType": 3,  # 3 = playoffs
            "period": 5,  # OT
        }
        parsed = parse_nhl_game(raw)
        assert parsed["match_id"] == "nhl-2023030111"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "finished"
        assert parsed["went_to_overtime"] is True
        assert parsed["went_to_shootout"] is False


class TestNHLAdapterGetMatchIdentity:
    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = NHLAdapter()
        identity = adapter.get_match_identity("nhl-2023020001")
        assert identity.match_id == "nhl-2023020001"
        assert identity.home.name == "New Jersey Devils"
        assert identity.away.name == "New York Rangers"
        assert identity.season.competition.code == "nhl"

    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = NHLAdapter()
        identity = adapter.get_match_identity("nhl-nonexistent")
        assert identity.match_id == "nhl-nonexistent"
        assert identity.home.name == "Home"


class TestNHLAdapterFetchAllData:
    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_fetch_all_data_includes_goalie_save_pct(self, mock_query):
        """fetch_all_data writes goalie save% into raw['custom']."""
        mock_query.return_value = _make_fixture()

        adapter = NHLAdapter()
        with patch.object(adapter, "_fetch_elo_ratings",
                          return_value={"New Jersey Devils": 1510.0, "New York Rangers": 1495.0}), \
             patch.object(adapter, "_fetch_starting_goalies",
                          return_value={
                              "home": {"name": "Igor Shesterkin", "save_pct": 0.912},
                              "away": {"name": "Juuse Saros", "save_pct": 0.920},
                          }):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1510.0
            assert raw["team"]["elo_away"] == 1495.0
            assert raw["environment"]["is_home_advantage"] is True
            # Goalie stats in custom dict
            assert raw["custom"]["goalie_save_pct_home"] == 0.912
            assert raw["custom"]["goalie_save_pct_away"] == 0.920
            # Overtime defaults (False for fresh game)
            assert raw["custom"]["went_to_overtime"] is False
            assert raw["custom"]["went_to_shootout"] is False


class TestNHLAdapterFetchOutcome:
    @patch("app.sports.hockey.nhl_adapter.build_match_outcome")
    @patch("app.sports.hockey.nhl_adapter.query_result")
    def test_fetch_outcome_returns_binary_outcome(self, mock_query, mock_build):
        """fetch_outcome returns binary outcome even for OT/shootout games."""
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="nhl-2023020001",
            home_score=3, away_score=2,
            outcome="home_win",  # binary — no "overtime_win"
            finished_at=datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc),
        )
        adapter = NHLAdapter()
        result = adapter.fetch_outcome("nhl-2023020001")
        assert result is not None
        assert result.home_score == 3
        assert result.outcome == "home_win"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_nhl_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.hockey.nhl_adapter'`

- [ ] **Step 3: Create nhl_adapter.py**

Create `backend/app/sports/hockey/nhl_adapter.py`:

```python
# backend/app/sports/hockey/nhl_adapter.py
"""NHLAdapter — DataAdapter Protocol implementation for NHL hockey.

Bridges the NHL Stats API to the sport-agnostic DataAdapter Protocol.

Match ID format: nhl-{gameId}
Stage mapping: gameType=3 (playoffs) → "playoff", else → "regular_season"
Status mapping: gameState in {"OFF FINAL", "FINAL"} → "finished", else → "scheduled"

Overtime/shootout design (Constraint 22):
    - MatchOutcome.outcome is always binary ("home_win"/"away_win")
    - Overtime/shootout info stored in raw["custom"]["went_to_overtime"]
      and raw["custom"]["went_to_shootout"] for future analysis
    - Does NOT modify domain.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core import config
from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.kernel.kernel_db import (
    get_kernel_session, KernelMatchFixture, KernelMatchResult, KernelEloRating,
)
from app.sports.hockey.nhl_stats_client import (
    fetch_nhl_schedule, fetch_nhl_team_roster, NHLStatsClientError,
)
from app.sports._shared.elo_calculator import seed_elo_from_games

logger = logging.getLogger(__name__)

_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)
_DEFAULT_SEASON = "20232024"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2024, 1, 15, tzinfo=timezone.utc)


def parse_nhl_game(game_data: dict) -> dict | None:
    """Parse a raw NHL Stats API game dict into internal fixture format.

    Returns None if game_data is malformed. Captures overtime/shootout
    flags in the parsed dict (returned under ``went_to_overtime`` /
    ``went_to_shootout`` keys; the adapter writes them into raw['custom']).
    """
    game_id = game_data.get("id")
    if not game_id:
        return None

    home_team = game_data.get("homeTeam", {}).get("name", "")
    away_team = game_data.get("awayTeam", {}).get("name", "")
    if not home_team or not away_team:
        return None

    date_str = game_data.get("gameDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    # gameType: 2 = regular season, 3 = playoffs
    game_type = game_data.get("gameType", 2)
    stage = "playoff" if game_type == 3 else "regular_season"

    # gameState: "OFF FINAL", "FINAL", "LIVE", "FUT", etc.
    game_state = game_data.get("gameState", "")
    status = "finished" if game_state in ("OFF FINAL", "FINAL") else "scheduled"

    home_score = game_data.get("homeTeamScore")
    away_score = game_data.get("awayTeamScore")

    # Overtime/shootout detection: period > 3 means OT (4) or shootout (5)
    period = game_data.get("period", 3)
    went_to_overtime = period == 4
    went_to_shootout = period == 5

    venue = game_data.get("venue", {}).get("default", "Unknown")

    return {
        "match_id": f"nhl-{game_id}",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "stage": stage,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "venue": venue,
        "went_to_overtime": went_to_overtime,
        "went_to_shootout": went_to_shootout,
    }


def query_fixture(match_id: str, model_cls) -> object | None:
    """Query a fixture by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query fixture %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def query_result(match_id: str, model_cls) -> object | None:
    """Query a match result by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query result %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def build_match_outcome(result: object) -> MatchOutcome | None:
    """Build MatchOutcome from a KernelMatchResult row. Binary outcome only.

    NHL overtime/shootout games still produce binary home_win/away_win;
    the OT/SO info is preserved separately in FeatureSet.custom.
    """
    if result is None:
        return None
    home_score = result.home_score or 0
    away_score = result.away_score or 0
    if home_score > away_score:
        outcome = "home_win"
    else:
        outcome = "away_win"
    return MatchOutcome(
        match_id=result.match_id,
        home_score=home_score,
        away_score=away_score,
        outcome=outcome,
        finished_at=result.finished_at or datetime.now(timezone.utc),
    )


def save_fixture(parsed: dict, competition: str, season: str) -> None:
    """Upsert a parsed NHL fixture into kernel_match_fixtures."""
    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        existing = session.get(KernelMatchFixture, parsed["match_id"])
        if existing:
            existing.home_team = parsed["home_team"]
            existing.away_team = parsed["away_team"]
            existing.kickoff_utc = parsed["kickoff_utc"]
            existing.stage = parsed["stage"]
            existing.status = parsed["status"]
            if parsed.get("home_score") is not None:
                existing.home_score = parsed["home_score"]
            if parsed.get("away_score") is not None:
                existing.away_score = parsed["away_score"]
            existing.updated_at = now
        else:
            fixture = KernelMatchFixture(
                match_id=parsed["match_id"],
                competition=competition,
                season=season,
                home_team=parsed["home_team"],
                away_team=parsed["away_team"],
                kickoff_utc=parsed["kickoff_utc"],
                stage=parsed["stage"],
                status=parsed["status"],
                home_score=parsed.get("home_score"),
                away_score=parsed.get("away_score"),
                venue=parsed.get("venue", "Unknown"),
                created_at=now,
                updated_at=now,
            )
            session.add(fixture)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to save fixture %s: %s", parsed.get("match_id"), exc)
    finally:
        session.close()


class NHLAdapter:
    """DataAdapter Protocol implementation for NHL hockey."""

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=_NHL)
        away = TeamIdentity(code="AWAY", name="Away", competition=_NHL)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_NHL, season_key=_DEFAULT_SEASON),
            stage=_DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=_DEFAULT_KICKOFF,
        )

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id, KernelMatchFixture)
        if fixture is None:
            return self._stub_identity(match_id)
        home = TeamIdentity(
            code=(fixture.home_team or "HOME")[:3].upper(),
            name=fixture.home_team or "Home",
            competition=_NHL,
        )
        away = TeamIdentity(
            code=(fixture.away_team or "AWAY")[:3].upper(),
            name=fixture.away_team or "Away",
            competition=_NHL,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(competition=_NHL, season_key=fixture.season or _DEFAULT_SEASON),
            stage=fixture.stage or _DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=fixture.kickoff_utc or _DEFAULT_KICKOFF,
        )

    def _fetch_elo_ratings(self, home_team: str, away_team: str) -> dict[str, float]:
        """Fetch Elo ratings for both teams from kernel_elo_ratings table."""
        session = get_kernel_session()
        try:
            ratings: dict[str, float] = {}
            for team_name in [home_team, away_team]:
                row = session.get(KernelEloRating, team_name)
                if row is not None and row.competition == "nhl":
                    ratings[team_name] = row.elo_rating
            return ratings
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch NHL Elo ratings: %s", exc)
            return {}
        finally:
            session.close()

    def _fetch_starting_goalies(self, match: MatchIdentity) -> dict:
        """Fetch starting goalie save% for both teams.

        Returns dict with 'home' and 'away' keys, each containing
        {'name': str, 'save_pct': float} or empty dict if unavailable.
        Stubbed in tests; in production this would call
        fetch_nhl_team_roster() for both teams.
        """
        return {"home": {}, "away": {}}

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an NHL match.

        All data comes from local DB (Elo, form, rest) and the NHL Stats
        API (goalie save%). Goalie stats and overtime flags are written
        to raw['custom'].
        """
        home_name = match.home.name
        away_name = match.away.name

        elo_ratings = self._fetch_elo_ratings(home_name, away_name)
        elo_home = elo_ratings.get(home_name)
        elo_away = elo_ratings.get(away_name)

        form_home = self._compute_form(home_name)
        form_away = self._compute_form(away_name)

        rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
        rest_away = self._compute_rest_days(away_name, match.kickoff_utc)

        goalies = self._fetch_starting_goalies(match)
        home_g = goalies.get("home", {})
        away_g = goalies.get("away", {})

        raw: dict = {
            "team": {
                "elo_home": elo_home,
                "elo_away": elo_away,
                "form_home": form_home,
                "form_away": form_away,
            },
            "general": {
                "rest_days_home": rest_home,
                "rest_days_away": rest_away,
                "days_since_last_match": rest_home,
            },
            "market": {},  # No odds source
            "player": {
                "starting_goalie_home": home_g.get("name"),
                "starting_goalie_away": away_g.get("name"),
            },
            "environment": {
                "venue": "Home Arena",
                "is_home_advantage": True,
            },
            "custom": {
                "goalie_save_pct_home": home_g.get("save_pct", 0.910),
                "goalie_save_pct_away": away_g.get("save_pct", 0.910),
                "team_gf_home": 3.20, "team_gf_away": 3.00,
                "team_ga_home": 2.90, "team_ga_away": 3.10,
                "corsi_pct_home": None, "corsi_pct_away": None,
                "pdo_home": None, "pdo_away": None,
                "went_to_overtime": False,
                "went_to_shootout": False,
            },
        }
        return raw

    def _compute_form(self, team_name: str) -> float:
        """Compute last-10 win rate from kernel_match_results. Returns 0.5 if none."""
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "nhl",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                    KernelMatchFixture.status == "finished",
                )
                .order_by(KernelMatchFixture.kickoff_utc.desc())
                .limit(10)
            )
            fixtures = session.execute(query).scalars().all()
            if not fixtures:
                return 0.5
            wins = 0
            for f in fixtures:
                if f.home_team == team_name:
                    if (f.home_score or 0) > (f.away_score or 0):
                        wins += 1
                else:
                    if (f.away_score or 0) > (f.home_score or 0):
                        wins += 1
            return wins / len(fixtures)
        except Exception:  # noqa: BLE001
            return 0.5
        finally:
            session.close()

    def _compute_rest_days(self, team_name: str, kickoff_utc: datetime) -> int:
        """Compute days since last match. Returns 0 if unknown."""
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture.kickoff_utc)
                .where(
                    KernelMatchFixture.competition == "nhl",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                    KernelMatchFixture.kickoff_utc < kickoff_utc,
                )
                .order_by(KernelMatchFixture.kickoff_utc.desc())
                .limit(1)
            )
            result = session.execute(query).scalar_one_or_none()
            if result is None:
                return 0
            delta = kickoff_utc - result
            return max(0, delta.days)
        except Exception:  # noqa: BLE001
            return 0
        finally:
            session.close()

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        """Sync NHL schedule from the NHL Stats API.

        Returns 0 if PHASE5_NHL_ENABLED is false or sync fails (graceful
        degradation, no exceptions). NHL season spans two calendar years
        (e.g., "20232024" for the 2023-24 season).
        """
        if not config.settings.PHASE5_NHL_ENABLED:
            return 0
        try:
            now = datetime.now(timezone.utc)
            # NHL season key: if month >= August, season starts this year
            if now.month >= 8:
                season = f"{now.year}{now.year + 1}"
            else:
                season = f"{now.year - 1}{now.year}"
            games_raw = fetch_nhl_schedule(season)
            count = 0
            for raw in games_raw:
                parsed = parse_nhl_game(raw)
                if parsed:
                    save_fixture(parsed, "nhl", season)
                    count += 1
            return count
        except NHLStatsClientError as exc:
            logger.error("NHL API error during sync_schedule: %s", exc)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync NHL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "nhl"
            )
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)
            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(
                    match=self.get_match_identity(f.match_id), raw_json={}
                )
                for f in fixtures
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch NHL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_nhl_adapter.py -v`
Expected: PASS (5 test classes, 6 test methods)

- [ ] **Step 5: Verify no regression**

Run: `cd backend && python -m pytest tests/test_nhl_stats_client.py tests/test_mlb_adapter.py tests/test_nhl_adapter.py -v`
Expected: ALL tests pass (NHL stats client + MLB adapter + NHL adapter).

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/hockey/nhl_adapter.py tests/test_nhl_adapter.py
git commit -m "feat(phase5): add NHLAdapter with nhl- prefix

Implements DataAdapter Protocol for NHL hockey. Parses NHL Stats API
game data (id, homeTeam, awayTeam, gameState, gameType, period) into
internal fixture format. Overtime/shootout flags captured in
raw['custom'] (Constraint 22) — MatchOutcome.outcome stays binary.
fetch_all_data reads Elo from kernel_elo_ratings and writes goalie
save% to raw['custom']. sync_schedule returns 0 on API error
(graceful degradation). Tests mock DB to avoid real DB files."
```

---

### Task 10: HockeyFeatureBuilder

**Files:**
- Create: `backend/app/sports/hockey/feature_builder.py`
- Test: `backend/tests/test_hockey_feature_builder.py` (5 tests)

**Interfaces:**
- Consumes: `app.kernel.domain` value objects (`SportIdentity`, `FeatureSet`, etc.)
- Produces: `HockeyFeatureBuilder` class implementing FeatureBuilder Protocol
  - `sport() -> SportIdentity` returns `SportIdentity(code="hockey", name="Hockey")`
  - `build(match: MatchIdentity, raw: dict) -> FeatureSet` with `feature_version = "nhl-1.0"`
  - Data quality: `"real"` if `raw["team"]["elo_home"] is not None`, `"partial"` otherwise
  - Custom dict: `goalie_save_pct_*`, `team_gf_*`, `team_ga_*`, `corsi_pct_*`, `pdo_*`, `went_to_overtime`, `went_to_shootout`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_hockey_feature_builder.py`:

```python
# backend/tests/test_hockey_feature_builder.py
"""Tests for HockeyFeatureBuilder — FeatureBuilder Protocol."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.hockey.feature_builder import HockeyFeatureBuilder


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="nhl-2023020001",
        season=SeasonIdentity(competition=_NHL, season_key="20232024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NJD", name="New Jersey Devils", competition=_NHL),
        away=TeamIdentity(code="NYR", name="New York Rangers", competition=_NHL),
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


def _make_raw_with_elo():
    return {
        "team": {"elo_home": 1510.0, "elo_away": 1495.0, "form_home": 0.6, "form_away": 0.45},
        "general": {"rest_days_home": 2, "rest_days_away": 1, "days_since_last_match": 2},
        "market": {},
        "player": {"starting_goalie_home": "Igor Shesterkin", "starting_goalie_away": "Juuse Saros"},
        "environment": {"venue": "Prudential Center", "is_home_advantage": True},
        "custom": {
            "goalie_save_pct_home": 0.912, "goalie_save_pct_away": 0.920,
            "team_gf_home": 3.20, "team_gf_away": 3.00,
            "team_ga_home": 2.90, "team_ga_away": 3.10,
            "corsi_pct_home": 52.0, "corsi_pct_away": 48.0,
            "pdo_home": 101.5, "pdo_away": 98.5,
            "went_to_overtime": False, "went_to_shootout": False,
        },
    }


class TestHockeyFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        builder = HockeyFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_hockey(self):
        builder = HockeyFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "hockey"
        assert sport.name == "Hockey"


class TestHockeyFeatureBuilderBuild:
    def test_full_feature_mapping(self):
        """All layers are mapped correctly from raw dict."""
        builder = HockeyFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())

        # General layer
        assert features.general.rest_days_home == 2
        assert features.general.rest_days_away == 1

        # Team layer
        assert features.team.elo_rating_home == 1510.0
        assert features.team.elo_rating_away == 1495.0
        assert features.team.form_home == 0.6
        assert features.team.form_away == 0.45
        assert features.team.h2h_draw_rate is None  # Hockey has no draws
        assert features.team.market_value_home is None

        # Market layer — all None (no odds source)
        assert features.market.odds_home is None
        assert features.market.odds_away is None

        # Environment layer
        assert features.environment.venue == "Prudential Center"
        assert features.environment.is_home_advantage is True
        assert features.environment.weather_temp_c is None

        # Custom layer — hockey-specific features
        assert features.custom["goalie_save_pct_home"] == 0.912
        assert features.custom["goalie_save_pct_away"] == 0.920
        assert features.custom["team_gf_home"] == 3.20
        assert features.custom["team_ga_away"] == 3.10
        assert features.custom["corsi_pct_home"] == 52.0
        assert features.custom["pdo_home"] == 101.5
        # Overtime/shootout flags preserved (Constraint 22)
        assert features.custom["went_to_overtime"] is False
        assert features.custom["went_to_shootout"] is False

        # Feature version
        assert features.feature_version == "nhl-1.0"

    def test_data_quality_real_when_elo_present(self):
        """Data quality is 'real' when Elo exists, even without odds."""
        builder = HockeyFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.data_quality == "real"
        assert "betting_odds_unavailable" not in features.quality_notes

    def test_data_quality_partial_when_elo_missing(self):
        """Data quality is 'partial' when Elo is None."""
        builder = HockeyFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["team"]["elo_home"] = None
        raw["team"]["elo_away"] = None
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_hockey_feature_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.hockey.feature_builder'`

- [ ] **Step 3: Create feature_builder.py**

Create `backend/app/sports/hockey/feature_builder.py`:

```python
# backend/app/sports/hockey/feature_builder.py
"""HockeyFeatureBuilder — computes FeatureSet from raw NHL data.

Maps raw dict from NHLAdapter to standardized FeatureSet. Same pattern
as BaseballFeatureBuilder / BasketballFeatureBuilder: odds absence does
NOT downgrade data quality because there is no odds source by design,
and HockeyEngine does not use odds.

Feature version: "nhl-1.0" (distinct from football's "1.0",
basketball's "nba-1.0", and baseball's "mlb-1.0").

Overtime/shootout flags (Constraint 22) are carried through unchanged
from raw["custom"] into FeatureSet.custom — they do NOT affect
MatchOutcome.outcome (which stays binary home_win/away_win).
"""
from __future__ import annotations

import logging

from app.kernel.domain import (
    SportIdentity,
    MatchIdentity,
    FeatureSet,
    GeneralFeatures,
    TeamFeatures,
    MarketFeatures,
    PlayerFeatures,
    EnvironmentFeatures,
)

logger = logging.getLogger(__name__)

_HOCKEY = SportIdentity(code="hockey", name="Hockey")


class HockeyFeatureBuilder:
    """Builds FeatureSet for NHL hockey matches.

    Implements the FeatureBuilder Protocol. Consumes a raw dict with
    keys ``team``, ``market``, ``player``, ``environment``, ``general``,
    and ``custom`` and produces a FeatureSet.
    """

    def sport(self) -> SportIdentity:
        return _HOCKEY

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Data quality: "real" if Elo exists, "partial" otherwise.
        # Odds absence does NOT downgrade quality (no odds source for NHL).
        has_elo = team_raw.get("elo_home") is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = []

        # Goalie availability flag for player layer
        goalie_home = player_raw.get("starting_goalie_home")
        goalie_away = player_raw.get("starting_goalie_away")
        goalie_home_available = goalie_home is not None
        goalie_away_available = goalie_away is not None

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=None,  # Not tracked for hockey
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for hockey
                h2h_draw_rate=None,  # Hockey has no draws (binary outcome)
                market_value_home=None,
                market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=None,  # No odds source
                odds_draw=None,
                odds_away=None,
                odds_source=None,
                odds_fresh=False,
            ),
            player=PlayerFeatures(
                key_players_available_home=goalie_home_available,
                key_players_available_away=goalie_away_available,
                injury_impact_home=None,
                injury_impact_away=None,
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,  # Indoor sport — not applicable
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="nhl-1.0",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_hockey_feature_builder.py -v`
Expected: PASS (2 test classes, 5 test methods)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sports/hockey/feature_builder.py tests/test_hockey_feature_builder.py
git commit -m "feat(phase5): add HockeyFeatureBuilder

Maps NHL raw dict to FeatureSet with feature_version='nhl-1.0'.
Data quality is 'real' when Elo exists (odds absence does NOT
downgrade). Custom dict holds goalie_save_pct, team_gf/ga,
corsi_pct, pdo, and went_to_overtime/went_to_shootout flags
(Constraint 22 — binary outcome preserved)."
```

---

### Task 11: HockeyEngine

**Files:**
- Create: `backend/app/sports/hockey/engines/__init__.py`
- Create: `backend/app/sports/hockey/engines/hockey_engine.py`
- Test: `backend/tests/test_hockey_engine.py` (7 tests)

**Interfaces:**
- Consumes:
  - `app.kernel.domain` (`FeatureSet`, `MatchIdentity`, `PredictionResult`, `ContributionItem`)
  - `app.kernel.factor_registry.FactorRegistry` (optional)
  - `app.core.config.settings` (`NHL_ELO_HFA`, `NHL_LEAGUE_AVG_TOTAL` — read at call time, from Task 1)
  - `app.sports._shared.elo_calculator.compute_expected_score` (from Task 2)
- Produces: `HockeyEngine` class implementing PredictionEngine Protocol
  - `name() -> str` returns `"hockey"`
  - `supported_sports() -> list[str]` returns `["hockey"]`
  - `predict(features: FeatureSet, match: MatchIdentity) -> PredictionResult`
  - 5 factors: `elo` (0.35), `home_court` (0.15), `rest` (0.15), `form` (0.20), `goalie` (0.15)
  - Bradley-Terry binary model: `outcome_probabilities = {"home_win": p, "away_win": 1-p}`
  - home_court constant: `0.55` (NHL historical home win rate)
  - goalie: `p = 0.5 + clamp(sv_pct_diff, -0.1, 0.1) * 2.0` where `sv_pct_diff = sv_pct_home - sv_pct_away`
  - Weight redistribution for unavailable factors (same pattern as BaseballEngine)
  - Score conversion: `margin = (elo_home - elo_away + hfa) * 0.03`, scores centered on `NHL_LEAGUE_AVG_TOTAL/2`
  - Confidence: `min(max(p_home, p_away) * 0.95, 0.95)`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_hockey_engine.py`:

```python
# backend/tests/test_hockey_engine.py
"""Tests for HockeyEngine — 5-factor Bradley-Terry binary prediction engine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.hockey.engines.hockey_engine import HockeyEngine


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_features(
    elo_home=1510.0, elo_away=1495.0,
    form_home=0.6, form_away=0.45,
    rest_home=2, rest_away=1,
    sv_pct_home=0.912, sv_pct_away=0.920,
) -> FeatureSet:
    comp = _NHL
    season = SeasonIdentity(competition=comp, season_key="20232024")
    home = TeamIdentity(code="NJD", name="New Jersey Devils", competition=comp)
    away = TeamIdentity(code="NYR", name="New York Rangers", competition=comp)
    match = MatchIdentity(
        match_id="nhl-2023020001", season=season,
        stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(
            rest_days_home=float(rest_home) if rest_home is not None else None,
            rest_days_away=float(rest_away) if rest_away is not None else None,
            travel_distance_km=None,
            days_since_last_match=None,
        ),
        team=TeamFeatures(
            elo_rating_home=elo_home,
            elo_rating_away=elo_away,
            form_home=form_home,
            form_away=form_away,
            h2h_home_win_rate=None, h2h_draw_rate=None,
            market_value_home=None, market_value_away=None,
        ),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures("Prudential Center", None, None, True),
        custom={
            "goalie_save_pct_home": sv_pct_home, "goalie_save_pct_away": sv_pct_away,
            "team_gf_home": 3.20, "team_gf_away": 3.00,
            "team_ga_home": 2.90, "team_ga_away": 3.10,
            "corsi_pct_home": 52.0, "corsi_pct_away": 48.0,
            "pdo_home": 101.5, "pdo_away": 98.5,
            "went_to_overtime": False, "went_to_shootout": False,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="nhl-1.0",
    )


class TestHockeyEngineProtocol:
    def test_implements_protocol(self):
        engine = HockeyEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name_and_supported_sports(self):
        engine = HockeyEngine()
        assert engine.name() == "hockey"
        assert "hockey" in engine.supported_sports()


class TestHockeyEnginePredict:
    def test_predict_returns_binary_probabilities(self):
        """Outcome probabilities have home_win and away_win (no draw)."""
        engine = HockeyEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        """Higher Elo home team → P(home_win) > P(away_win)."""
        engine = HockeyEngine()
        strong = _make_features(elo_home=1700, elo_away=1400)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_explanation_has_five_factors(self):
        """Explanation contains all 5 factors: elo, home_court, rest, form, goalie."""
        engine = HockeyEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        factor_ids = [e.factor for e in result.explanation]
        assert "elo" in factor_ids
        assert "home_court" in factor_ids
        assert "rest" in factor_ids
        assert "form" in factor_ids
        assert "goalie" in factor_ids

    def test_contribution_item_predicted_outcome_is_binary(self):
        """Each ContributionItem.predicted_outcome is home_win or away_win."""
        engine = HockeyEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        for item in result.explanation:
            assert item.predicted_outcome in ("home_win", "away_win", None)

    def test_better_home_goalie_increases_home_win_prob(self):
        """Higher home save% (better goalie) → higher P(home_win)."""
        engine = HockeyEngine()
        # Home goalie much better
        better_home = _make_features(sv_pct_home=0.930, sv_pct_away=0.890)
        # Equal goalies
        equal = _make_features(sv_pct_home=0.910, sv_pct_away=0.910)
        p_better = engine.predict(better_home, better_home.match).outcome_probabilities["home_win"]
        p_equal = engine.predict(equal, equal.match).outcome_probabilities["home_win"]
        assert p_better > p_equal

    def test_no_elo_fallback(self):
        """When Elo is None, engine still produces valid prediction via weight redistribution."""
        engine = HockeyEngine()
        features = _make_features(elo_home=None, elo_away=None)
        result = engine.predict(features, features.match)
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.available is False
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01
        # Other factors still contribute
        assert result.outcome_probabilities["home_win"] != 0.5 or \
               result.outcome_probabilities["away_win"] != 0.5

    def test_score_conversion_uses_league_avg(self):
        """Predicted scores are centered around NHL league avg total (5.5)."""
        engine = HockeyEngine()
        features = _make_features(elo_home=1500, elo_away=1500)
        result = engine.predict(features, features.match)
        home_score = result.predicted_scores["home"]
        away_score = result.predicted_scores["away"]
        # League avg = 5.5, so each ~2.75 (plus HFA adjustment)
        assert 1.5 < home_score < 4.5
        assert 1.5 < away_score < 4.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_hockey_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.hockey.engines'`

- [ ] **Step 3: Create engines package init**

Create `backend/app/sports/hockey/engines/__init__.py`:

```python
# backend/app/sports/hockey/engines/__init__.py
"""Hockey prediction engines."""
```

- [ ] **Step 4: Create hockey_engine.py**

Create `backend/app/sports/hockey/engines/hockey_engine.py`:

```python
# backend/app/sports/hockey/engines/hockey_engine.py
"""HockeyEngine — 5-factor Bradley-Terry binary prediction engine.

5 factors that each compute P(home_win), then weighted-average fusion.
NHL has binary outcomes (home_win/away_win, no draws). Overtime and
shootout games still resolve to a binary winner; the OT/SO info is
preserved separately in FeatureSet.custom (Constraint 22).

Factors:
    elo (0.35)        — Elo-based win probability with HFA=55
    home_court (0.15) — NHL historical home win rate (constant 0.55)
    rest (0.15)       — Rest days differential
    form (0.20)       — Recent form (last-10 win rate)
    goalie (0.15)     — Starting goalie save% differential

Goalie formula:
    sv_pct_diff = sv_pct_home - sv_pct_away
    p = 0.5 + clamp(sv_pct_diff, -0.1, 0.1) * 2.0
    (Higher home save% → p > 0.5)

Weights are read from FactorRegistry at call time, falling back to
defaults if FactorRegistry is None. When a factor is unavailable, its
weight is redistributed proportionally to available factors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core import config
from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.sports._shared.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS = {
    "elo": 0.35,
    "home_court": 0.15,
    "rest": 0.15,
    "form": 0.20,
    "goalie": 0.15,
}

# NHL historical home win rate (constant — slightly higher than MLB's 0.54)
_HOME_COURT_PROB = 0.55


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class HockeyEngine:
    """5-factor Bradley-Terry binary outcome engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "hockey"

    def supported_sports(self) -> list[str]:
        return ["hockey"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        hfa = config.settings.NHL_ELO_HFA
        league_avg = config.settings.NHL_LEAGUE_AVG_TOTAL

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            weights = {
                fid: self._factor_registry.get_weight(fid, competition)
                for fid in _DEFAULT_WEIGHTS
            }
        else:
            weights = dict(_DEFAULT_WEIGHTS)

        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo factor
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        if elo_home is not None and elo_away is not None:
            p_elo = compute_expected_score(elo_home, elo_away, hfa)
            elo_available = True
        else:
            p_elo = 0.5
            elo_available = False
        factors.append(("elo", p_elo, weights["elo"], elo_available))

        # 2. Home court factor (constant — NHL home advantage)
        p_home_court = _HOME_COURT_PROB
        factors.append(("home_court", p_home_court, weights["home_court"], True))

        # 3. Rest factor
        rest_home = features.general.rest_days_home
        rest_away = features.general.rest_days_away
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            p_rest = 0.5 + rest_diff * 0.03
            rest_available = True
        else:
            p_rest = 0.5
            rest_available = False
        factors.append(("rest", p_rest, weights["rest"], rest_available))

        # 4. Form factor
        form_home = features.team.form_home
        form_away = features.team.form_away
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            p_form = 0.5 + form_diff * 0.5
            form_available = True
        else:
            p_form = 0.5
            form_available = False
        factors.append(("form", p_form, weights["form"], form_available))

        # 5. Goalie factor
        # sv_pct_diff = sv_pct_home - sv_pct_away; home goalie better → diff > 0 → p > 0.5
        sv_pct_home = features.custom.get("goalie_save_pct_home")
        sv_pct_away = features.custom.get("goalie_save_pct_away")
        if sv_pct_home is not None and sv_pct_away is not None:
            sv_pct_diff = _clamp(sv_pct_home - sv_pct_away, -0.1, 0.1)
            p_goalie = 0.5 + sv_pct_diff * 2.0
            goalie_available = True
        else:
            p_goalie = 0.5
            goalie_available = False
        factors.append(("goalie", p_goalie, weights["goalie"], goalie_available))

        # Weighted fusion — redistribute unavailable factor weights
        available_factors = [(f, p, w) for f, p, w, a in factors if a]
        total_w = sum(w for _, _, w in available_factors)
        if total_w > 0:
            p_home = sum(p * (w / total_w) for _, p, w in available_factors)
        else:
            p_home = 0.5  # All factors unavailable → neutral
        p_away = 1.0 - p_home

        outcome_probabilities = {
            "home_win": round(p_home, 4),
            "away_win": round(p_away, 4),
        }

        # Score conversion (NHL: league_avg=5.5, low-scoring)
        if elo_home is not None and elo_away is not None:
            margin = (elo_home - elo_away + hfa) * 0.03
        else:
            margin = 0.0
        home_score = league_avg / 2 + margin / 2
        away_score = league_avg / 2 - margin / 2
        predicted_scores = {
            "home": round(home_score, 1),
            "away": round(away_score, 1),
        }

        # Confidence (same formula as BaseballEngine / BasketballEngine)
        confidence = round(min(max(p_home, p_away) * 0.95, 0.95), 4)

        # Build explanation with ContributionItems
        explanation: list[ContributionItem] = []
        for fid, p, w, available in factors:
            predicted_outcome = "home_win" if p >= 0.5 else "away_win"
            explanation.append(ContributionItem(
                factor=fid,
                direction="support" if available else "neutral",
                weight=w,
                available=available,
                detail=f"P(home_win)={round(p, 4)}" if available else f"{fid} unavailable",
                predicted_outcome=predicted_outcome if available else None,
            ))

        return PredictionResult(
            predicted_scores=predicted_scores,
            outcome_probabilities=outcome_probabilities,
            confidence=confidence,
            engine_name="hockey",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_hockey_engine.py -v`
Expected: PASS (2 test classes, 7 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/hockey/engines/__init__.py app/sports/hockey/engines/hockey_engine.py tests/test_hockey_engine.py
git commit -m "feat(phase5): add HockeyEngine with 5-factor Bradley-Terry model

5 factors: elo(0.35), home_court(0.15), rest(0.15), form(0.20),
goalie(0.15). Binary outcomes (home_win/away_win, no draw). Goalie
factor uses save% differential (sv_pct_home - sv_pct_away). Home court
constant 0.55. Weight redistribution for unavailable factors. Reads
HFA and league avg from config.settings. Overtime/shootout info
preserved in FeatureSet.custom (Constraint 22) — does not affect
binary outcome."
```

---

### Task 12: NHL API Integration + FactorRegistry

**Files:**
- Modify: `backend/app/kernel/factor_registry.py` (add `"nhl"` branch to `ensure_competition_factors`)
- Modify: `backend/app/api/routes/predictions.py` (add NHL registration block in `_get_kernel`)
- Test: `backend/tests/test_kernel_factor_registry.py` (append `TestEnsureNHLFactors`, 4 tests)
- Test: `backend/tests/test_kernel_prediction_kernel.py` (append `TestPhase5NHLRegistration`, 1 test)

**Interfaces:**
- Consumes: All NHL components from Tasks 8-11 (NHLAdapter, HockeyFeatureBuilder, HockeyEngine)
- Produces:
  - Modified `FactorRegistry.ensure_competition_factors("nhl")` seeds 5 NHL factors
  - Modified `_get_kernel()` registers NHL components when `PHASE5_NHL_ENABLED` is true

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kernel_factor_registry.py` (after `TestEnsureMLBFactors`):

```python
class TestEnsureNHLFactors:
    """Phase 5: ensure_competition_factors for NHL factor seeding."""

    def test_seeds_nhl_factors_when_empty(self):
        """NHL factors are seeded when none exist for 'nhl' competition."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        # Before: no NHL-specific factors; falls back to global elo=0.30
        assert reg.get_weight("elo", "nhl") == 0.30

        reg.ensure_competition_factors("nhl")

        # After: NHL factors seeded with correct weights
        assert reg.get_weight("elo", "nhl") == 0.35
        assert reg.get_weight("home_court", "nhl") == 0.15
        assert reg.get_weight("rest", "nhl") == 0.15
        assert reg.get_weight("form", "nhl") == 0.20
        assert reg.get_weight("goalie", "nhl") == 0.15

    def test_idempotent_when_already_seeded(self):
        """Calling twice doesn't duplicate or overwrite factors."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        reg.ensure_competition_factors("nhl")
        reg.ensure_competition_factors("nhl")  # Second call

        assert reg.get_weight("elo", "nhl") == 0.35
        assert reg.get_weight("goalie", "nhl") == 0.15

    def test_football_defaults_unchanged(self):
        """NHL seeding doesn't affect football global defaults."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        reg.ensure_competition_factors("nhl")

        assert reg.get_weight("elo", "world_cup") == 0.30
        assert reg.get_weight("odds", "world_cup") == 0.70

    def test_mlb_factors_unchanged(self):
        """NHL seeding doesn't affect MLB factors."""
        from app.kernel.factor_registry import FactorRegistry

        reg = FactorRegistry()
        reg.ensure_competition_factors("mlb")
        reg.ensure_competition_factors("nhl")

        # MLB factors unchanged
        assert reg.get_weight("elo", "mlb") == 0.30
        assert reg.get_weight("starting_pitcher", "mlb") == 0.25
```

Append to `backend/tests/test_kernel_prediction_kernel.py` (after `TestPhase5MLBRegistration`):

```python
class TestPhase5NHLRegistration:
    """Phase 5: NHL components are registered when PHASE5_NHL_ENABLED is true."""

    def test_nhl_engine_registered_when_enabled(self, tmp_path, monkeypatch):
        """When PHASE5_NHL_ENABLED=true, HockeyEngine is in EngineRegistry."""
        import app.core.config as config_module
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session

        db_path = str(tmp_path / "kernel_api_test_nhl.db")
        init_kernel_db(db_path)
        try:
            monkeypatch.setattr(
                config_module.settings, "KERNEL_PREDICTION_ENABLED", True
            )
            monkeypatch.setattr(
                config_module.settings, "PHASE5_NHL_ENABLED", True
            )

            # Clear cached kernel
            from app.api.routes import predictions
            if hasattr(predictions._get_kernel, "_instance"):
                delattr(predictions._get_kernel, "_instance")

            kernel = predictions._get_kernel()
            engines = kernel._engine_registry.list_engines()
            assert "hockey" in engines
            assert "elo_odds" in engines
        finally:
            close_kernel_session()
            from app.api.routes import predictions
            if hasattr(predictions._get_kernel, "_instance"):
                delattr(predictions._get_kernel, "_instance")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py::TestEnsureNHLFactors tests/test_kernel_prediction_kernel.py::TestPhase5NHLRegistration -v`
Expected: FAIL — NHL factors not seeded (`get_weight("goalie", "nhl")` returns 1.0 default), and `"hockey" not in engines`.

- [ ] **Step 3: Add NHL branch to `ensure_competition_factors`**

In `backend/app/kernel/factor_registry.py`, find the `ensure_competition_factors` method. After Task 7 added the `elif competition == "mlb":` branch, the code looks like this. Add an `elif competition == "nhl":` branch after the MLB branch.

**Find this section (post-Task 7):**
```python
        if competition == "nba":
            defaults = [
                ("elo", "elo_rating", 0.45),
                ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.25),
            ]
        elif competition == "mlb":
            defaults = [
                ("elo", "elo_rating", 0.30),
                ("home_court", "home_advantage", 0.10),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.20),
                ("starting_pitcher", "pitcher_matchup", 0.25),
            ]
        else:
            return  # Unknown competition — no defaults
```

**Replace with:**
```python
        if competition == "nba":
            defaults = [
                ("elo", "elo_rating", 0.45),
                ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.25),
            ]
        elif competition == "mlb":
            defaults = [
                ("elo", "elo_rating", 0.30),
                ("home_court", "home_advantage", 0.10),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.20),
                ("starting_pitcher", "pitcher_matchup", 0.25),
            ]
        elif competition == "nhl":
            defaults = [
                ("elo", "elo_rating", 0.35),
                ("home_court", "home_advantage", 0.15),
                ("rest", "rest_days", 0.15),
                ("form", "recent_form", 0.20),
                ("goalie", "goalie_matchup", 0.15),
            ]
        else:
            return  # Unknown competition — no defaults
```

- [ ] **Step 4: Add NHL registration block to `_get_kernel`**

In `backend/app/api/routes/predictions.py`, find the MLB registration block added in Task 7. After the MLB block (and before the `MultiFeatureBuilder` wrap condition), add the NHL registration block. Then extend the wrap condition to include `PHASE5_NHL_ENABLED`.

**Find this section (post-Task 7):**
```python
        if config.settings.PHASE5_MLB_ENABLED:
            from app.sports.baseball.mlb_adapter import MLBAdapter
            from app.sports.baseball.feature_builder import BaseballFeatureBuilder
            from app.sports.baseball.engines.baseball_engine import BaseballEngine

            adapters["mlb-"] = MLBAdapter()
            mlb_engine = BaseballEngine(factor_registry=factor_registry)
            reg.register(mlb_engine)

            factor_registry.ensure_competition_factors("mlb")
            builders["mlb-"] = BaseballFeatureBuilder()

        # If any non-football sport is enabled, wrap builders in MultiFeatureBuilder
        if config.settings.PHASE4_NBA_ENABLED or config.settings.PHASE5_MLB_ENABLED:
            from app.kernel.multi_feature_builder import MultiFeatureBuilder
            feature_builder = MultiFeatureBuilder(builders)
```

**Replace with:**
```python
        if config.settings.PHASE5_MLB_ENABLED:
            from app.sports.baseball.mlb_adapter import MLBAdapter
            from app.sports.baseball.feature_builder import BaseballFeatureBuilder
            from app.sports.baseball.engines.baseball_engine import BaseballEngine

            adapters["mlb-"] = MLBAdapter()
            mlb_engine = BaseballEngine(factor_registry=factor_registry)
            reg.register(mlb_engine)

            factor_registry.ensure_competition_factors("mlb")
            builders["mlb-"] = BaseballFeatureBuilder()

        if config.settings.PHASE5_NHL_ENABLED:
            from app.sports.hockey.nhl_adapter import NHLAdapter
            from app.sports.hockey.feature_builder import HockeyFeatureBuilder
            from app.sports.hockey.engines.hockey_engine import HockeyEngine

            adapters["nhl-"] = NHLAdapter()
            nhl_engine = HockeyEngine(factor_registry=factor_registry)
            reg.register(nhl_engine)

            factor_registry.ensure_competition_factors("nhl")
            builders["nhl-"] = HockeyFeatureBuilder()

        # If any non-football sport is enabled, wrap builders in MultiFeatureBuilder
        if (config.settings.PHASE4_NBA_ENABLED
                or config.settings.PHASE5_MLB_ENABLED
                or config.settings.PHASE5_NHL_ENABLED):
            from app.kernel.multi_feature_builder import MultiFeatureBuilder
            feature_builder = MultiFeatureBuilder(builders)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py::TestEnsureNHLFactors tests/test_kernel_prediction_kernel.py::TestPhase5NHLRegistration -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py tests/test_kernel_prediction_kernel.py tests/test_multi_feature_builder.py tests/test_kernel_factor_registry.py::TestEnsureMLBFactors -v`
Expected: ALL existing tests (including Phase 4 NBA + Phase 5 MLB registration) + 5 new Phase 5 NHL tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/kernel/factor_registry.py app/api/routes/predictions.py tests/test_kernel_factor_registry.py tests/test_kernel_prediction_kernel.py
git commit -m "feat(phase5): wire NHL components in FactorRegistry and _get_kernel

FactorRegistry.ensure_competition_factors adds 'nhl' branch seeding
5 factors (elo=0.35, home_court=0.15, rest=0.15, form=0.20,
goalie=0.15). _get_kernel registers NHLAdapter, HockeyEngine, and
HockeyFeatureBuilder when PHASE5_NHL_ENABLED=true. Extended the
MultiFeatureBuilder wrap condition to include PHASE5_NHL_ENABLED so
nhl- prefix routes to HockeyFeatureBuilder. Phase 5 MLB/NHL integration
complete — Kernel now supports 4 sports (football, basketball,
baseball, hockey)."
```

---

## Self-Review Checklist

Before handing off for execution, the following was verified against the spec:

**1. Spec coverage:** Every Phase 5 spec section maps to a task:
- Config (12 fields) → Task 1
- Shared Elo calculator → Task 2
- MLB Stats Client → Task 3
- MLBAdapter (`mlb-` prefix, DataAdapter Protocol) → Task 4
- BaseballFeatureBuilder (`mlb-1.0`) → Task 5
- BaseballEngine (5 factors: elo/home_court/rest/form/starting_pitcher) → Task 6
- MLB API integration + FactorRegistry `mlb` branch → Task 7
- NHL Stats Client → Task 8
- NHLAdapter (`nhl-` prefix, OT/SO in custom) → Task 9
- HockeyFeatureBuilder (`nhl-1.0`) → Task 10
- HockeyEngine (5 factors: elo/home_court/rest/form/goalie) → Task 11
- NHL API integration + FactorRegistry `nhl` branch → Task 12

**2. Placeholder scan:** No TBD/TODO/"implement later"/"similar to Task N" — every test and implementation step contains complete code. Each task repeats its full code even when it parallels an earlier task (engine/feature-builder tasks mirror each other but are written out in full).

**3. Type consistency:**
- `BaseballEngine.name() -> "baseball"`, `HockeyEngine.name() -> "hockey"` — matches the strings used in `_get_kernel` registration and `EngineRegistry.list_engines()` assertions.
- `BaseballFeatureBuilder.feature_version = "mlb-1.0"`, `HockeyFeatureBuilder.feature_version = "nhl-1.0"` — matches test assertions and Constraint 15.
- Factor IDs (`elo`, `home_court`, `rest`, `form`, `starting_pitcher`, `goalie`) are identical between engine `_DEFAULT_WEIGHTS`, `ContributionItem.factor`, FactorRegistry `ensure_competition_factors` branches, and test assertions.
- `ContributionItem.predicted_outcome` uses `"home_win"`/`"away_win"` (Constraint 12) — consistent across BaseballEngine and HockeyEngine.
- Config field names (`PHASE5_MLB_ENABLED`, `PHASE5_NHL_ENABLED`, `MLB_ELO_HFA`, `NHL_ELO_HFA`, `MLB_LEAGUE_AVG_TOTAL`, `NHL_LEAGUE_AVG_TOTAL`) are identical in Task 1 config, engine `config.settings.*` reads, and Task 7/12 `_get_kernel` flags.
- `parse_nhl_game` returns `went_to_overtime`/`went_to_shootout` keys → NHLAdapter writes them to `raw["custom"]` → HockeyFeatureBuilder passes `custom` through → Constraint 22 satisfied without `domain.py` changes.
- Match ID prefixes (`mlb-`, `nhl-`) consistent across adapters, `_get_kernel` adapter dict keys, and MultiFeatureBuilder builder dict keys.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-14-sports-prediction-os-phase5.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. Aligns with Constraint 14 (subagent-driven task execution with independent sub-agents per task and inter-task reviews).

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
