# MLB Park Factor Deepening (P1-M2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the static MLB runs park-factor table from ~15 teams to full 30-franchise coverage so every home club injects a non-silent `custom.park_factor` for BaseballEngine soft fusion.

**Architecture:** Keep the existing in-adapter static map `_PARK_FACTORS` and lookup `_park_factor_for_team`. Replace the partial map with a complete 30-team (+ Athletics alias) table of multi-year-ish runs factors (`1.0` = league average). No engine formula changes, no new modules, no network.

**Tech Stack:** Python 3.12+, pytest. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-mlb-park-factor-deepening-design.md`
- Do **not** change BaseballEngine park formula `(pf-1)*0.25` or clamp `±0.04` or weight `0.07`
- Do **not** add HR / L-R platoon park splits or season-dynamic API factors
- Do **not** extract a new park module (Option A: in-place table only)
- `custom.park_factor` remains a single float
- Values roughly in `[0.90, 1.20]`; `1.0` = league average
- Unknown / empty name still returns `1.0`
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Python runner for this machine: `C:\Python314\python.exe` with `PYTHONPATH=backend` when default `python` lacks deps

## File Structure

### Modified files
1. `backend/app/sports/baseball/mlb_adapter.py` — expand `_PARK_FACTORS` to 30 franchises + aliases
2. `backend/tests/test_mlb_adapter.py` — coverage / range / direction tests for park table
3. `CHANGELOG.md` — Unreleased note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-M2 status update

### Unchanged (verify only)
1. `backend/app/sports/baseball/engines/baseball_engine.py` — park soft path
2. `backend/tests/test_sport_factors_travel_park.py` — existing engine park test stays green

---

### Task 1: Failing park coverage tests

**Files:**
- Modify: `backend/tests/test_mlb_adapter.py`
- (No production code yet)

**Interfaces:**
- Consumes: `app.sports.baseball.mlb_adapter._PARK_FACTORS`, `_park_factor_for_team`, `_MLB_TEAM_IDS`
- Produces: failing tests that define the acceptance contract for Task 2

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mlb_adapter.py`:

```python
from app.sports.baseball.mlb_adapter import (
    MLBAdapter,
    _MLB_TEAM_IDS,
    _PARK_FACTORS,
    _park_factor_for_team,
    parse_mlb_game,
)


# Primary franchise names: unique team-id keys excluding pure aliases that
# share an id with another canonical name. Athletics is primary; Oakland is alias.
_PRIMARY_FRANCHISES = sorted(
    {name for name, tid in _MLB_TEAM_IDS.items() if name != "Oakland Athletics"},
    key=str,
)


class TestParkFactors:
    def test_primary_franchises_are_thirty(self):
        assert len(_PRIMARY_FRANCHISES) == 30

    def test_every_primary_franchise_has_explicit_park_key(self):
        missing = [n for n in _PRIMARY_FRANCHISES if n not in _PARK_FACTORS]
        assert missing == [], f"missing park factors: {missing}"

    def test_athletics_alias_matches_primary(self):
        assert "Athletics" in _PARK_FACTORS
        assert "Oakland Athletics" in _PARK_FACTORS
        assert _PARK_FACTORS["Athletics"] == _PARK_FACTORS["Oakland Athletics"]

    def test_all_primary_values_in_range(self):
        for name in _PRIMARY_FRANCHISES:
            pf = _PARK_FACTORS[name]
            assert 0.90 <= pf <= 1.20, f"{name}={pf} out of range"

    def test_coors_highest_or_tied(self):
        coors = _PARK_FACTORS["Colorado Rockies"]
        assert coors == max(_PARK_FACTORS[n] for n in _PRIMARY_FRANCHISES)

    def test_low_run_parks_below_neutral(self):
        for name in (
            "Miami Marlins",
            "Seattle Mariners",
            "San Francisco Giants",
            "San Diego Padres",
        ):
            assert _PARK_FACTORS[name] < 1.0, name

    def test_lookup_exact_and_empty_default(self):
        assert _park_factor_for_team("Colorado Rockies") == _PARK_FACTORS["Colorado Rockies"]
        assert _park_factor_for_team("") == 1.0
        assert _park_factor_for_team("Totally Fake FC") == 1.0
```

If imports of `_PARK_FACTORS` / `_MLB_TEAM_IDS` / `_park_factor_for_team` are not already present at the top of the test file, add them to the existing `from app.sports.baseball.mlb_adapter import ...` line (or keep the class-local import as shown). Prefer a single top-level import to match file style.

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_mlb_adapter.py::TestParkFactors -q --tb=short
```

Expected: FAIL — `test_every_primary_franchise_has_explicit_park_key` lists ~15 missing names (or `len(_PRIMARY_FRANCHISES)` still 30 but missing keys).

- [ ] **Step 3: Commit tests only**

```powershell
git add backend/tests/test_mlb_adapter.py
git commit -m "test(mlb): require full 30-team park factor coverage (P1-M2)"
```

---

### Task 2: Expand `_PARK_FACTORS` to 30 franchises

**Files:**
- Modify: `backend/app/sports/baseball/mlb_adapter.py` (replace `_PARK_FACTORS` block ~L91–108)

**Interfaces:**
- Consumes: none new
- Produces: complete `_PARK_FACTORS` map used by `_park_factor_for_team` → `custom.park_factor`

- [ ] **Step 1: Replace the partial table with full coverage**

Replace the existing `_PARK_FACTORS` dict with the following (public-consensus multi-year-ish **runs** factors; soft signal only). Keep comment style consistent:

```python
# Static multi-year-ish park run factors (1.0 = league average). Soft signal only.
# Expanded to all 30 franchises (P1-M2). Alias keys mirror _MLB_TEAM_IDS dual names.
_PARK_FACTORS: dict[str, float] = {
    "Arizona Diamondbacks": 1.02,
    "Athletics": 0.97,
    "Atlanta Braves": 1.01,
    "Baltimore Orioles": 1.01,
    "Boston Red Sox": 1.06,
    "Chicago Cubs": 1.02,
    "Chicago White Sox": 1.00,
    "Cincinnati Reds": 1.05,
    "Cleveland Guardians": 0.99,
    "Colorado Rockies": 1.15,
    "Detroit Tigers": 0.98,
    "Houston Astros": 0.99,
    "Kansas City Royals": 1.01,
    "Los Angeles Angels": 1.00,
    "Los Angeles Dodgers": 0.98,
    "Miami Marlins": 0.93,
    "Milwaukee Brewers": 1.01,
    "Minnesota Twins": 1.01,
    "New York Mets": 0.97,
    "New York Yankees": 1.01,
    "Oakland Athletics": 0.97,
    "Philadelphia Phillies": 1.03,
    "Pittsburgh Pirates": 0.98,
    "San Diego Padres": 0.96,
    "San Francisco Giants": 0.94,
    "Seattle Mariners": 0.94,
    "St. Louis Cardinals": 0.97,
    "Tampa Bay Rays": 0.96,
    "Texas Rangers": 1.04,
    "Toronto Blue Jays": 1.02,
    "Washington Nationals": 1.00,
}
```

Notes for the implementer:
- Do **not** change `_park_factor_for_team` body unless a bug is proven.
- Athletics / Oakland Athletics must stay equal.
- Coors must remain the unique maximum among primary franchises (1.15).
- Existing refined extremes (Marlins 0.93, Giants/Mariners 0.94, Fenway 1.06, GABP 1.05, Globe Life 1.04) are preserved.

- [ ] **Step 2: Run park tests — expect PASS**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_mlb_adapter.py::TestParkFactors -q --tb=short
```

Expected: PASS (all TestParkFactors).

- [ ] **Step 3: Run related regression suite**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_mlb_adapter.py tests/test_sport_factors_travel_park.py -q --tb=short
```

Expected: all PASS (engine park soft behavior unchanged).

- [ ] **Step 4: Commit implementation**

```powershell
git add backend/app/sports/baseball/mlb_adapter.py
git commit -m "feat(mlb): full 30-team static park run factors (P1-M2)"
```

---

### Task 3: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md` (Unreleased section top)
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` (P1-M2 row)

**Interfaces:**
- Consumes: Task 2 behavior
- Produces: operator-visible status

- [ ] **Step 1: Update CHANGELOG**

Insert under `## Unreleased` (above older MLB entries):

```markdown
### MLB full 30-team static park factors (P1-M2)
- Expand `_PARK_FACTORS` to all 30 franchises (+ Athletics alias)
- Runs-only soft signal; engine park formula/weight unchanged
- Coverage + range + Coors/low-park direction unit tests
```

- [ ] **Step 2: Update backlog row**

Replace the P1-M2 line in the MLB table with:

```markdown
| P1-M2 | ✅ 2026-07-24：30 队静态 runs `park_factor`（+ Athletics 别名）+ BaseballEngine `park` soft；HR/L-R/动态源仍待 |
```

- [ ] **Step 3: Commit docs**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs: mark P1-M2 full static 30-team park factors"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| 30 franchise static runs PF | Task 2 |
| Athletics aliases | Task 2 + Task 1 tests |
| Lookup / inject / engine unchanged | Task 2 (no engine edit) + regression in Task 2 Step 3 |
| Values ~0.90–1.20 | Task 1 range test + Task 2 values |
| Coverage + direction tests | Task 1 |
| CHANGELOG + backlog | Task 3 |
| No HR / L-R / dynamic API | Global constraints; no task adds them |

Placeholder scan: none. Type consistency: single float `park_factor` throughout.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-24-mlb-park-factor-deepening.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — same session with executing-plans, batch + checkpoints  

Which approach?
