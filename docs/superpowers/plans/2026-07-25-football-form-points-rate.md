# Football Form Points Rate (P1-F1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write football `team.form_home/away` as points rate `(3W+D)/(3N)` instead of win rate `W/N`, so MultiFactor form reflects draw-aware league table semantics without changing engine math.

**Architecture:** Pure helper `points_form_rate` lives in `club_form.py` (football form surface). Single write site in `enrich_situational_features` converts W/D/played from either historical CSV or kernel club form into `form_*`. FeatureBuilder and MultiFactor stay untouched. US-sport `rest_form` stays win-rate.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-football-form-points-rate-design.md`
- Formula (exact): `form = (3*W + D) / (3*N)` when `N > 0`, else `None` (omit key)
- Coerce non-negative ints for W, D, N; clamp result to `[0, 1]`; round to 4 decimals
- Single write site: `enrich_situational_features` only
- Do **not** change MultiFactor form weight, `* 0.25`, or clamps
- Do **not** change `rest_form.py` or NBA/MLB/NHL form
- Do **not** change `get_historical_team_stats` / `team_form_from_kernel` return shapes
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`

## File Structure

### Modified files
1. `backend/app/sports/football/club_form.py` — add pure `points_form_rate`
2. `backend/tests/test_club_form.py` — unit tests for `points_form_rate`
3. `backend/app/sports/football/adapters/_shared.py` — form write uses `points_form_rate` (+ use real `draws` / `played`)
4. `backend/tests/test_adapter_shared.py` — update `test_enrich_form_and_h2h` expectation; optional dedicated points-rate assert
5. `CHANGELOG.md` — Unreleased P1-F1 note
6. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F1 status line

### Unchanged (verify only)
1. `backend/app/sports/football/feature_builder.py` — form passthrough
2. `backend/app/sports/football/engines/football_multi_factor_engine.py` — form soft path
3. `backend/app/sports/_shared/rest_form.py` — US sports win-rate form
4. `backend/tests/test_rest_form.py` — must stay green without edits

---

### Task 1: `points_form_rate` unit tests (RED)

**Files:**
- Modify: `backend/tests/test_club_form.py`
- (No production code yet — or only if import fails; expect ImportError / AttributeError)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.club_form.points_form_rate`
- Produces: failing tests that define pure API for Task 2

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_club_form.py` (keep existing kernel tests):

```python
import pytest

from app.sports.football.club_form import points_form_rate


class TestPointsFormRate:
    def test_all_wins(self):
        assert points_form_rate(10, 0, 10) == pytest.approx(1.0)

    def test_all_draws(self):
        assert points_form_rate(0, 10, 10) == pytest.approx(0.3333)

    def test_all_losses(self):
        assert points_form_rate(0, 0, 10) == pytest.approx(0.0)

    def test_mixed_w1_d1_n2(self):
        # (3*1 + 1) / (3*2) = 4/6
        assert points_form_rate(1, 1, 2) == pytest.approx(0.6667)

    def test_existing_enrich_fixture_shape(self):
        # wins=6, draws=2, played=10 → 20/30
        assert points_form_rate(6, 2, 10) == pytest.approx(0.6667)

    def test_n_zero_returns_none(self):
        assert points_form_rate(0, 0, 0) is None

    def test_negative_n_returns_none(self):
        assert points_form_rate(1, 0, -1) is None

    def test_none_ish_coercion(self):
        assert points_form_rate(None, None, 5) == pytest.approx(0.0)
        assert points_form_rate(2, None, 4) == pytest.approx(0.5)

    def test_dirty_over_points_clamped(self):
        # W+D > N would exceed 1.0 without clamp
        assert points_form_rate(10, 10, 5) == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_club_form.py::TestPointsFormRate -v
```

Expected: FAIL with `ImportError` or `AttributeError: module ... has no attribute 'points_form_rate'`.

- [ ] **Step 3: Commit failing tests**

```powershell
git add backend/tests/test_club_form.py
git commit -m "test(football): failing P1-F1 points_form_rate unit tests"
```

---

### Task 2: Implement `points_form_rate` (GREEN)

**Files:**
- Modify: `backend/app/sports/football/club_form.py`
- Test: `backend/tests/test_club_form.py`

**Interfaces:**
- Consumes: Task 1 test contract
- Produces:

```python
def points_form_rate(
    wins: int | float | None,
    draws: int | float | None,
    played: int | float | None,
) -> float | None:
    """Football points rate in [0, 1]: (3W+D)/(3N). None if N <= 0."""
```

- [ ] **Step 1: Implement helper in `club_form.py`**

Add near the top of the module (after imports / before or after `_normalize`), full function:

```python
def points_form_rate(
    wins: int | float | None,
    draws: int | float | None,
    played: int | float | None,
) -> float | None:
    """Football points rate in [0, 1]: (3W + D) / (3N). None if N <= 0."""
    try:
        n = int(played or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        w = max(0, int(wins or 0))
    except (TypeError, ValueError):
        w = 0
    try:
        d = max(0, int(draws or 0))
    except (TypeError, ValueError):
        d = 0
    rate = (3 * w + d) / (3 * n)
    if rate < 0.0:
        rate = 0.0
    elif rate > 1.0:
        rate = 1.0
    return round(rate, 4)
```

Do **not** change `team_form_from_kernel` body in this task.

- [ ] **Step 2: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_club_form.py -v
```

Expected: all PASS (including existing kernel form tests).

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/club_form.py backend/tests/test_club_form.py
git commit -m "feat(football): points_form_rate helper (P1-F1)"
```

---

### Task 3: Wire enrich write site + update adapter tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (form blocks ~323–346)
- Modify: `backend/tests/test_adapter_shared.py` (`test_enrich_form_and_h2h`)

**Interfaces:**
- Consumes: `points_form_rate(wins, draws, played) -> float | None`
- Produces: `raw["team"]["form_home"]` / `form_away` as points rate when stats present

**Important behavior change vs today:**

Today:

```python
played = max(int(home_stats.get("played") or 0), 1)
wins = int(home_stats.get("wins") or 0)
raw["team"]["form_home"] = round(wins / played, 4)
```

`max(..., 1)` forced a denominator so win-rate never divided by zero. With points rate, **`played == 0` must omit form** (helper returns None). Rest / xG blocks still use stats when present.

- [ ] **Step 1: Update failing expectation first (RED on full path)**

In `test_enrich_form_and_h2h` (`backend/tests/test_adapter_shared.py`), change:

```python
assert raw["team"]["form_home"] == 0.6
assert raw["team"]["form_away"] == 0.6
```

to:

```python
# points rate: (3*6 + 2) / (3*10) = 0.6667  (old win rate was 0.6)
assert raw["team"]["form_home"] == pytest.approx(0.6667)
assert raw["team"]["form_away"] == pytest.approx(0.6667)
```

Ensure `pytest` is imported at top of file (already used elsewhere in this file).

- [ ] **Step 2: Run adapter form test — expect FAIL**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestFetchEloAndOdds::test_enrich_form_and_h2h -v
```

Expected: FAIL with `0.6 != 0.6667` (still writing win rate).

- [ ] **Step 3: Implement enrich write site**

In `backend/app/sports/football/adapters/_shared.py`, at top of file or inside the form blocks, use:

```python
from app.sports.football.club_form import points_form_rate
```

Prefer import **inside** the existing enrich function near the form writes (keeps module load light / matches local-import style for club_form), or hoist next to other football imports if already top-level — follow local style of this file (club_form is already imported inside the function for kernel fallback).

Replace home form block:

```python
    if home_stats:
        played = int(home_stats.get("played") or 0)
        wins = int(home_stats.get("wins") or 0)
        draws = int(home_stats.get("draws") or 0)
        form_h = points_form_rate(wins, draws, played)
        if form_h is not None:
            raw["team"]["form_home"] = form_h
        last = home_stats.get("last_match_date")
        rest = _days_since(last, before)
        if rest is not None:
            raw["general"]["rest_days_home"] = rest
            raw["general"]["days_since_last_match"] = rest
        gpg = home_stats.get("goals_per_game")
        if gpg is not None:
            raw.setdefault("custom", {})["xg_home"] = float(gpg)
```

Replace away form block:

```python
    if away_stats:
        played = int(away_stats.get("played") or 0)
        wins = int(away_stats.get("wins") or 0)
        draws = int(away_stats.get("draws") or 0)
        form_a = points_form_rate(wins, draws, played)
        if form_a is not None:
            raw["team"]["form_away"] = form_a
        last = away_stats.get("last_match_date")
        rest = _days_since(last, before)
        if rest is not None:
            raw["general"]["rest_days_away"] = rest
        gpg = away_stats.get("goals_per_game")
        if gpg is not None:
            raw.setdefault("custom", {})["xg_away"] = float(gpg)
```

Notes:
- Import `points_form_rate` once in the function (can share the `club_form` import block used for `team_form_from_kernel`, or a small dedicated try/import — do **not** swallow form write failures silently beyond existing stats acquisition errors).
- If `points_form_rate` import is unconditional at module level, that is also fine; prefer consistency with file.

- [ ] **Step 4: Run focused tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_club_form.py tests/test_adapter_shared.py -v --tb=short
```

Expected: PASS.

- [ ] **Step 5: Smoke multi-factor + rest_form (no edits)**

```powershell
C:\Python314\python.exe -m pytest tests/test_football_multi_factor_engine.py tests/test_rest_form.py -v --tb=short
```

Expected: PASS without file changes.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): write form_* as points rate (P1-F1)"
```

---

### Task 4: Docs + backlog (CHANGELOG / OPPORTUNITY)

**Files:**
- Modify: `CHANGELOG.md` (Unreleased section top)
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` (P1-F1 row)

**Interfaces:**
- Consumes: implemented behavior from Tasks 2–3
- Produces: documented status only

- [ ] **Step 1: CHANGELOG**

Under `## Unreleased`, add **above** other football entries if present:

```markdown
### Football form points rate (P1-F1)
- `points_form_rate`: form_* = (3W+D)/(3N) in [0,1] when played > 0
- Adapter enrich single write site; historical CSV + club kernel both benefit
- MultiFactor form weight/formula unchanged; US-sport rest_form stays win-rate
```

- [ ] **Step 2: Backlog P1-F1 row**

Replace the P1-F1 status cell content with something equivalent to:

```markdown
| P1-F1 | form（近 N 场） | ✅ 部分 2026-07-25：`form_*` = 积分率 (3W+D)/(3N)（historical + club_form 经 enrich 统一写入）；加权近 N / 覆盖率与别名仍待 | 引擎 form 差分未改 |
```

Keep table alignment / surrounding rows intact.

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F1 points-rate form changelog + backlog"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| `points_form_rate` pure helper | Task 1–2 |
| Formula (3W+D)/(3N), None if N≤0, clamp, round 4dp | Task 2 |
| Single write site in enrich | Task 3 |
| Both historical + club kernel via same write | Task 3 (no data-source formula change) |
| Update adapter test 0.6 → 0.6667 | Task 3 |
| MultiFactor / FeatureBuilder / rest_form unchanged | Task 3 smoke |
| CHANGELOG + backlog | Task 4 |
| No live API / flag / schema | Global constraints |

Placeholder scan: none.  
Type consistency: `points_form_rate(wins, draws, played) -> float | None` used in Tasks 1–3.
