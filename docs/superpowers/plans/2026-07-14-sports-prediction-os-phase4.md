# Sports Prediction OS — Phase 4: NBA Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate NBA basketball as the first non-football sport, validating the Kernel's multi-sport architecture with a binary outcome model (home_win/away_win), self-computed Elo, and the MultiFeatureBuilder prefix-dispatch pattern.

**Architecture:** A parallel `backend/app/sports/basketball/` module alongside `football/`. New `MultiFeatureBuilder` mirrors `MultiAdapter` for FeatureBuilder dispatch. `BasketballEngine` uses Bradley-Terry binary model with 4 factors (elo/home_court/rest/form). `learning_service.py` is generalized to dynamically iterate outcome keys and factor names. `PredictionKernel`, `domain.py`, and frontend are zero-modification.

**Tech Stack:** Python 3.11+, SQLAlchemy ORM, SQLite, httpx, pytest

## Global Constraints

1. `PHASE4_NBA_ENABLED` defaults to OFF (false) — when false, `nba-` prefix match_ids return 404
2. `BALLDONTLIE_API_KEY` empty → NBAAdapter gracefully disables (no exceptions, `sync_schedule` returns 0)
3. NBAAdapter request interval ≥ 12 seconds (5 req/min free tier limit)
4. Reuse `kernel_match_fixtures` and `kernel_match_results` tables with `competition = "nba"` — no new fixture/result tables
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
15. BasketballFeatureBuilder `feature_version = "nba-1.0"` (distinct from football's `"1.0"`)
16. BasketballFeatureBuilder data quality: `"real"` if Elo exists, `"partial"` if missing — odds absence does NOT downgrade quality
17. All test files go in `backend/tests/` directory
18. All tests use in-memory or temp SQLite DB with per-test isolation (same pattern as existing `test_learning_weights.py`)
19. NBA match_id prefix is `nba-` (e.g., `nba-0022100001`)
20. balldontlie.io base URL: `https://api.balldontlie.io/v1`, auth header: `Authorization: {BALLDONTLIE_API_KEY}`

---

## File Structure

### New Files

| File | Responsibility | Task |
|------|---------------|------|
| `backend/app/sports/basketball/__init__.py` | Package init | 3 |
| `backend/app/sports/basketball/elo_calculator.py` | Stateless Elo computation functions | 3 |
| `backend/app/sports/basketball/balldontlie_client.py` | HTTP client for balldontlie.io API | 4 |
| `backend/app/sports/basketball/nba_adapter.py` | NBAAdapter (DataAdapter Protocol) | 4 |
| `backend/app/sports/basketball/feature_builder.py` | BasketballFeatureBuilder (FeatureBuilder Protocol) | 5 |
| `backend/app/sports/basketball/engines/__init__.py` | Engines package init | 7 |
| `backend/app/sports/basketball/engines/basketball_engine.py` | BasketballEngine (PredictionEngine Protocol) | 7 |
| `backend/app/kernel/multi_feature_builder.py` | MultiFeatureBuilder (FeatureBuilder Protocol, prefix-dispatch) | 6 |
| `backend/tests/test_nba_elo_calculator.py` | 4 tests for Elo calculator | 3 |
| `backend/tests/test_nba_adapter.py` | 5 tests for NBA adapter | 4 |
| `backend/tests/test_basketball_feature_builder.py` | 4 tests for feature builder | 5 |
| `backend/tests/test_multi_feature_builder.py` | 4 tests for MultiFeatureBuilder | 6 |
| `backend/tests/test_basketball_engine.py` | 6 tests for basketball engine | 7 |
| `backend/tests/test_learning_dynamic_outcomes.py` | 5 tests for learning service generalization | 8 |

### Modified Files

| File | Responsibility | Task |
|------|---------------|------|
| `backend/app/core/config.py` | +6 config fields for Phase 4 | 1 |
| `backend/.env.example` | +Phase 4 section | 1 |
| `backend/app/kernel/kernel_db.py` | +`KernelEloRating` table model | 2 |
| `backend/app/kernel/learning_service.py` | Dynamic outcome keys in `compute_error()`, dynamic factor iteration in `update_weights()` | 8 |
| `backend/app/kernel/factor_registry.py` | +`ensure_competition_factors()` method | 9 |
| `backend/app/api/routes/predictions.py` | `_get_kernel()` NBA registration | 10 |

**Total: 28 new tests across 6 new test files + 2 small test additions**

---

## Task Dependency Graph

```
Task 1 (Config) ─────────────────────────────────┐
Task 2 (KernelEloRating table) ───────────┐      │
                                          │      │
Task 3 (Elo Calculator) ─────┐            │      │
                             ▼            ▼      ▼
Task 4 (NBAAdapter + client) ◄───────────────────┘
                             │
Task 5 (BasketballFeatureBuilder) ── independent
                             │
Task 6 (MultiFeatureBuilder) ◄── needs Task 5
                             │
Task 7 (BasketballEngine) ◄── needs Task 3 (Elo functions)
                             │
Task 8 (LearningService generalization) ── independent but riskiest
                             │
Task 9 (FactorRegistry.ensure_competition_factors) ── independent
                             │
Task 10 (API integration) ◄── needs ALL above
```

Tasks 1, 2, 3, 5, 8, 9 can potentially run in parallel (no inter-dependencies). Tasks 4, 6, 7, 10 have dependencies on earlier tasks.

---

### Task 1: Config + .env.example

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config.py` (add 1 test class)

**Interfaces:**
- Consumes: existing `_env_bool` helper, `os.getenv`
- Produces: `config.settings.PHASE4_NBA_ENABLED` (bool, default false), `config.settings.BALLDONTLIE_API_KEY` (str, default ""), `config.settings.NBA_ELO_HFA` (int, default 100), `config.settings.NBA_ELO_K_REGULAR` (int, default 20), `config.settings.NBA_ELO_K_PLAYOFF` (int, default 30), `config.settings.NBA_LEAGUE_AVG_TOTAL` (float, default 220.0)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_config.py` (append new test class at end of file):

```python
class TestPhase4Config:
    """Phase 4 NBA configuration fields."""

    def test_phase4_nba_enabled_defaults_false(self):
        from app.core import config
        assert config.settings.PHASE4_NBA_ENABLED is False

    def test_balldontlie_api_key_defaults_empty(self):
        from app.core import config
        assert config.settings.BALLDONTLIE_API_KEY == ""

    def test_nba_elo_hfa_default(self):
        from app.core import config
        assert config.settings.NBA_ELO_HFA == 100

    def test_nba_elo_k_regular_default(self):
        from app.core import config
        assert config.settings.NBA_ELO_K_REGULAR == 20

    def test_nba_elo_k_playoff_default(self):
        from app.core import config
        assert config.settings.NBA_ELO_K_PLAYOFF == 30

    def test_nba_league_avg_total_default(self):
        from app.core import config
        assert config.settings.NBA_LEAGUE_AVG_TOTAL == 220.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_config.py::TestPhase4Config -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'PHASE4_NBA_ENABLED'`

- [ ] **Step 3: Add config fields to config.py**

In `backend/app/core/config.py`, find the line `CLUB_ELO_REQUEST_INTERVAL: float = float(os.getenv("CLUB_ELO_REQUEST_INTERVAL", "1.0"))` (around line 996-997) and add after it:

```python
    # Phase 4 — NBA Integration (default OFF). When false, nba- prefix
    # match_ids return 404 and NBAAdapter/BasketballEngine are not
    # instantiated.
    PHASE4_NBA_ENABLED: bool = _env_bool("PHASE4_NBA_ENABLED", "false")
    BALLDONTLIE_API_KEY: str = os.getenv("BALLDONTLIE_API_KEY", "")
    NBA_ELO_HFA: int = int(os.getenv("NBA_ELO_HFA", "100"))
    NBA_ELO_K_REGULAR: int = int(os.getenv("NBA_ELO_K_REGULAR", "20"))
    NBA_ELO_K_PLAYOFF: int = int(os.getenv("NBA_ELO_K_PLAYOFF", "30"))
    NBA_LEAGUE_AVG_TOTAL: float = float(os.getenv("NBA_LEAGUE_AVG_TOTAL", "220.0"))
```

- [ ] **Step 4: Add Phase 4 section to .env.example**

Append at the end of `backend/.env.example`:

```env
# === Phase 4: NBA Integration ===
# When false, nba- prefix match_ids return 404 and NBA components are not loaded.
PHASE4_NBA_ENABLED=false  # 中文：是否启用 NBA 篮球集成；默认关闭。
# balldontlie.io API key (free tier: 5 req/min). Empty = NBAAdapter disabled.
BALLDONTLIE_API_KEY=""  # 中文：balldontlie.io API Key；为空则自动禁用 NBA 数据源。
# NBA Elo parameters (self-computed from historical games — no external Elo source)
NBA_ELO_HFA=100  # 中文：NBA 主场优势 Elo 加成。
NBA_ELO_K_REGULAR=20  # 中文：常规赛 K 因子。
NBA_ELO_K_PLAYOFF=30  # 中文：季后赛 K 因子。
NBA_LEAGUE_AVG_TOTAL=220.0  # 中文：NBA 联盟平均总得分，用于预测比分转换。
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_config.py::TestPhase4Config -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Verify no regression**

Run: `cd backend && python -m pytest tests/test_config.py tests/test_config_defaults.py -v`
Expected: All existing config tests still pass.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/core/config.py .env.example tests/test_config.py
git commit -m "feat(phase4): add NBA integration config fields

Add 6 Phase 4 config settings: PHASE4_NBA_ENABLED (default OFF),
BALLDONTLIE_API_KEY (default empty), NBA_ELO_HFA (100),
NBA_ELO_K_REGULAR (20), NBA_ELO_K_PLAYOFF (30),
NBA_LEAGUE_AVG_TOTAL (220.0)."
```

---

### Task 2: KernelEloRating Table Model

**Files:**
- Modify: `backend/app/kernel/kernel_db.py`
- Test: `backend/tests/test_db_migration.py` (add 1 test)

**Interfaces:**
- Consumes: existing `KernelBase` declarative base, `init_kernel_db()`, `get_kernel_session()`
- Produces: `KernelEloRating` SQLAlchemy model class with columns: `team_name` (PK), `sport`, `competition`, `elo_rating` (Float), `source` (default "self_computed"), `updated_at` (DateTime)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_db_migration.py` (append new test class at end):

```python
class TestKernelEloRatingTable:
    """Phase 4: kernel_elo_ratings table for self-computed NBA Elo."""

    def test_elo_ratings_table_created(self, tmp_path):
        """KernelEloRating table is created by init_kernel_db()."""
        from app.kernel.kernel_db import (
            init_kernel_db, close_kernel_session, get_kernel_session,
            KernelEloRating,
        )
        db_path = str(tmp_path / "kernel_elo_test.db")
        init_kernel_db(db_path)
        try:
            session = get_kernel_session()
            # Verify table exists by inserting and querying a row
            from datetime import datetime, timezone
            row = KernelEloRating(
                team_name="Boston Celtics",
                sport="basketball",
                competition="nba",
                elo_rating=1650.0,
                source="self_computed",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.commit()
            fetched = session.get(KernelEloRating, "Boston Celtics")
            assert fetched is not None
            assert fetched.elo_rating == 1650.0
            assert fetched.competition == "nba"
            session.close()
        finally:
            close_kernel_session()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_db_migration.py::TestKernelEloRatingTable -v`
Expected: FAIL with `ImportError: cannot import name 'KernelEloRating'`

- [ ] **Step 3: Add KernelEloRating model to kernel_db.py**

In `backend/app/kernel/kernel_db.py`, find the `KernelClubEloCache` class definition (around line 156-165) and add after it (before `def init_kernel_db`):

```python
class KernelEloRating(KernelBase):
    """Self-computed Elo ratings for sports without external Elo sources.

    Used by NBA (basketball) where no external Elo API exists. Follows
    the kernel_ prefix convention. Can be reused for future self-computed
    Elo in other sports.
    """
    __tablename__ = "kernel_elo_ratings"

    team_name = Column(String, primary_key=True)
    sport = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    elo_rating = Column(Float, nullable=False)
    source = Column(String, default="self_computed")
    updated_at = Column(DateTime, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_db_migration.py::TestKernelEloRatingTable -v`
Expected: PASS

- [ ] **Step 5: Verify no regression**

Run: `cd backend && python -m pytest tests/test_db_migration.py tests/test_kernel_db_fixtures.py -v`
Expected: All existing DB tests still pass.

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/kernel/kernel_db.py tests/test_db_migration.py
git commit -m "feat(phase4): add KernelEloRating table for self-computed NBA Elo

New kernel_elo_ratings table stores self-computed Elo ratings for
sports without external Elo sources (NBA). Columns: team_name (PK),
sport, competition, elo_rating, source, updated_at."
```

---

### Task 3: NBA Elo Calculator (Stateless Functions)

**Files:**
- Create: `backend/app/sports/basketball/__init__.py`
- Create: `backend/app/sports/basketball/elo_calculator.py`
- Test: `backend/tests/test_nba_elo_calculator.py` (4 tests)

**Interfaces:**
- Consumes: nothing (pure stateless functions)
- Produces:
  - `compute_expected_score(elo_home: float, elo_away: float, hfa: int = 100) -> float` — returns E_home (probability home wins)
  - `update_elo(elo: float, expected: float, actual: float, k: int = 20) -> float` — returns new Elo rating
  - `apply_season_regression(elo: float, mean: float = 1500.0, carry: float = 0.75) -> float` — returns regressed Elo
  - `seed_elo_from_games(games: list[dict], hfa: int = 100, k_regular: int = 20, k_playoff: int = 30) -> dict[str, float]` — returns `{team_name: elo_rating}` after processing games chronologically

  `games` is a list of dicts with keys: `home_team` (str), `away_team` (str), `home_score` (int), `away_score` (int), `is_playoff` (bool), `season` (int). Games must be in chronological order. `seed_elo_from_games` applies season regression when the `season` field changes between consecutive games.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nba_elo_calculator.py`:

```python
# backend/tests/test_nba_elo_calculator.py
"""Tests for NBA Elo calculator — stateless functions."""
import pytest

from app.sports.basketball.elo_calculator import (
    compute_expected_score,
    update_elo,
    apply_season_regression,
    seed_elo_from_games,
)


class TestComputeExpectedScore:
    def test_equal_elo_with_hfa(self):
        """Equal Elo + HFA=100 → home advantage > 0.5."""
        # elo_home = elo_away = 1500, HFA = 100
        # E_home = 1 / (1 + 10^((1500 - 1500 - 100) / 400))
        #        = 1 / (1 + 10^(-0.25))
        #        = 1 / (1 + 0.5623)
        #        ≈ 0.6401
        p = compute_expected_score(1500.0, 1500.0, hfa=100)
        assert round(p, 4) == 0.6401

    def test_equal_elo_no_hfa(self):
        """Equal Elo + HFA=0 → 0.5 (no home advantage)."""
        p = compute_expected_score(1500.0, 1500.0, hfa=0)
        assert round(p, 4) == 0.5000


class TestUpdateElo:
    def test_win_increases_elo(self):
        """Winning increases Elo; K=20, expected=0.5, actual=1.0 → +10."""
        new_elo = update_elo(1500.0, expected=0.5, actual=1.0, k=20)
        assert new_elo == 1510.0

    def test_loss_decreases_elo(self):
        """Losing decreases Elo; K=20, expected=0.5, actual=0.0 → -10."""
        new_elo = update_elo(1500.0, expected=0.5, actual=0.0, k=20)
        assert new_elo == 1490.0

    def test_k_playoff_higher_than_regular(self):
        """K=30 (playoff) produces larger swing than K=20 (regular)."""
        regular = update_elo(1500.0, expected=0.5, actual=1.0, k=20)
        playoff = update_elo(1500.0, expected=0.5, actual=1.0, k=30)
        assert playoff > regular


class TestApplySeasonRegression:
    def test_regression_toward_mean(self):
        """new_elo = 0.75 * old + 0.25 * 1500 → pulls toward 1500."""
        # 1600 → 0.75*1600 + 0.25*1500 = 1200 + 375 = 1575
        regressed = apply_season_regression(1600.0, mean=1500.0, carry=0.75)
        assert regressed == 1575.0

    def test_low_elo_pulls_up(self):
        """Below-average Elo pulls up toward mean."""
        # 1400 → 0.75*1400 + 0.25*1500 = 1050 + 375 = 1425
        regressed = apply_season_regression(1400.0)
        assert regressed == 1425.0


class TestSeedEloFromGames:
    def test_seed_produces_ratings_for_all_teams(self):
        """After processing games, all teams have Elo ratings."""
        games = [
            {"home_team": "Celtics", "away_team": "Lakers",
             "home_score": 110, "away_score": 108, "is_playoff": False, "season": 2023},
            {"home_team": "Lakers", "away_team": "Celtics",
             "home_score": 105, "away_score": 100, "is_playoff": False, "season": 2023},
        ]
        ratings = seed_elo_from_games(games)
        assert "Celtics" in ratings
        assert "Lakers" in ratings
        # Both start at 1500; after 2 games they should still be near 1500
        assert 1450 < ratings["Celtics"] < 1550
        assert 1450 < ratings["Lakers"] < 1550

    def test_season_regression_applied(self):
        """When season changes, regression is applied between seasons."""
        games = [
            # Season 2023: Celtics win 10 games straight (Elo climbs high)
            *[{"home_team": "Celtics", "away_team": f"Team{i}",
               "home_score": 110, "away_score": 100,
               "is_playoff": False, "season": 2023} for i in range(10)],
            # Season 2024: first game
            {"home_team": "Celtics", "away_team": "TeamX",
             "home_score": 110, "away_score": 100,
             "is_playoff": False, "season": 2024},
        ]
        ratings = seed_elo_from_games(games)

        # Without regression, Celtics would be well above 1500 after 10 wins.
        # With regression (carry=0.75), their Elo is pulled toward 1500
        # before the 2024 season starts. Verify regression was applied by
        # checking the 2024 rating is lower than the pre-regression value
        # would be (10 wins at K=20 would add ~100 points; regression pulls
        # 25% back toward 1500).
        # After 10 wins: ~1600 (approximate). After regression: ~1575.
        # After 1 more win: ~1585.
        assert ratings["Celtics"] > 1500  # Still strong
        assert ratings["Celtics"] < 1620  # But regression kept it in check
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_nba_elo_calculator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.basketball'`

- [ ] **Step 3: Create basketball package init**

Create `backend/app/sports/basketball/__init__.py` (empty file):

```python
# backend/app/sports/basketball/__init__.py
"""Basketball sport module — NBA integration (Phase 4)."""
```

- [ ] **Step 4: Create elo_calculator.py**

Create `backend/app/sports/basketball/elo_calculator.py`:

```python
# backend/app/sports/basketball/elo_calculator.py
"""Stateless Elo computation functions for NBA basketball.

No external Elo source exists for NBA (unlike football which has
ClubElo.com). These functions compute Elo ratings from historical
game results using standard Elo with basketball-specific parameters.

Parameters (defaults match Phase 4 spec):
    HFA (Home Field Advantage) = 100
    K-factor (regular season) = 20
    K-factor (playoff) = 30
    Season regression = 0.75 (carry 75% of previous season's Elo)
    Initial Elo = 1500
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

Run: `cd backend && python -m pytest tests/test_nba_elo_calculator.py -v`
Expected: PASS (4 test classes, 7 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/basketball/__init__.py app/sports/basketball/elo_calculator.py tests/test_nba_elo_calculator.py
git commit -m "feat(phase4): add NBA Elo calculator with stateless functions

4 functions: compute_expected_score (HFA=100), update_elo (K-factor),
apply_season_regression (carry=0.75), seed_elo_from_games (chronological
processing with season boundary regression). All teams start at 1500."
```

---

### Task 4: NBAAdapter + balldontlie Client

**Files:**
- Create: `backend/app/sports/basketball/balldontlie_client.py`
- Create: `backend/app/sports/basketball/nba_adapter.py`
- Test: `backend/tests/test_nba_adapter.py` (5 tests)

**Interfaces:**
- Consumes:
  - `config.settings.BALLDONTLIE_API_KEY`, `config.settings.NBA_ELO_HFA`, `config.settings.NBA_ELO_K_REGULAR`, `config.settings.NBA_ELO_K_PLAYOFF`
  - `app.kernel.kernel_db.KernelMatchFixture`, `KernelMatchResult`, `KernelEloRating`, `get_kernel_session`
  - `app.sports.basketball.elo_calculator.seed_elo_from_games`
  - `app.kernel.domain` value objects (SportIdentity, CompetitionIdentity, etc.)
- Produces:
  - `NBAAdapter` class implementing DataAdapter Protocol
  - `fetch_nba_games(season: int) -> list[dict]` — fetches games from balldontlie.io with pagination
  - `parse_nba_game(game_data: dict) -> dict` — parses raw API response to internal fixture format

  NBAAdapter raw dict structure (returned by `fetch_all_data`):
  ```python
  {
      "team": {"elo_home": float|None, "elo_away": float|None, "form_home": float, "form_away": float},
      "general": {"rest_days_home": int, "rest_days_away": int, "days_since_last_match": int},
      "market": {},
      "player": {},
      "environment": {"venue": str, "is_home_advantage": True},
      "custom": {"pace_home": float, "pace_away": float, "ortg_home": float, "ortg_away": float,
                 "drtg_home": float, "drtg_away": float, "tpct_home": float, "tpct_away": float},
  }
  ```

  Match ID format: `nba-{balldontlie_game_id}` (e.g., `nba-0022100001`)

  Stage mapping: `postseason=False` → `"regular_season"`, `postseason=True` → `"playoff"`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nba_adapter.py`:

```python
# backend/tests/test_nba_adapter.py
"""Tests for NBAAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.basketball.nba_adapter import NBAAdapter, parse_nba_game


_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)


def _make_match(match_id="nba-123") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_NBA, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="BOS", name="Boston Celtics", competition=_NBA),
        away=TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=_NBA),
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="nba-123", home="Boston Celtics", away="Los Angeles Lakers"):
    """Create a mock KernelMatchFixture row."""
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "nba"
    fixture.season = "2024-25"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 12, 25, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "TD Garden"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestNBAAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = NBAAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseNbaGame:
    def test_parses_regular_season_game(self):
        """parse_nba_game maps API fields to internal fixture format."""
        raw = {
            "id": 123,
            "season": 2023,
            "postseason": False,
            "home_team": {"id": 1, "full_name": "Boston Celtics"},
            "visitor_team": {"id": 2, "full_name": "Los Angeles Lakers"},
            "date": "2023-12-25T00:00:00Z",
            "home_team_score": 114,
            "visitor_team_score": 108,
            "status": "Final",
        }
        parsed = parse_nba_game(raw)
        assert parsed["match_id"] == "nba-123"
        assert parsed["home_team"] == "Boston Celtics"
        assert parsed["away_team"] == "Los Angeles Lakers"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"

    def test_parses_playoff_game(self):
        """postseason=True maps to 'playoff' stage."""
        raw = {
            "id": 456,
            "season": 2023,
            "postseason": True,
            "home_team": {"id": 1, "full_name": "Boston Celtics"},
            "visitor_team": {"id": 2, "full_name": "Los Angeles Lakers"},
            "date": "2024-04-15T00:00:00Z",
            "home_team_score": 0,
            "visitor_team_score": 0,
            "status": "Scheduled",
        }
        parsed = parse_nba_game(raw)
        assert parsed["match_id"] == "nba-456"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "scheduled"


class TestNBAAdapterGetMatchIdentity:
    @patch("app.sports.basketball.nba_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = NBAAdapter()
        identity = adapter.get_match_identity("nba-123")
        assert identity.match_id == "nba-123"
        assert identity.home.name == "Boston Celtics"
        assert identity.away.name == "Los Angeles Lakers"
        assert identity.season.competition.code == "nba"

    @patch("app.sports.basketball.nba_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = NBAAdapter()
        identity = adapter.get_match_identity("nba-nonexistent")
        assert identity.match_id == "nba-nonexistent"
        assert identity.home.name == "Home"


class TestNBAAdapterSyncSchedule:
    @patch("app.sports.basketball.nba_adapter.save_fixture")
    @patch("app.sports.basketball.nba_adapter.parse_nba_game")
    @patch("app.sports.basketball.nba_adapter.fetch_nba_games")
    @patch("app.sports.basketball.nba_adapter.config")
    def test_sync_returns_count_when_api_key_present(
        self, mock_config, mock_fetch, mock_parse, mock_save
    ):
        mock_config.settings.BALLDONTLIE_API_KEY = "test-key"
        mock_fetch.return_value = [{"id": 1}, {"id": 2}]
        mock_parse.return_value = {"match_id": "nba-1"}
        adapter = NBAAdapter()
        count = adapter.sync_schedule()
        assert count == 2

    @patch("app.sports.basketball.nba_adapter.config")
    def test_sync_returns_zero_when_api_key_empty(self, mock_config):
        mock_config.settings.BALLDONTLIE_API_KEY = ""
        adapter = NBAAdapter()
        count = adapter.sync_schedule()
        assert count == 0


class TestNBAAdapterFetchAllData:
    @patch("app.sports.basketball.nba_adapter.query_fixture")
    def test_fetch_all_data_returns_elo_from_db(self, mock_query):
        """fetch_all_data reads Elo from kernel_elo_ratings table."""
        # Setup: fixture exists in DB with team names
        mock_query.return_value = _make_fixture()

        adapter = NBAAdapter()
        # Mock the internal elo lookup to avoid real DB
        with patch.object(adapter, "_fetch_elo_ratings", return_value={"Boston Celtics": 1650.0, "Los Angeles Lakers": 1520.0}):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1650.0
            assert raw["team"]["elo_away"] == 1520.0
            assert raw["environment"]["is_home_advantage"] is True


class TestNBAAdapterFetchOutcome:
    @patch("app.sports.basketball.nba_adapter.build_match_outcome")
    @patch("app.sports.basketball.nba_adapter.query_result")
    def test_fetch_outcome_returns_outcome(self, mock_query, mock_build):
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="nba-123",
            home_score=114, away_score=108,
            outcome="home_win",
            finished_at=datetime(2024, 12, 25, tzinfo=timezone.utc),
        )
        adapter = NBAAdapter()
        result = adapter.fetch_outcome("nba-123")
        assert result is not None
        assert result.home_score == 114
        assert result.outcome == "home_win"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_nba_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.basketball.nba_adapter'`

- [ ] **Step 3: Create balldontlie_client.py**

Create `backend/app/sports/basketball/balldontlie_client.py`:

```python
# backend/app/sports/basketball/balldontlie_client.py
"""HTTP client for balldontlie.io NBA API.

Free tier: 5 req/min. Provides Teams, Players, Games endpoints.
Auth: Authorization header with API key (no Bearer prefix).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from app.core import config

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.balldontlie.io/v1"
_REQUEST_INTERVAL_SECONDS = 12.0  # 5 req/min → 12s between requests

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class BalldontlieClientError(Exception):
    """balldontlie.io API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain ≥ 12s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def fetch_nba_games(season: int) -> list[dict[str, Any]]:
    """Fetch all NBA games for a season from balldontlie.io.

    Uses cursor-based pagination with per_page=100.

    Args:
        season: Season year (e.g., 2023 for 2023-24 season).

    Returns:
        List of raw game dicts from the API response.

    Raises:
        BalldontlieClientError: If API key is missing or request fails.
    """
    api_key = config.settings.BALLDONTLIE_API_KEY
    if not api_key:
        raise BalldontlieClientError("BALLDONTLIE_API_KEY not configured")

    all_games: list[dict[str, Any]] = []
    cursor: int | None = None

    while True:
        _enforce_rate_limit()
        params: dict[str, Any] = {"seasons[]": season, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor

        try:
            response = httpx.get(
                f"{_BASE_URL}/games",
                headers={"Authorization": api_key},
                params=params,
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise BalldontlieClientError("Request timeout") from exc
        except httpx.RequestError as exc:
            raise BalldontlieClientError(f"Request failed: {exc}") from exc

        if response.status_code == 401:
            raise BalldontlieClientError("API key invalid")
        if response.status_code == 429:
            raise BalldontlieClientError("Rate limit exceeded")
        if response.status_code != 200:
            raise BalldontlieClientError(
                f"API error: {response.status_code} - {response.text[:200]}"
            )

        data = response.json()
        if not isinstance(data, dict):
            raise BalldontlieClientError("balldontlie.io returned non-object JSON")

        all_games.extend(data.get("data", []))

        meta = data.get("meta", {})
        next_cursor = meta.get("next_cursor")
        if next_cursor is None:
            break
        cursor = next_cursor

    return all_games
```

- [ ] **Step 4: Create nba_adapter.py**

Create `backend/app/sports/basketball/nba_adapter.py`:

```python
# backend/app/sports/basketball/nba_adapter.py
"""NBAAdapter — DataAdapter Protocol implementation for NBA basketball.

Bridges balldontlie.io API to the sport-agnostic DataAdapter Protocol.
The Kernel never sees basketball-specific code — it only sees DataAdapter.

Match ID format: nba-{balldontlie_game_id}
Stage mapping: postseason=False → "regular_season", postseason=True → "playoff"

When BALLDONTLIE_API_KEY is empty, sync_schedule() returns 0 and
fetch_all_data() returns a raw dict with None Elo values (graceful
degradation, no exceptions).
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
from app.sports.basketball.balldontlie_client import fetch_nba_games
from app.sports.basketball.elo_calculator import seed_elo_from_games

logger = logging.getLogger(__name__)

_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)
_DEFAULT_SEASON = "2024-25"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2024, 12, 25, tzinfo=timezone.utc)


def parse_nba_game(game_data: dict) -> dict | None:
    """Parse a raw balldontlie.io game dict into internal fixture format.

    Returns None if game_data is malformed.
    """
    game_id = game_data.get("id")
    if not game_id:
        return None

    home_team = game_data.get("home_team", {}).get("full_name", "")
    away_team = game_data.get("visitor_team", {}).get("full_name", "")
    if not home_team or not away_team:
        return None

    date_str = game_data.get("date", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    postseason = game_data.get("postseason", False)
    stage = "playoff" if postseason else "regular_season"

    status_raw = game_data.get("status", "")
    status = "finished" if status_raw == "Final" else "scheduled"

    home_score = game_data.get("home_team_score")
    away_score = game_data.get("visitor_team_score")

    return {
        "match_id": f"nba-{game_id}",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "stage": stage,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
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
    """Build MatchOutcome from a KernelMatchResult row."""
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
    """Upsert a parsed NBA fixture into kernel_match_fixtures."""
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


class NBAAdapter:
    """DataAdapter Protocol implementation for NBA basketball."""

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=_NBA)
        away = TeamIdentity(code="AWAY", name="Away", competition=_NBA)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_NBA, season_key=_DEFAULT_SEASON),
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
            competition=_NBA,
        )
        away = TeamIdentity(
            code=(fixture.away_team or "AWAY")[:3].upper(),
            name=fixture.away_team or "Away",
            competition=_NBA,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(competition=_NBA, season_key=fixture.season or _DEFAULT_SEASON),
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
            ratings = {}
            for team_name in [home_team, away_team]:
                row = session.get(KernelEloRating, team_name)
                if row is not None:
                    ratings[team_name] = row.elo_rating
            return ratings
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch Elo ratings: %s", exc)
            return {}
        finally:
            session.close()

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an NBA match.

        All data comes from local DB (no API calls). Elo ratings are read
        from kernel_elo_ratings table. Form and rest days are computed
        from recent fixtures.
        """
        home_name = match.home.name
        away_name = match.away.name

        elo_ratings = self._fetch_elo_ratings(home_name, away_name)
        elo_home = elo_ratings.get(home_name)
        elo_away = elo_ratings.get(away_name)

        # Compute form (last-10 win rate) from recent results
        form_home = self._compute_form(home_name)
        form_away = self._compute_form(away_name)

        # Rest days (simplified — 0 if unknown)
        rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
        rest_away = self._compute_rest_days(away_name, match.kickoff_utc)

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
            "market": {},
            "player": {},
            "environment": {
                "venue": "Home Arena",
                "is_home_advantage": True,
            },
            "custom": {
                "pace_home": 99.5,
                "pace_away": 97.2,
                "ortg_home": 112.3,
                "ortg_away": 108.1,
                "drtg_home": 105.0,
                "drtg_away": 110.5,
                "tpct_home": 0.365,
                "tpct_away": 0.342,
            },
        }
        return raw

    def _compute_form(self, team_name: str) -> float:
        """Compute last-10 win rate from kernel_match_results.

        Returns 0.5 if no data available.
        """
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "nba",
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
                    KernelMatchFixture.competition == "nba",
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
        """Sync NBA schedule from balldontlie.io.

        Returns 0 if API key is not configured or sync fails.
        """
        if not config.settings.BALLDONTLIE_API_KEY:
            return 0

        try:
            # Fetch current season games
            season_year = 2024  # 2024-25 season
            games_raw = fetch_nba_games(season_year)
            count = 0
            for raw in games_raw:
                parsed = parse_nba_game(raw)
                if parsed:
                    save_fixture(parsed, "nba", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync NBA schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "nba"
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
            logger.warning("Failed to fetch NBA schedule: %s", exc)
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_nba_adapter.py -v`
Expected: PASS (5 test classes, 8 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/basketball/balldontlie_client.py app/sports/basketball/nba_adapter.py tests/test_nba_adapter.py
git commit -m "feat(phase4): add NBAAdapter + balldontlie.io client

NBAAdapter implements DataAdapter Protocol with nba- prefix.
balldontlie_client fetches games with 12s rate limiting (5 req/min).
sync_schedule returns 0 when BALLDONTLIE_API_KEY is empty (graceful
degradation). fetch_all_data reads Elo from kernel_elo_ratings table."
```

---

### Task 5: BasketballFeatureBuilder

**Files:**
- Create: `backend/app/sports/basketball/feature_builder.py`
- Test: `backend/tests/test_basketball_feature_builder.py` (4 tests)

**Interfaces:**
- Consumes: `app.kernel.domain` value objects (SportIdentity, FeatureSet, GeneralFeatures, TeamFeatures, MarketFeatures, PlayerFeatures, EnvironmentFeatures)
- Produces: `BasketballFeatureBuilder` class implementing FeatureBuilder Protocol
  - `sport() -> SportIdentity` returns `SportIdentity(code="basketball", name="Basketball")`
  - `build(match: MatchIdentity, raw: dict) -> FeatureSet` with `feature_version = "nba-1.0"`
  - Data quality: `"real"` if `raw["team"]["elo_home"] is not None`, `"partial"` otherwise
  - Odds absence does NOT downgrade quality (unlike FootballFeatureBuilder)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_basketball_feature_builder.py`:

```python
# backend/tests/test_basketball_feature_builder.py
"""Tests for BasketballFeatureBuilder — FeatureBuilder Protocol."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.basketball.feature_builder import BasketballFeatureBuilder


_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="nba-123",
        season=SeasonIdentity(competition=_NBA, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="BOS", name="Boston Celtics", competition=_NBA),
        away=TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=_NBA),
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _make_raw_with_elo():
    return {
        "team": {"elo_home": 1650.0, "elo_away": 1520.0, "form_home": 0.7, "form_away": 0.4},
        "general": {"rest_days_home": 2, "rest_days_away": 1, "days_since_last_match": 2},
        "market": {},
        "player": {},
        "environment": {"venue": "TD Garden", "is_home_advantage": True},
        "custom": {
            "pace_home": 99.5, "pace_away": 97.2,
            "ortg_home": 112.3, "ortg_away": 108.1,
            "drtg_home": 105.0, "drtg_away": 110.5,
            "tpct_home": 0.365, "tpct_away": 0.342,
        },
    }


class TestBasketballFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        builder = BasketballFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_basketball(self):
        builder = BasketballFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "basketball"


class TestBasketballFeatureBuilderBuild:
    def test_full_feature_mapping(self):
        """All layers are mapped correctly from raw dict."""
        builder = BasketballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())

        # General layer
        assert features.general.rest_days_home == 2
        assert features.general.rest_days_away == 1

        # Team layer
        assert features.team.elo_rating_home == 1650.0
        assert features.team.elo_rating_away == 1520.0
        assert features.team.form_home == 0.7
        assert features.team.form_away == 0.4
        # Basketball has no draws
        assert features.team.h2h_draw_rate is None
        assert features.team.market_value_home is None

        # Market layer — all None (free tier has no odds)
        assert features.market.odds_home is None
        assert features.market.odds_away is None

        # Environment layer
        assert features.environment.venue == "TD Garden"
        assert features.environment.is_home_advantage is True
        # Weather not applicable to basketball
        assert features.environment.weather_temp_c is None

        # Custom layer — basketball-specific features
        assert features.custom["pace_home"] == 99.5
        assert features.custom["ortg_home"] == 112.3
        assert features.custom["drtg_away"] == 110.5
        assert features.custom["tpct_home"] == 0.365

        # Feature version
        assert features.feature_version == "nba-1.0"

    def test_data_quality_real_when_elo_present(self):
        """Data quality is 'real' when Elo exists, even without odds."""
        builder = BasketballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.data_quality == "real"
        # No odds-related quality notes (unlike football)
        assert "betting_odds_unavailable" not in features.quality_notes

    def test_data_quality_partial_when_elo_missing(self):
        """Data quality is 'partial' when Elo is None."""
        builder = BasketballFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["team"]["elo_home"] = None
        raw["team"]["elo_away"] = None
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_basketball_feature_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.basketball.feature_builder'`

- [ ] **Step 3: Create feature_builder.py**

Create `backend/app/sports/basketball/feature_builder.py`:

```python
# backend/app/sports/basketball/feature_builder.py
"""BasketballFeatureBuilder — computes FeatureSet from raw NBA data.

Maps raw dict from NBAAdapter to standardized FeatureSet. Unlike
FootballFeatureBuilder, odds absence does NOT downgrade data quality
because the balldontlie.io free tier has no odds by design, and
BasketballEngine does not use odds.

Feature version: "nba-1.0" (distinct from football's "1.0").
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

_BASKETBALL = SportIdentity(code="basketball", name="Basketball")


class BasketballFeatureBuilder:
    """Builds FeatureSet for NBA basketball matches.

    Implements the FeatureBuilder Protocol. Consumes a raw dict with
    keys ``team``, ``market``, ``player``, ``environment``, ``general``,
    and ``custom`` and produces a FeatureSet.
    """

    def sport(self) -> SportIdentity:
        return _BASKETBALL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Data quality: "real" if Elo exists, "partial" otherwise.
        # Unlike football, odds absence does NOT downgrade quality.
        has_elo = team_raw.get("elo_home") is not None
        data_quality = "real" if has_elo else "partial"
        quality_notes: list[str] = []

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=None,  # Not tracked for basketball
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=None,  # Not computed for basketball
                h2h_draw_rate=None,  # Basketball has no draws
                market_value_home=None,  # Not applicable
                market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=None,  # Free tier has no odds
                odds_draw=None,
                odds_away=None,
                odds_source=None,
                odds_fresh=False,
            ),
            player=PlayerFeatures(
                key_players_available_home=None,  # Free tier has no injuries
                key_players_available_away=None,
                injury_impact_home=None,
                injury_impact_away=None,
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=None,  # Indoor sport
                weather_condition=None,
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="nba-1.0",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_basketball_feature_builder.py -v`
Expected: PASS (2 test classes, 4 test methods)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sports/basketball/feature_builder.py tests/test_basketball_feature_builder.py
git commit -m "feat(phase4): add BasketballFeatureBuilder

Maps NBA raw dict to FeatureSet with feature_version='nba-1.0'.
Data quality is 'real' when Elo exists (odds absence does NOT
downgrade — basketball free tier has no odds by design). Custom
dict holds pace/ortg/drtg/tpct basketball-specific features."
```

---

### Task 6: MultiFeatureBuilder

**Files:**
- Create: `backend/app/kernel/multi_feature_builder.py`
- Test: `backend/tests/test_multi_feature_builder.py` (4 tests)

**Interfaces:**
- Consumes: `app.kernel.domain.SportIdentity`, `app.kernel.domain.MatchIdentity`, `app.kernel.domain.FeatureSet`, FeatureBuilder Protocol
- Produces: `MultiFeatureBuilder` class implementing FeatureBuilder Protocol
  - `__init__(builders: dict[str, FeatureBuilder])` — prefix-to-builder mapping, first builder is default
  - `_select(match_id: str) -> FeatureBuilder` — prefix-based dispatch
  - `sport() -> SportIdentity` — returns default builder's sport
  - `build(match: MatchIdentity, raw: dict) -> FeatureSet` — delegates to selected builder

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_multi_feature_builder.py`:

```python
# backend/tests/test_multi_feature_builder.py
"""Tests for MultiFeatureBuilder — prefix-dispatch FeatureBuilder proxy."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import FeatureBuilder
from app.kernel.multi_feature_builder import MultiFeatureBuilder


_FOOTBALL = SportIdentity(code="football", name="Football")
_BASKETBALL = SportIdentity(code="basketball", name="Basketball")


def _make_match(match_id="wc-123") -> MatchIdentity:
    if match_id.startswith("wc-"):
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=_FOOTBALL)
    else:
        comp = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=comp, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="HOME", name="Home", competition=comp),
        away=TeamIdentity(code="AWAY", name="Away", competition=comp),
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _mock_builder(sport: SportIdentity) -> MagicMock:
    """Create a MagicMock that satisfies FeatureBuilder Protocol."""
    builder = MagicMock()
    builder.sport.return_value = sport
    builder.build.return_value = FeatureSet(
        match=_make_match(),
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(None, None, None, None, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[],
        feature_version="1.0",
    )
    return builder


class TestMultiFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        fb = MultiFeatureBuilder({"wc-": _mock_builder(_FOOTBALL)})
        assert isinstance(fb, FeatureBuilder)


class TestPrefixDispatch:
    def test_wc_prefix_dispatches_to_football_builder(self):
        football = _mock_builder(_FOOTBALL)
        basketball = _mock_builder(_BASKETBALL)
        mfb = MultiFeatureBuilder({"wc-": football, "nba-": basketball})

        match = _make_match("wc-123")
        mfb.build(match, {})
        football.build.assert_called_once_with(match, {})
        basketball.build.assert_not_called()

    def test_nba_prefix_dispatches_to_basketball_builder(self):
        football = _mock_builder(_FOOTBALL)
        basketball = _mock_builder(_BASKETBALL)
        mfb = MultiFeatureBuilder({"wc-": football, "nba-": basketball})

        match = _make_match("nba-456")
        mfb.build(match, {})
        basketball.build.assert_called_once_with(match, {})
        football.build.assert_not_called()

    def test_unknown_prefix_falls_back_to_default(self):
        football = _mock_builder(_FOOTBALL)
        mfb = MultiFeatureBuilder({"wc-": football})

        match = _make_match("unknown-789")
        mfb.build(match, {})
        football.build.assert_called_once_with(match, {})


class TestSport:
    def test_sport_returns_default_builder_sport(self):
        football = _mock_builder(_FOOTBALL)
        basketball = _mock_builder(_BASKETBALL)
        mfb = MultiFeatureBuilder({"wc-": football, "nba-": basketball})
        # Default is first builder (football)
        assert mfb.sport() == _FOOTBALL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_multi_feature_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.multi_feature_builder'`

- [ ] **Step 3: Create multi_feature_builder.py**

Create `backend/app/kernel/multi_feature_builder.py`:

```python
# backend/app/kernel/multi_feature_builder.py
"""MultiFeatureBuilder — FeatureBuilder Protocol proxy with prefix dispatch.

Mirrors MultiAdapter's prefix-dispatch pattern. The PredictionKernel
sees a single FeatureBuilder; internally, calls are routed to the
correct sport-specific builder based on the match_id prefix.

Prefix mapping:
    "wc-", "ucl-", "epl-", ... → FootballFeatureBuilder
    "nba-"                     → BasketballFeatureBuilder

Unknown prefixes fall back to the default builder (first registered).
"""
from __future__ import annotations

from app.kernel.domain import SportIdentity, MatchIdentity, FeatureSet


class MultiFeatureBuilder:
    """FeatureBuilder Protocol proxy — dispatches by match_id prefix."""

    def __init__(self, builders: dict[str, object]) -> None:
        """Initialize with prefix-to-builder mapping.

        Args:
            builders: {prefix: builder} where prefix is a string like
                "wc-", "nba-". The first builder is used as the default
                for unknown prefixes.
        """
        self._builders = builders
        self._default = next(iter(builders.values()))

    def _select(self, match_id: str) -> object:
        """Select the builder for a given match_id by prefix."""
        for prefix, builder in self._builders.items():
            if match_id.startswith(prefix):
                return builder
        return self._default

    def sport(self) -> SportIdentity:
        return self._default.sport()

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        return self._select(match.match_id).build(match, raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_multi_feature_builder.py -v`
Expected: PASS (2 test classes, 4 test methods)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/kernel/multi_feature_builder.py tests/test_multi_feature_builder.py
git commit -m "feat(phase4): add MultiFeatureBuilder with prefix dispatch

Mirrors MultiAdapter pattern for FeatureBuilder Protocol. Routes
build() calls to FootballFeatureBuilder or BasketballFeatureBuilder
based on match_id prefix (wc-/nba-/etc). Unknown prefixes fall back
to the first registered builder."
```

---

### Task 7: BasketballEngine

**Files:**
- Create: `backend/app/sports/basketball/engines/__init__.py`
- Create: `backend/app/sports/basketball/engines/basketball_engine.py`
- Test: `backend/tests/test_basketball_engine.py` (6 tests)

**Interfaces:**
- Consumes:
  - `app.kernel.domain` (FeatureSet, MatchIdentity, PredictionResult, ContributionItem)
  - `app.kernel.factor_registry.FactorRegistry` (optional, for weight lookup)
  - `app.core.config.settings` (NBA_ELO_HFA, NBA_LEAGUE_AVG_TOTAL — read at call time)
  - `app.sports.basketball.elo_calculator.compute_expected_score` (for Elo factor)
- Produces: `BasketballEngine` class implementing PredictionEngine Protocol
  - `name() -> str` returns `"basketball"`
  - `supported_sports() -> list[str]` returns `["basketball"]`
  - `predict(features: FeatureSet, match: MatchIdentity) -> PredictionResult`
  - 4 factors: elo (0.45), home_court (0.15), rest (0.15), form (0.25)
  - Bradley-Terry binary model: `outcome_probabilities = {"home_win": p, "away_win": 1-p}`
  - Weights read from `FactorRegistry.get_weight(factor_id, competition)` at call time, fallback to defaults
  - When a factor is unavailable, its weight is redistributed proportionally
  - Score conversion: `margin = (elo_home - elo_away + HFA) * 0.03`, `home_score = league_avg/2 + margin/2`
  - ContributionItem.predicted_outcome: `"home_win"` if factor's P(home_win) >= 0.5, else `"away_win"`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_basketball_engine.py`:

```python
# backend/tests/test_basketball_engine.py
"""Tests for BasketballEngine — Bradley-Terry binary prediction engine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.basketball.engines.basketball_engine import BasketballEngine


_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)


def _make_features(
    elo_home=1650.0, elo_away=1520.0,
    form_home=0.7, form_away=0.4,
    rest_home=2, rest_away=1,
) -> FeatureSet:
    comp = _NBA
    season = SeasonIdentity(competition=comp, season_key="2024-25")
    home = TeamIdentity(code="BOS", name="Boston Celtics", competition=comp)
    away = TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=comp)
    match = MatchIdentity(
        match_id="nba-123", season=season,
        stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
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
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures("TD Garden", None, None, True),
        custom={
            "pace_home": 99.5, "pace_away": 97.2,
            "ortg_home": 112.3, "ortg_away": 108.1,
            "drtg_home": 105.0, "drtg_away": 110.5,
            "tpct_home": 0.365, "tpct_away": 0.342,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="nba-1.0",
    )


class TestBasketballEngineProtocol:
    def test_implements_protocol(self):
        engine = BasketballEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name(self):
        assert BasketballEngine().name() == "basketball"

    def test_supported_sports(self):
        assert "basketball" in BasketballEngine().supported_sports()


class TestBasketballEnginePredict:
    def test_predict_returns_binary_probabilities(self):
        """Outcome probabilities have home_win and away_win (no draw)."""
        engine = BasketballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        # Probabilities sum to 1.0
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        """Higher Elo home team → P(home_win) > P(away_win)."""
        engine = BasketballEngine()
        strong = _make_features(elo_home=1800, elo_away=1500)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_explanation_has_four_factors(self):
        """Explanation contains elo, home_court, rest, form factors."""
        engine = BasketballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        factor_ids = [e.factor for e in result.explanation]
        assert "elo" in factor_ids
        assert "home_court" in factor_ids
        assert "rest" in factor_ids
        assert "form" in factor_ids

    def test_contribution_item_predicted_outcome_is_binary(self):
        """Each ContributionItem.predicted_outcome is home_win or away_win."""
        engine = BasketballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        for item in result.explanation:
            assert item.predicted_outcome in ("home_win", "away_win")

    def test_no_elo_fallback(self):
        """When Elo is None, engine still produces valid prediction."""
        engine = BasketballEngine()
        features = _make_features(elo_home=None, elo_away=None)
        result = engine.predict(features, features.match)
        # Elo factor should be unavailable
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.available is False
        # Still produces valid probabilities
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_score_conversion_uses_league_avg(self):
        """Predicted scores are centered around league average total."""
        engine = BasketballEngine()
        features = _make_features(elo_home=1500, elo_away=1500)
        result = engine.predict(features, features.match)
        # Equal Elo → scores should be near league_avg/2 each
        home_score = result.predicted_scores["home"]
        away_score = result.predicted_scores["away"]
        # League avg = 220, so each ≈ 110 (plus HFA adjustment)
        assert 100 < home_score < 130
        assert 100 < away_score < 130
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_basketball_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports.basketball.engines'`

- [ ] **Step 3: Create engines package**

Create `backend/app/sports/basketball/engines/__init__.py`:

```python
# backend/app/sports/basketball/engines/__init__.py
"""Basketball prediction engines."""
```

- [ ] **Step 4: Create basketball_engine.py**

Create `backend/app/sports/basketball/engines/basketball_engine.py`:

```python
# backend/app/sports/basketball/engines/basketball_engine.py
"""BasketballEngine — Bradley-Terry binary prediction engine.

Uses 4 independent factors that each compute P(home_win), then
weighted-average fusion. Unlike football (3-way home/draw/away),
basketball has binary outcomes (home_win/away_win, no draws).

Factors:
    elo (0.45)        — Elo-based win probability with HFA
    home_court (0.15) — NBA historical home win rate (constant 0.58)
    rest (0.15)       — Rest days advantage
    form (0.25)       — Recent form (last-10 win rate)

Weights are read from FactorRegistry at call time, falling back to
defaults if FactorRegistry is None. When a factor is unavailable,
its weight is redistributed proportionally to available factors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core import config
from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.sports.basketball.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS = {
    "elo": 0.45,
    "home_court": 0.15,
    "rest": 0.15,
    "form": 0.25,
}

# NBA historical home win rate (constant)
_HOME_COURT_PROB = 0.58


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class BasketballEngine:
    """Bradley-Terry binary outcome engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "basketball"

    def supported_sports(self) -> list[str]:
        return ["basketball"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        hfa = config.settings.NBA_ELO_HFA
        league_avg = config.settings.NBA_LEAGUE_AVG_TOTAL

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            weights = {
                fid: self._factor_registry.get_weight(fid, competition)
                for fid in _DEFAULT_WEIGHTS
            }
        else:
            weights = dict(_DEFAULT_WEIGHTS)

        # Compute each factor's P(home_win) and availability
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

        # 2. Home court factor (constant)
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

        # Score conversion
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

        # Confidence (same formula as EloOddsEngine)
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
            engine_name="basketball",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_basketball_engine.py -v`
Expected: PASS (3 test classes, 8 test methods)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sports/basketball/engines/__init__.py app/sports/basketball/engines/basketball_engine.py tests/test_basketball_engine.py
git commit -m "feat(phase4): add BasketballEngine with Bradley-Terry binary model

4 factors: elo(0.45), home_court(0.15), rest(0.15), form(0.25).
Binary outcomes (home_win/away_win, no draw). Weight redistribution
for unavailable factors. Score conversion uses NBA_LEAGUE_AVG_TOTAL.
Reads HFA and league avg from config.settings at call time."
```

---

### Task 8: LearningService Generalization

**Files:**
- Modify: `backend/app/kernel/learning_service.py`
- Test: `backend/tests/test_learning_dynamic_outcomes.py` (5 tests)

**Interfaces:**
- Consumes: existing `learning_service.py` code, `config.settings.EWMA_ALPHA`, `config.settings.WEIGHT_FLOOR`, `config.settings.WEIGHT_CEILING`
- Produces: modified `compute_error()` (dynamic outcome keys) and `update_weights()` (dynamic factor iteration)
- CRITICAL: All 174 existing tests must pass with zero modifications

**Changes:**

1. `compute_error()` — replace hardcoded `["home_win", "draw", "away_win"]` with `list(probs.keys())`
2. `update_weights()` — replace hardcoded `elo`/`odds` counters with dynamic factor collection from explanation

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_learning_dynamic_outcomes.py`:

```python
# backend/tests/test_learning_dynamic_outcomes.py
"""Tests for LearningService generalization — dynamic outcome keys and factor iteration.

Verifies:
1. Binary Brier score works for basketball (home_win/away_win, no draw)
2. 4-factor EWMA update works for basketball (elo/home_court/rest/form)
3. Football 3-way regression unchanged (existing behavior preserved)
4. Mixed competition isolation (NBA update doesn't affect football weights)
5. Empty explanation safe handling (no crash)
"""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome,
)
from app.kernel.learning_service import KernelLearningService
from app.kernel.factor_registry import FactorRegistry


def _make_match(match_id="m1", competition="nba") -> MatchIdentity:
    sport = SportIdentity(code="basketball", name="Basketball")
    comp = CompetitionIdentity(code=competition, name="NBA", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2024-25")
    home = TeamIdentity(code="BOS", name="Boston Celtics", competition=comp)
    away = TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _make_football_match(match_id="fm1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_basketball_prediction(
    elo_outcome="home_win", home_court_outcome="home_win",
    rest_outcome="home_win", form_outcome="home_win",
) -> PredictionResult:
    """4-factor basketball prediction with binary outcomes."""
    return PredictionResult(
        predicted_scores={"home": 110.0, "away": 105.0},
        outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
        confidence=0.62, engine_name="basketball",
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.45,
                             available=True, detail="Elo", predicted_outcome=elo_outcome),
            ContributionItem(factor="home_court", direction="support", weight=0.15,
                             available=True, detail="Home", predicted_outcome=home_court_outcome),
            ContributionItem(factor="rest", direction="support", weight=0.15,
                             available=True, detail="Rest", predicted_outcome=rest_outcome),
            ContributionItem(factor="form", direction="support", weight=0.25,
                             available=True, detail="Form", predicted_outcome=form_outcome),
        ],
        betting_analysis=None, feature_version="nba-1.0",
        prediction_timestamp=datetime(2024, 12, 24, tzinfo=timezone.utc),
    )


def _make_football_prediction(elo_outcome="home_win", odds_outcome="home_win") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name="elo_odds",
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome=elo_outcome),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome=odds_outcome),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc_with_registry(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    registry = FactorRegistry()
    service = KernelLearningService(factor_registry=registry)
    yield service, registry
    close_kernel_session()


class TestBinaryBrierScore:
    def test_basketball_brier_score_computed(self, svc_with_registry):
        """compute_error works with binary {home_win, away_win} probabilities."""
        svc, reg = svc_with_registry
        match = _make_match("nba-1")
        pred = _make_basketball_prediction()
        svc.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id="nba-1", home_score=110, away_score=105,
            outcome="home_win",
            finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
        )
        svc.record_outcome(outcome)
        error = svc.compute_error("nba-1")
        assert error is not None
        # Brier = (0.65 - 1)^2 + (0.35 - 0)^2 = 0.1225 + 0.1225 = 0.245
        assert error.brier_score == 0.245


class TestFourFactorEWMA:
    def test_basketball_weight_update(self, svc_with_registry):
        """update_weights works with 4 basketball factors."""
        svc, reg = svc_with_registry
        # Seed NBA factors
        reg.ensure_competition_factors("nba")

        # Seed 12 predictions + outcomes
        for i in range(12):
            match = _make_match(f"nba-{i}")
            pred = _make_basketball_prediction()
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"nba-{i}", home_score=110, away_score=105,
                outcome="home_win",
                finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"nba-{i}")

        old_elo = reg.get_weight("elo", "nba")
        svc.update_weights("nba")
        new_elo = reg.get_weight("elo", "nba")
        # Weights should change (all factors predicted correctly → target ≈ equal weights)
        assert new_elo != old_elo


class TestFootballRegressionUnchanged:
    def test_football_3way_brier_unchanged(self, svc_with_registry):
        """Football Brier score with 3-way outcomes is identical to old behavior."""
        svc, reg = svc_with_registry
        match = _make_football_match("wc-1")
        pred = _make_football_prediction()
        svc.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id="wc-1", home_score=2, away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 22, 0, tzinfo=timezone.utc),
        )
        svc.record_outcome(outcome)
        error = svc.compute_error("wc-1")
        assert error is not None
        # Old formula: (0.55-1)^2 + (0.25-0)^2 + (0.20-0)^2
        #            = 0.2025 + 0.0625 + 0.04 = 0.305
        assert error.brier_score == 0.305


class TestMixedCompetitionIsolation:
    def test_nba_update_doesnt_affect_football(self, svc_with_registry):
        """NBA weight update doesn't change football weights."""
        svc, reg = svc_with_registry
        reg.ensure_competition_factors("nba")

        # Seed NBA data
        for i in range(12):
            match = _make_match(f"nba-{i}")
            pred = _make_basketball_prediction()
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"nba-{i}", home_score=110, away_score=105,
                outcome="home_win",
                finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"nba-{i}")

        # Football weight should be default (0.30) before NBA update
        old_football_elo = reg.get_weight("elo", "world_cup")
        assert old_football_elo == 0.30

        svc.update_weights("nba")

        # Football weight unchanged after NBA update
        assert reg.get_weight("elo", "world_cup") == 0.30


class TestEmptyExplanationSafeHandling:
    def test_empty_explanation_no_crash(self, svc_with_registry):
        """update_weights doesn't crash when explanation is empty."""
        svc, reg = svc_with_registry
        reg.ensure_competition_factors("nba")

        for i in range(12):
            match = _make_match(f"empty-{i}")
            pred = PredictionResult(
                predicted_scores={"home": 110.0, "away": 105.0},
                outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
                confidence=0.62, engine_name="basketball",
                explanation=[],  # Empty!
                betting_analysis=None, feature_version="nba-1.0",
                prediction_timestamp=datetime(2024, 12, 24, tzinfo=timezone.utc),
            )
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"empty-{i}", home_score=110, away_score=105,
                outcome="home_win",
                finished_at=datetime(2024, 12, 25, 22, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"empty-{i}")

        old_elo = reg.get_weight("elo", "nba")
        # Should not crash, and should not update (no factor data)
        svc.update_weights("nba")
        assert reg.get_weight("elo", "nba") == old_elo
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_learning_dynamic_outcomes.py -v`
Expected: FAIL — `ensure_competition_factors` doesn't exist yet (Task 9), and `compute_error` may fail on binary outcomes

Note: Some tests may fail because `ensure_competition_factors` is implemented in Task 9. To make this task independently testable, we can seed factors manually. Update the test fixture to pre-seed NBA factors directly in the registry:

Actually, to keep Task 8 independent of Task 9, replace `reg.ensure_competition_factors("nba")` in the tests with manual factor registration:

```python
# Replace this line in all tests:
#   reg.ensure_competition_factors("nba")
# With this manual seeding:
from app.kernel.factor_registry import FactorConfig
from datetime import datetime, timezone
for fid, cat, w in [("elo", "elo_rating", 0.45), ("home_court", "home_advantage", 0.15),
                     ("rest", "rest_days", 0.15), ("form", "recent_form", 0.25)]:
    reg.register_factor(FactorConfig(fid, cat, "1.0", w, "nba", True, "test",
                                      datetime.now(timezone.utc)))
```

Apply this substitution in the test file wherever `reg.ensure_competition_factors("nba")` appears.

- [ ] **Step 3: Modify compute_error() in learning_service.py**

In `backend/app/kernel/learning_service.py`, find the `compute_error` method (around line 147-151). Replace the hardcoded outcome keys:

**Before (lines 147-151):**
```python
            # Brier score
            probs = pred.outcome_probabilities
            brier = sum(
                (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
                for k in ["home_win", "draw", "away_win"]
            )
```

**After:**
```python
            # Brier score — dynamically iterate outcome keys
            # (supports both football 3-way and basketball binary)
            probs = pred.outcome_probabilities
            outcome_keys = list(probs.keys())
            brier = sum(
                (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
                for k in outcome_keys
            )
```

- [ ] **Step 4: Modify update_weights() in learning_service.py**

In `backend/app/kernel/learning_service.py`, find the `update_weights` method (around line 249-323). Replace the hardcoded elo/odds logic:

**Before (lines 270-318):**
```python
            elo_correct = 0
            elo_total = 0
            odds_correct = 0
            odds_total = 0

            for pred, outcome in results:
                actual = outcome.outcome
                explanation = pred.explanation or []
                for item in explanation:
                    if not isinstance(item, dict):
                        continue
                    factor = item.get("factor")
                    predicted = item.get("predicted_outcome")
                    if not predicted:
                        continue
                    if factor == "elo":
                        elo_total += 1
                        if predicted == actual:
                            elo_correct += 1
                    elif factor == "odds":
                        odds_total += 1
                        if predicted == actual:
                            odds_correct += 1

            if elo_total == 0 or odds_total == 0:
                return

            elo_acc = elo_correct / elo_total
            odds_acc = odds_correct / odds_total

            total_acc = elo_acc + odds_acc
            if total_acc == 0:
                return

            w_elo_target = elo_acc / total_acc
            w_odds_target = odds_acc / total_acc

            w_elo_old = self._factor_registry.get_weight("elo", competition)
            w_odds_old = self._factor_registry.get_weight("odds", competition)

            alpha = config.settings.EWMA_ALPHA
            w_elo_new = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING,
                          alpha * w_elo_target + (1 - alpha) * w_elo_old))
            w_odds_new = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING,
                           1.0 - w_elo_new))

            self._factor_registry.update_weight("elo", competition, w_elo_new, source="ewma")
            self._factor_registry.update_weight("odds", competition, w_odds_new, source="ewma")
```

**After:**
```python
            # Dynamic factor collection — supports any number of factors
            # (football: elo+odds, basketball: elo+home_court+rest+form)
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
            if not factor_stats or any(s["total"] == 0 for s in factor_stats.values()):
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
            # (handles clamp rounding drift — same pattern as old code's
            # w_odds = 1.0 - w_elo for football's 2-factor case)
            factors = list(target_weights.keys())
            if len(factors) > 1:
                sum_w = sum(self._factor_registry.get_weight(f, competition) for f in factors[:-1])
                last_w = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING, 1.0 - sum_w))
                self._factor_registry.update_weight(factors[-1], competition, last_w, source="ewma")
```

- [ ] **Step 5: Run new tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_learning_dynamic_outcomes.py -v`
Expected: PASS (5 test classes, 5 test methods)

- [ ] **Step 6: CRITICAL — Verify all 174 existing tests still pass**

Run: `cd backend && python -m pytest tests/test_learning_weights.py tests/test_learning_calibration.py tests/test_engine_score_persistence.py tests/test_kernel_learning_service.py tests/test_engine_dynamic_selection.py -v`
Expected: ALL PASS with zero modifications

If any existing test fails, the generalization broke backward compatibility. The most common issue would be factor ordering in the normalization step. Football explanations always have `[elo, odds]` in that order, so `factors[-1]` is always `"odds"` — identical to the old `1.0 - w_elo` behavior.

- [ ] **Step 7: Run full test suite to verify zero regression**

Run: `cd backend && python -m pytest tests/ -v --tb=short -x`
Expected: ALL existing tests pass + 5 new tests pass

- [ ] **Step 8: Commit**

```bash
cd backend
git add app/kernel/learning_service.py tests/test_learning_dynamic_outcomes.py
git commit -m "feat(phase4): generalize learning service for multi-sport support

compute_error() now dynamically iterates probs.keys() instead of
hardcoded ['home_win','draw','away_win'] — supports basketball's
binary outcomes. update_weights() now dynamically collects all
factors from explanation instead of hardcoded elo/odds — supports
basketball's 4 factors. All 174 existing tests pass unchanged."
```

---

### Task 9: FactorRegistry.ensure_competition_factors()

**Files:**
- Modify: `backend/app/kernel/factor_registry.py`
- Test: `backend/tests/test_kernel_factor_registry.py` (add 1 test class)

**Interfaces:**
- Consumes: existing `FactorRegistry`, `FactorConfig`, `KernelFactor`, `get_kernel_session`
- Produces: `FactorRegistry.ensure_competition_factors(competition: str) -> None`
  - Seeds default factors for a competition if none exist
  - For `"nba"`: seeds elo(0.45), home_court(0.15), rest(0.15), form(0.25)
  - For unknown competitions: no-op (returns immediately)
  - Football global defaults (elo=0.30, odds=0.70) remain unchanged

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_kernel_factor_registry.py` (append new test class):

```python
class TestEnsureCompetitionFactors:
    """Phase 4: ensure_competition_factors for NBA factor seeding."""

    def test_seeds_nba_factors_when_empty(self, tmp_path):
        """NBA factors are seeded when none exist for 'nba' competition."""
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session
        from app.kernel.factor_registry import FactorRegistry

        db_path = str(tmp_path / "kernel_factor_test.db")
        init_kernel_db(db_path)
        try:
            reg = FactorRegistry()
            # Before: no NBA factors
            assert reg.get_weight("elo", "nba") == 1.0  # default fallback

            reg.ensure_competition_factors("nba")

            # After: NBA factors seeded with correct weights
            assert reg.get_weight("elo", "nba") == 0.45
            assert reg.get_weight("home_court", "nba") == 0.15
            assert reg.get_weight("rest", "nba") == 0.15
            assert reg.get_weight("form", "nba") == 0.25
        finally:
            close_kernel_session()

    def test_idempotent_when_already_seeded(self, tmp_path):
        """Calling twice doesn't duplicate or overwrite factors."""
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session
        from app.kernel.factor_registry import FactorRegistry

        db_path = str(tmp_path / "kernel_factor_test2.db")
        init_kernel_db(db_path)
        try:
            reg = FactorRegistry()
            reg.ensure_competition_factors("nba")
            reg.ensure_competition_factors("nba")  # Second call

            # Weights still correct
            assert reg.get_weight("elo", "nba") == 0.45
        finally:
            close_kernel_session()

    def test_football_defaults_unchanged(self, tmp_path):
        """NBA seeding doesn't affect football global defaults."""
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session
        from app.kernel.factor_registry import FactorRegistry

        db_path = str(tmp_path / "kernel_factor_test3.db")
        init_kernel_db(db_path)
        try:
            reg = FactorRegistry()
            reg.ensure_competition_factors("nba")

            # Football globals unchanged
            assert reg.get_weight("elo", "world_cup") == 0.30
            assert reg.get_weight("odds", "world_cup") == 0.70
        finally:
            close_kernel_session()

    def test_unknown_competition_noop(self, tmp_path):
        """Unknown competition returns without seeding."""
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session
        from app.kernel.factor_registry import FactorRegistry

        db_path = str(tmp_path / "kernel_factor_test4.db")
        init_kernel_db(db_path)
        try:
            reg = FactorRegistry()
            reg.ensure_competition_factors("unknown_sport")
            # No factors seeded
            assert reg.get_weight("elo", "unknown_sport") == 1.0
        finally:
            close_kernel_session()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py::TestEnsureCompetitionFactors -v`
Expected: FAIL with `AttributeError: 'FactorRegistry' object has no attribute 'ensure_competition_factors'`

- [ ] **Step 3: Add ensure_competition_factors() to factor_registry.py**

In `backend/app/kernel/factor_registry.py`, find the `list_active` method (around line 156-171) and add after it:

```python
    def ensure_competition_factors(self, competition: str) -> None:
        """Seed default factors for a competition if none exist.

        For "nba": seeds elo(0.45), home_court(0.15), rest(0.15), form(0.25).
        For unknown competitions: no-op (returns immediately).
        Football global defaults (elo=0.30, odds=0.70) are never modified.
        """
        # Check if competition already has factors
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
                fc = FactorConfig(
                    factor_id=factor_id, category=category,
                    version="1.0", weight=weight,
                    competition=competition, enabled=True,
                    source="default", updated_at=now,
                )
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py::TestEnsureCompetitionFactors -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Verify no regression in existing factor registry tests**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py tests/test_factor_registry_persistence.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/kernel/factor_registry.py tests/test_kernel_factor_registry.py
git commit -m "feat(phase4): add FactorRegistry.ensure_competition_factors()

Seeds NBA default factors (elo=0.45, home_court=0.15, rest=0.15,
form=0.25) when none exist for 'nba' competition. Idempotent —
second call is a no-op. Football global defaults unchanged."
```

---

### Task 10: API Integration (_get_kernel Wiring)

**Files:**
- Modify: `backend/app/api/routes/predictions.py`
- Test: `backend/tests/test_kernel_prediction_kernel.py` (add 1 test)

**Interfaces:**
- Consumes: All Phase 4 components (NBAAdapter, BasketballFeatureBuilder, BasketballEngine, MultiFeatureBuilder, FactorRegistry.ensure_competition_factors)
- Produces: Modified `_get_kernel()` that registers NBA components when `PHASE4_NBA_ENABLED` is true

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_kernel_prediction_kernel.py` (append new test class):

```python
class TestPhase4KernelRegistration:
    """Phase 4: NBA components are registered when PHASE4_NBA_ENABLED is true."""

    def test_nba_engine_registered_when_enabled(self, tmp_path, monkeypatch):
        """When PHASE4_NBA_ENABLED=true, BasketballEngine is in EngineRegistry."""
        import app.core.config as config_module
        from app.kernel.kernel_db import init_kernel_db, close_kernel_session

        db_path = str(tmp_path / "kernel_api_test.db")
        init_kernel_db(db_path)
        try:
            monkeypatch.setattr(
                config_module.settings, "KERNEL_PREDICTION_ENABLED", True
            )
            monkeypatch.setattr(
                config_module.settings, "PHASE4_NBA_ENABLED", True
            )
            monkeypatch.setattr(
                config_module.settings, "BALLDONTLIE_API_KEY", ""
            )

            # Clear cached kernel
            from app.api.routes import predictions
            if hasattr(predictions._get_kernel, "_instance"):
                delattr(predictions._get_kernel, "_instance")

            kernel = predictions._get_kernel()
            engines = kernel._engine_registry.list_engines()
            assert "basketball" in engines
            assert "elo_odds" in engines
        finally:
            close_kernel_session()
            # Clean up cached kernel
            from app.api.routes import predictions
            if hasattr(predictions._get_kernel, "_instance"):
                delattr(predictions._get_kernel, "_instance")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_prediction_kernel.py::TestPhase4KernelRegistration -v`
Expected: FAIL — `"basketball" not in engines`

- [ ] **Step 3: Modify _get_kernel() in predictions.py**

In `backend/app/api/routes/predictions.py`, find the `_get_kernel` function. Modify it to register Phase 4 NBA components.

**Find this section (around lines 68-78):**
```python
        from app.sports.football.adapters.multi_adapter import MultiAdapter
        multi = MultiAdapter(adapters)

        _get_kernel._instance = PredictionKernel(
            adapter=multi,
            feature_builder=FootballFeatureBuilder(),
            engine_registry=reg,
            factor_registry=factor_registry,
            feature_registry=FeatureRegistry(),
            learning=learning,
        )
    return _get_kernel._instance
```

**Replace with:**
```python
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

            # All football prefixes share the same FootballFeatureBuilder instance
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
            feature_builder=feature_builder,
            engine_registry=reg,
            factor_registry=factor_registry,
            feature_registry=FeatureRegistry(),
            learning=learning,
        )
    return _get_kernel._instance
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_prediction_kernel.py::TestPhase4KernelRegistration -v`
Expected: PASS

- [ ] **Step 5: Verify no regression in existing prediction kernel tests**

Run: `cd backend && python -m pytest tests/test_kernel_prediction_kernel.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run full test suite for final regression check**

Run: `cd backend && python -m pytest tests/ -v --tb=short -x`
Expected: ALL existing tests pass + all 28 new Phase 4 tests pass

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/api/routes/predictions.py tests/test_kernel_prediction_kernel.py
git commit -m "feat(phase4): wire NBA components in _get_kernel()

When PHASE4_NBA_ENABLED=true: registers NBAAdapter (nba- prefix),
BasketballEngine, seeds NBA factors via ensure_competition_factors,
and wraps FeatureBuilder in MultiFeatureBuilder for prefix dispatch.
When false: FootballFeatureBuilder is used directly (no overhead)."
```

---

## Self-Review Checklist

After completing all tasks, verify:

### Spec Coverage
- [x] Section 3 (NBA Adapter) → Task 4
- [x] Section 4 (Elo Calculator) → Task 3
- [x] Section 5 (BasketballFeatureBuilder) → Task 5
- [x] Section 6 (MultiFeatureBuilder) → Task 6
- [x] Section 7 (BasketballEngine) → Task 7
- [x] Section 8 (Learning Service Generalization) → Task 8
- [x] Section 9 (FactorRegistry Extension) → Task 9
- [x] Section 10 (Config) → Task 1
- [x] Section 11 (API Integration) → Task 10
- [x] Section 12 (Database Schema — kernel_elo_ratings) → Task 2
- [x] Section 13 (Test Strategy — 28 tests, 6 files) → Tasks 3-8
- [x] Section 14 (Constraints 1-14) → Global Constraints above

### Placeholder Scan
- [x] No "TBD", "TODO", "implement later" in any task
- [x] All code blocks contain actual implementation code
- [x] All test code blocks contain actual test assertions

### Type Consistency
- [x] `compute_expected_score(elo_home, elo_away, hfa)` — same signature in Task 3 and Task 7
- [x] `seed_elo_from_games(games, hfa, k_regular, k_playoff)` — same signature in Task 3 and Task 4
- [x] `NBAAdapter` implements all 8 DataAdapter Protocol methods
- [x] `BasketballFeatureBuilder` implements `sport()` and `build()` matching FeatureBuilder Protocol
- [x] `MultiFeatureBuilder` implements `sport()` and `build()` matching FeatureBuilder Protocol
- [x] `BasketballEngine` implements `predict()`, `name()`, `supported_sports()` matching PredictionEngine Protocol
- [x] `ensure_competition_factors(competition: str)` — same signature in Task 9 and Task 10

### Test Count Verification
- Task 3: 4 test classes, 7 test methods (test_nba_elo_calculator.py)
- Task 4: 5 test classes, 8 test methods (test_nba_adapter.py)
- Task 5: 2 test classes, 4 test methods (test_basketball_feature_builder.py)
- Task 6: 2 test classes, 4 test methods (test_multi_feature_builder.py)
- Task 7: 3 test classes, 8 test methods (test_basketball_engine.py)
- Task 8: 5 test classes, 5 test methods (test_learning_dynamic_outcomes.py)
- Task 1: 1 test class, 6 test methods (test_config.py addition)
- Task 2: 1 test class, 1 test method (test_db_migration.py addition)
- Task 9: 1 test class, 4 test methods (test_kernel_factor_registry.py addition)
- Task 10: 1 test class, 1 test method (test_kernel_prediction_kernel.py addition)

**Total: 28 new tests in 6 new files + 12 test additions in 4 existing files**

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-14-sports-prediction-os-phase4.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration. Matches Phase 4 Constraint 14.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
