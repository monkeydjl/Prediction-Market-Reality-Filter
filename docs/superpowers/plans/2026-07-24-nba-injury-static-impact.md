# NBA Static Injury Impact (P1-B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire real `injury_impact_home/away` scalars from a code-local static Out list (role-weighted) into the NBA adapter and feature builder so BasketballEngine soft `injury` can become available without changing engine math.

**Architecture:** New pure module `nba_injury.py` owns role weights, static Out table, `summarize_injury_impact`, and `injury_impact_for_team`. `NBAAdapter.fetch_all_data` dual-writes non-null impacts into `player` and `custom`. `BasketballFeatureBuilder` passthroughs `player_raw` injury fields. Engine and factor registry stay untouched.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-nba-injury-static-impact-design.md`
- Status filter: **Out only** (case-insensitive); Doubtful/Questionable ignored
- Role weights (exact): star `0.35`, starter `0.18`, rotation `0.08`, bench `0.03`; unknown role → bench
- Multiple Outs: sum weights, clamp to `[0, 1]`
- Missing team / empty / no Out rows → **`None`** (never write `0.0`)
- Dual inject: `player.injury_impact_*` + `custom.injury_impact_*` when non-null
- Do **not** change BasketballEngine injury formula (`delta * 0.12`, clamp `0.35–0.65`) or weight `0.06`
- Do **not** add live injury API, env vars, or DB schema
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Python runner for this machine: `C:\Python314\python.exe` with `PYTHONPATH` set to backend when default `python` lacks deps

## File Structure

### Created files
1. `backend/app/sports/basketball/nba_injury.py` — weights, static table, pure summarize + team lookup
2. `backend/tests/test_nba_injury.py` — unit tests for summarize / lookup

### Modified files
1. `backend/app/sports/basketball/nba_adapter.py` — inject injury impacts in `fetch_all_data`
2. `backend/app/sports/basketball/feature_builder.py` — passthrough `player_raw` injury fields
3. `backend/tests/test_nba_adapter.py` — adapter dual-write tests
4. `backend/tests/test_basketball_feature_builder.py` — passthrough test
5. `CHANGELOG.md` — Unreleased note
6. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-B1 status update

### Unchanged (verify only)
1. `backend/app/sports/basketball/engines/basketball_engine.py` — injury soft path
2. `backend/app/kernel/factor_registry.py` — NBA injury weight seed

---

### Task 1: `summarize_injury_impact` unit tests (RED)

**Files:**
- Create: `backend/tests/test_nba_injury.py`
- (No production code yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.basketball.nba_injury.summarize_injury_impact`, `injury_impact_for_team`, `ROLE_WEIGHTS`
- Produces: failing tests that define the pure API contract for Task 2

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nba_injury.py`:

```python
# backend/tests/test_nba_injury.py
"""Tests for NBA static injury impact (P1-B1)."""
from __future__ import annotations

import pytest

from app.sports.basketball.nba_injury import (
    ROLE_WEIGHTS,
    injury_impact_for_team,
    summarize_injury_impact,
)


class TestRoleWeights:
    def test_expected_tiers(self):
        assert ROLE_WEIGHTS["star"] == 0.35
        assert ROLE_WEIGHTS["starter"] == 0.18
        assert ROLE_WEIGHTS["rotation"] == 0.08
        assert ROLE_WEIGHTS["bench"] == 0.03


class TestSummarizeInjuryImpact:
    def test_none_and_empty_return_none(self):
        assert summarize_injury_impact(None) is None
        assert summarize_injury_impact([]) is None

    def test_single_star_out(self):
        rows = [{"player": "Star A", "role": "star", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.35)

    def test_status_case_insensitive(self):
        rows = [{"player": "X", "role": "starter", "status": "OUT"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.18)

    def test_non_out_statuses_ignored(self):
        rows = [
            {"player": "A", "role": "star", "status": "doubtful"},
            {"player": "B", "role": "starter", "status": "questionable"},
            {"player": "C", "role": "bench", "status": "probable"},
        ]
        assert summarize_injury_impact(rows) is None

    def test_unknown_role_uses_bench(self):
        rows = [{"player": "Y", "role": "unknown", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.03)

    def test_missing_role_uses_bench(self):
        rows = [{"player": "Z", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.03)

    def test_multiple_outs_sum(self):
        rows = [
            {"player": "A", "role": "star", "status": "out"},
            {"player": "B", "role": "starter", "status": "out"},
            {"player": "C", "role": "rotation", "status": "out"},
        ]
        # 0.35 + 0.18 + 0.08 = 0.61
        assert summarize_injury_impact(rows) == pytest.approx(0.61)

    def test_clamp_to_one(self):
        rows = [{"player": f"S{i}", "role": "star", "status": "out"} for i in range(5)]
        # 5 * 0.35 = 1.75 → 1.0
        assert summarize_injury_impact(rows) == pytest.approx(1.0)

    def test_mixed_out_and_non_out(self):
        rows = [
            {"player": "A", "role": "star", "status": "out"},
            {"player": "B", "role": "starter", "status": "doubtful"},
        ]
        assert summarize_injury_impact(rows) == pytest.approx(0.35)

    def test_ignores_non_dict_rows(self):
        rows = ["bad", None, {"player": "A", "role": "bench", "status": "out"}]
        assert summarize_injury_impact(rows) == pytest.approx(0.03)


class TestInjuryImpactForTeam:
    def test_unknown_team_returns_none(self):
        assert injury_impact_for_team("Totally Fake FC") is None
        assert injury_impact_for_team("") is None

    def test_example_franchise_has_out_impact(self):
        # Example rows shipped in static table (Task 2): Boston Celtics
        impact = injury_impact_for_team("Boston Celtics")
        assert impact is not None
        assert 0.0 < impact <= 1.0

    def test_second_example_franchise(self):
        impact = injury_impact_for_team("Los Angeles Lakers")
        assert impact is not None
        assert 0.0 < impact <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_injury.py" -q --tb=short
```

Expected: FAIL — `ModuleNotFoundError` or `ImportError` for `app.sports.basketball.nba_injury`.

- [ ] **Step 3: Commit tests only**

```powershell
git add backend/tests/test_nba_injury.py
git commit -m "test(nba): failing P1-B1 static injury impact unit tests"
```

---

### Task 2: Implement `nba_injury.py` (GREEN)

**Files:**
- Create: `backend/app/sports/basketball/nba_injury.py`
- Test: `backend/tests/test_nba_injury.py`

**Interfaces:**
- Consumes: none
- Produces:
  - `ROLE_WEIGHTS: dict[str, float]`
  - `summarize_injury_impact(rows: list[dict] | None) -> float | None`
  - `injury_impact_for_team(team_name: str) -> float | None`
  - `_STATIC_INJURIES` with at least Boston Celtics + Los Angeles Lakers example Out rows

- [ ] **Step 1: Write the module**

Create `backend/app/sports/basketball/nba_injury.py`:

```python
# backend/app/sports/basketball/nba_injury.py
"""Static NBA injury impact (P1-B1).

Soft signal only: code-local Out list + role-tier weights.
Missing team / no Out rows → None (do not claim known-healthy 0.0).
Engine formula/weights live in BasketballEngine and are unchanged.
"""
from __future__ import annotations

from typing import Any

ROLE_WEIGHTS: dict[str, float] = {
    "star": 0.35,
    "starter": 0.18,
    "rotation": 0.08,
    "bench": 0.03,
}

# Soft static snapshot for tests / optional spot checks. Operators update by PR.
# Keys = fixture full names (balldontlie full_name). Only status "out" counts.
_STATIC_INJURIES: dict[str, list[dict[str, str]]] = {
    "Boston Celtics": [
        {"player": "Example Star Out", "role": "star", "status": "out"},
    ],
    "Los Angeles Lakers": [
        {"player": "Example Starter Out", "role": "starter", "status": "out"},
        {"player": "Example Rotation Out", "role": "rotation", "status": "out"},
    ],
}


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def summarize_injury_impact(rows: list[dict[str, Any]] | None) -> float | None:
    """Sum role weights for Out rows; clamp to [0, 1]. None if no Out contribution."""
    if not rows:
        return None
    total = 0.0
    saw_out = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status != "out":
            continue
        role = str(row.get("role") or "").strip().lower()
        weight = ROLE_WEIGHTS.get(role, ROLE_WEIGHTS["bench"])
        total += float(weight)
        saw_out = True
    if not saw_out:
        return None
    return _clamp01(total)


def injury_impact_for_team(team_name: str) -> float | None:
    """Exact full-name lookup into static table; None if missing/empty/no Out."""
    name = (team_name or "").strip()
    if not name:
        return None
    rows = _STATIC_INJURIES.get(name)
    return summarize_injury_impact(rows)
```

- [ ] **Step 2: Run tests to verify they pass**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_injury.py" -q --tb=short
```

Expected: PASS (all tests green).

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/basketball/nba_injury.py backend/tests/test_nba_injury.py
git commit -m "feat(nba): static Out role-weighted injury impact module (P1-B1)"
```

---

### Task 3: Adapter dual inject (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/basketball/nba_adapter.py` (`fetch_all_data`, after custom block / before return)
- Modify: `backend/tests/test_nba_adapter.py`
- Test: `backend/tests/test_nba_adapter.py::TestNBAAdapterInjuryImpact` (new)

**Interfaces:**
- Consumes: `injury_impact_for_team(team_name: str) -> float | None`
- Produces: when non-null, `raw["player"]["injury_impact_home|away"]` and `raw["custom"]["injury_impact_home|away"]`

- [ ] **Step 1: Write the failing adapter tests**

Append to `backend/tests/test_nba_adapter.py`:

```python
class TestNBAAdapterInjuryImpact:
    def test_fetch_all_data_dual_writes_example_teams(self):
        """Boston/Lakers example static Outs inject player + custom impacts."""
        adapter = NBAAdapter()
        with patch.object(adapter, "_fetch_elo_ratings", return_value={}), \
             patch.object(adapter, "_compute_form", return_value=0.5), \
             patch.object(adapter, "_compute_rest_days", return_value=2):
            match = _make_match()  # Boston Celtics vs Los Angeles Lakers
            raw = adapter.fetch_all_data(match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        # Lakers: starter 0.18 + rotation 0.08 = 0.26
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.26)
        assert raw["custom"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["custom"]["injury_impact_away"] == pytest.approx(0.26)

    def test_fetch_all_data_omits_injury_when_unknown_teams(self):
        adapter = NBAAdapter()
        unknown = MatchIdentity(
            match_id="nba-999",
            season=SeasonIdentity(competition=_NBA, season_key="2024-25"),
            stage="regular_season",
            round=None,
            home=TeamIdentity(code="XXX", name="Fake Home FC", competition=_NBA),
            away=TeamIdentity(code="YYY", name="Fake Away FC", competition=_NBA),
            kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
        )
        with patch.object(adapter, "_fetch_elo_ratings", return_value={}), \
             patch.object(adapter, "_compute_form", return_value=0.5), \
             patch.object(adapter, "_compute_rest_days", return_value=2):
            raw = adapter.fetch_all_data(unknown)

        assert "injury_impact_home" not in raw["player"]
        assert "injury_impact_away" not in raw["player"]
        assert "injury_impact_home" not in raw["custom"]
        assert "injury_impact_away" not in raw["custom"]
```

Also add imports at top of `test_nba_adapter.py` if missing:

```python
import pytest
```

(`MatchIdentity`, `SeasonIdentity`, `TeamIdentity`, `datetime`, `timezone`, `_NBA`, `patch` already exist in the file.)

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterInjuryImpact" -q --tb=short
```

Expected: FAIL — `KeyError` or assertion on missing `injury_impact_*`.

- [ ] **Step 3: Implement adapter inject**

In `backend/app/sports/basketball/nba_adapter.py`, inside `fetch_all_data`, **after** the liquidity try/except and **before** `return raw`, add:

```python
        try:
            from app.sports.basketball.nba_injury import injury_impact_for_team

            inj_h = injury_impact_for_team(home_name)
            inj_a = injury_impact_for_team(away_name)
            if inj_h is not None:
                raw["player"]["injury_impact_home"] = float(inj_h)
                raw["custom"]["injury_impact_home"] = float(inj_h)
            if inj_a is not None:
                raw["player"]["injury_impact_away"] = float(inj_a)
                raw["custom"]["injury_impact_away"] = float(inj_a)
        except Exception:  # noqa: BLE001
            logger.debug("NBA injury enrich skipped", exc_info=True)
```

Do not change Elo/form/rest/travel logic.

- [ ] **Step 4: Run adapter injury tests + existing fetch tests**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterInjuryImpact" "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterFetchAllData" -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/basketball/nba_adapter.py backend/tests/test_nba_adapter.py
git commit -m "feat(nba): inject static injury_impact into player+custom (P1-B1)"
```

---

### Task 4: FeatureBuilder passthrough (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/basketball/feature_builder.py` (PlayerFeatures construction ~L82–87)
- Modify: `backend/tests/test_basketball_feature_builder.py`

**Interfaces:**
- Consumes: `player_raw.get("injury_impact_home|away")`, optional `key_players_available_*`
- Produces: `PlayerFeatures.injury_impact_home|away` from raw (not hard-coded `None`)

- [ ] **Step 1: Write the failing passthrough test**

Append to `backend/tests/test_basketball_feature_builder.py` inside `TestBasketballFeatureBuilderBuild` (or as a new class):

```python
    def test_injury_impact_passthrough_from_player_raw(self):
        builder = BasketballFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["player"] = {
            "injury_impact_home": 0.35,
            "injury_impact_away": 0.26,
        }
        features = builder.build(_make_match(), raw)
        assert features.player.injury_impact_home == pytest.approx(0.35)
        assert features.player.injury_impact_away == pytest.approx(0.26)

    def test_injury_impact_defaults_none_when_absent(self):
        builder = BasketballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.player.injury_impact_home is None
        assert features.player.injury_impact_away is None
```

(`pytest` is already imported in this file.)

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_feature_builder.py::TestBasketballFeatureBuilderBuild::test_injury_impact_passthrough_from_player_raw" -q --tb=short
```

Expected: FAIL — hard-coded `None` still returned.

- [ ] **Step 3: Implement passthrough**

In `backend/app/sports/basketball/feature_builder.py`, replace the `player=PlayerFeatures(...)` block with:

```python
            player=PlayerFeatures(
                key_players_available_home=player_raw.get("key_players_available_home"),
                key_players_available_away=player_raw.get("key_players_available_away"),
                injury_impact_home=player_raw.get("injury_impact_home"),
                injury_impact_away=player_raw.get("injury_impact_away"),
            ),
```

- [ ] **Step 4: Run feature builder tests**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_feature_builder.py" -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/basketball/feature_builder.py backend/tests/test_basketball_feature_builder.py
git commit -m "feat(nba): passthrough player injury_impact in feature builder (P1-B1)"
```

---

### Task 5: Engine smoke (injury available) + docs

**Files:**
- Modify: `backend/tests/test_basketball_engine.py` (optional but preferred one case)
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` (P1-B1 row)

**Interfaces:**
- Consumes: existing `BasketballEngine.predict` + `PlayerFeatures` / `custom` injury fields
- Produces: proof that dual non-null impacts mark injury factor available; docs updated

- [ ] **Step 1: Add engine smoke tests**

Existing tests already use `result.explanation` items with `.factor` and `.available` (see `test_missing_elo_redistributes_weight`). Append:

```python
class TestBasketballEngineInjury:
    def test_injury_factor_available_when_both_impacts_set(self):
        engine = BasketballEngine()
        base = _make_features()
        features = FeatureSet(
            match=base.match,
            general=base.general,
            team=base.team,
            market=base.market,
            player=PlayerFeatures(None, None, 0.35, 0.10),
            environment=base.environment,
            custom=base.custom,
            data_quality=base.data_quality,
            quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        result = engine.predict(features, features.match)
        inj = next(e for e in result.explanation if e.factor == "injury")
        assert inj.available is True
        assert 0.0 < result.outcome_probabilities["home_win"] < 1.0

    def test_custom_injury_fallback_shifts_home_win(self):
        """Higher home injury_impact lowers home_win vs the reverse case."""
        engine = BasketballEngine()
        base = _make_features()
        low_home_inj = FeatureSet(
            match=base.match,
            general=base.general,
            team=base.team,
            market=base.market,
            player=PlayerFeatures(None, None, None, None),
            environment=base.environment,
            custom={**base.custom, "injury_impact_home": 0.0, "injury_impact_away": 0.4},
            data_quality=base.data_quality,
            quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        high_home_inj = FeatureSet(
            match=base.match,
            general=base.general,
            team=base.team,
            market=base.market,
            player=PlayerFeatures(None, None, None, None),
            environment=base.environment,
            custom={**base.custom, "injury_impact_home": 0.4, "injury_impact_away": 0.0},
            data_quality=base.data_quality,
            quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        p_low = engine.predict(low_home_inj, low_home_inj.match).outcome_probabilities["home_win"]
        p_high = engine.predict(high_home_inj, high_home_inj.match).outcome_probabilities["home_win"]
        assert p_low > p_high
```

Note: match `engine.predict(features, features.match)` call style already used in this test file.

- [ ] **Step 2: Run engine tests**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_engine.py" -q --tb=short
```

Expected: PASS (including new injury case).

- [ ] **Step 3: Update CHANGELOG**

At top of `## Unreleased` in `CHANGELOG.md`, add:

```markdown
### NBA static injury impact (P1-B1)
- `nba_injury`: Out-only role weights (star/starter/rotation/bench) → `injury_impact` in [0,1]
- Adapter dual-writes `player` + `custom` when static table has Out rows; missing → None
- FeatureBuilder passthrough; BasketballEngine formula/weight unchanged
```

- [ ] **Step 4: Update backlog P1-B1 row**

In `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`, replace the P1-B1 line with:

```markdown
| P1-B1 | ✅ 部分 2026-07-24：静态 Out 名单 + 角色加权 `injury_impact_*`（adapter player/custom 双写 + FeatureBuilder 透传）；真实时名单源仍待 |
```

- [ ] **Step 5: Full related suite**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_injury.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_feature_builder.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_engine.py" `
  -q --tb=short
```

Expected: PASS.

- [ ] **Step 6: Commit docs + engine test**

```powershell
git add backend/tests/test_basketball_engine.py CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(nba): P1-B1 static injury impact changelog + backlog; engine smoke"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `nba_injury.py` with weights + static table + pure API | Task 2 |
| Out-only filter; role sum; clamp [0,1]; unknown role → bench | Task 1–2 |
| Missing → `None` not `0.0` | Task 1–2, Task 3 unknown-team test |
| Adapter dual-write player + custom | Task 3 |
| FeatureBuilder passthrough | Task 4 |
| Engine formula/weight unchanged | Task 5 (smoke only; no engine edit) |
| Example franchises for non-vacuous tests | Task 2 static table |
| CHANGELOG + backlog | Task 5 |
| No network / no new config | Global constraints + Task 2–3 |

## Placeholder / consistency self-review

- No TBD/TODO left in steps; full module and test code inlined.
- API names consistent: `ROLE_WEIGHTS`, `summarize_injury_impact`, `injury_impact_for_team`.
- Example franchise impacts: Celtics `0.35`, Lakers `0.26` (starter+rotation) used in adapter tests — must match Task 2 table.
- Engine smoke allows fallback directional test if factor_breakdown field names differ; implementer must grep existing result shape before asserting `.available`.
