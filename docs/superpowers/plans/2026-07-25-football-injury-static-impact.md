# Football Static Injury Impact (P1-F3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire real `injury_impact_home/away` scalars from a code-local static Out list (role-weighted) into football enrich so MultiFactor soft `injury` can become available for club matches without changing engine math.

**Architecture:** New pure module `football_injury.py` owns role weights, static Out table, `summarize_injury_impact`, and `injury_impact_for_team`. `enrich_situational_features` dual-writes non-null impacts into `player` and `custom` (static first; WC source only as fallback when static is None). FeatureBuilder already passthroughs. Engine and weights stay untouched.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-football-injury-static-impact-design.md`
- Status filter: **Out only** (case-insensitive); doubtful/questionable/suspended ignored
- Role weights (exact): star `0.35`, starter `0.18`, rotation `0.08`, bench `0.03`; unknown role → bench
- Multiple Outs: sum weights, clamp to `[0, 1]`
- Missing team / empty / no Out rows → **`None`** (never write `0.0`)
- Dual inject: `player.injury_impact_*` + `custom.injury_impact_*` when non-null
- Static wins when present (do not overwrite with WC)
- WC `get_team_injury_impact` remains fallback only when static returns None for that side
- Do **not** change MultiFactor injury formula (`inj_diff * 0.12`) or weight `0.05`
- Do **not** add live injury API, env vars, or DB schema
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`

## File Structure

### Created files
1. `backend/app/sports/football/football_injury.py` — weights, static table, pure summarize + team lookup
2. `backend/tests/test_football_injury.py` — unit tests for summarize / lookup

### Modified files
1. `backend/app/sports/football/adapters/_shared.py` — injury block in `enrich_situational_features`
2. `backend/tests/test_adapter_shared.py` — enrich dual-write + WC fallback tests
3. `CHANGELOG.md` — Unreleased note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F3 status update

### Unchanged (verify only)
1. `backend/app/sports/football/feature_builder.py` — already passthroughs injury
2. `backend/app/sports/football/engines/football_multi_factor_engine.py` — injury soft path
3. Existing `test_injury_custom_fallback` in multi_factor tests

---

### Task 1: `summarize_injury_impact` unit tests (RED)

**Files:**
- Create: `backend/tests/test_football_injury.py`
- (No production code yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.football_injury.summarize_injury_impact`, `injury_impact_for_team`, `ROLE_WEIGHTS`
- Produces: failing tests that define the pure API contract for Task 2

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_football_injury.py`:

```python
# backend/tests/test_football_injury.py
"""Tests for football static injury impact (P1-F3)."""
from __future__ import annotations

import pytest

from app.sports.football.football_injury import (
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
            {"player": "C", "role": "bench", "status": "suspended"},
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

    def test_real_madrid_example(self):
        # Task 2 ships star out → 0.35
        assert injury_impact_for_team("Real Madrid CF") == pytest.approx(0.35)

    def test_bayern_example(self):
        # Task 2 ships starter + rotation → 0.26
        assert injury_impact_for_team("FC Bayern München") == pytest.approx(0.26)
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_football_injury.py" -q --tb=short
```

Expected: FAIL — `ImportError` / cannot import `football_injury`.

- [ ] **Step 3: Commit tests only**

```powershell
git add backend/tests/test_football_injury.py
git commit -m "test(football): failing P1-F3 static injury unit tests"
```

---

### Task 2: Implement `football_injury` module (GREEN)

**Files:**
- Create: `backend/app/sports/football/football_injury.py`
- Test: `backend/tests/test_football_injury.py`

**Interfaces:**
- Produces:

```python
ROLE_WEIGHTS: dict[str, float]
def summarize_injury_impact(rows: list[dict[str, Any]] | None) -> float | None: ...
def injury_impact_for_team(team_name: str) -> float | None: ...
```

- [ ] **Step 1: Implement module**

Create `backend/app/sports/football/football_injury.py`:

```python
# backend/app/sports/football/football_injury.py
"""Static football injury impact (P1-F3).

Soft signal only: code-local Out list + role-tier weights.
Missing team / no Out rows → None (do not claim known-healthy 0.0).
Engine formula/weights live in FootballMultiFactorEngine and are unchanged.
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
# Keys = fixture full names (adapter / kernel). Only status "out" counts.
_STATIC_INJURIES: dict[str, list[dict[str, str]]] = {
    "Real Madrid CF": [
        {"player": "Example Star Out", "role": "star", "status": "out"},
    ],
    "FC Bayern München": [
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
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_football_injury.py" -q --tb=short
```

Expected: PASS (all tests green).

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/football_injury.py backend/tests/test_football_injury.py
git commit -m "feat(football): static Out role-weighted injury impact module (P1-F3)"
```

---

### Task 3: Enrich dual inject + WC fallback (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (injury block in `enrich_situational_features`, currently ~L426–439)
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `injury_impact_for_team(team_name: str) -> float | None`
- Produces: when non-null, `raw["player"]["injury_impact_home|away"]` and `raw["custom"]["injury_impact_home|away"]`
- WC fallback only when static None for that side; static never overwritten

- [ ] **Step 1: Write failing enrich tests**

Append to `backend/tests/test_adapter_shared.py` (imports already include `enrich_situational_features`, `patch`, `pytest`):

```python
class TestInjuryImpactEnrich:
    def test_static_dual_writes_sample_teams(self):
        """Real Madrid / Bayern sample Outs inject player + custom impacts."""
        match = _make_match("ucl-injury")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.26)
        assert raw["custom"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["custom"]["injury_impact_away"] == pytest.approx(0.26)

    def test_unknown_teams_omit_injury_keys(self):
        football = SportIdentity(code="football", name="Football")
        ucl = CompetitionIdentity(code="ucl", name="UCL", sport=football)
        match = MatchIdentity(
            match_id="ucl-unknown-inj",
            season=SeasonIdentity(competition=ucl, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="Fake Home FC", competition=ucl),
            away=TeamIdentity(code="YYY", name="Fake Away FC", competition=ucl),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "injury_impact_home" not in raw["player"]
        assert "injury_impact_away" not in raw["player"]
        assert "injury_impact_home" not in raw["custom"]
        assert "injury_impact_away" not in raw["custom"]

    def test_wc_fallback_when_static_none(self):
        """WC source fills a side only when static returns None."""
        football = SportIdentity(code="football", name="Football")
        ucl = CompetitionIdentity(code="ucl", name="UCL", sport=football)
        match = MatchIdentity(
            match_id="ucl-wc-fallback",
            season=SeasonIdentity(competition=ucl, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="Fake Home FC", competition=ucl),
            away=TeamIdentity(code="YYY", name="Fake Away FC", competition=ucl),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }

        def _wc_side(name: str):
            if name == "Fake Home FC":
                return 0.22
            return None

        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            side_effect=_wc_side,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.22)
        assert raw["custom"]["injury_impact_home"] == pytest.approx(0.22)
        assert "injury_impact_away" not in raw["player"]
        assert "injury_impact_away" not in raw["custom"]

    def test_static_not_overwritten_by_wc(self):
        match = _make_match("ucl-static-wins")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            return_value=0.99,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.26)
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestInjuryImpactEnrich" -q --tb=short
```

Expected: FAIL — missing dual-write / custom keys / static-over-WC behavior.

- [ ] **Step 3: Replace injury block in `_shared.py`**

Replace the existing injury block in `enrich_situational_features` (the comment `# Injury: optional world-cup player status...` through its `except` / `pass`) with:

```python
    # P1-F3: injury impact — static role-weighted Out list, WC fallback
    try:
        from app.sports.football.football_injury import injury_impact_for_team

        inj_h = injury_impact_for_team(home_name)
        inj_a = injury_impact_for_team(away_name)

        wc_lookup = None
        if inj_h is None or inj_a is None:
            try:
                from app.services.world_cup_player_status_source import (
                    get_team_injury_impact,
                )
                wc_lookup = get_team_injury_impact
            except Exception:  # noqa: BLE001
                wc_lookup = None

        if inj_h is None and wc_lookup is not None:
            try:
                inj_h = wc_lookup(home_name)
            except Exception:  # noqa: BLE001
                inj_h = None
        if inj_a is None and wc_lookup is not None:
            try:
                inj_a = wc_lookup(away_name)
            except Exception:  # noqa: BLE001
                inj_a = None

        if inj_h is not None:
            raw["player"]["injury_impact_home"] = float(inj_h)
            raw.setdefault("custom", {})["injury_impact_home"] = float(inj_h)
        if inj_a is not None:
            raw["player"]["injury_impact_away"] = float(inj_a)
            raw.setdefault("custom", {})["injury_impact_away"] = float(inj_a)
    except Exception:  # noqa: BLE001
        logger.debug("injury impact enrich skipped", exc_info=True)
```

Notes:
- `home_name` / `away_name` already in scope.
- Do not change schedule density or other enrich blocks.
- Prefer `logger.debug` over bare `pass` for consistency with density block.

- [ ] **Step 4: Run injury enrich + density regression**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestInjuryImpactEnrich" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestScheduleDensityEnrich" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_football_injury.py" `
  -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): inject static injury_impact into player+custom (P1-F3)"
```

---

### Task 4: Docs + engine smoke verification

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`
- Verify only: `backend/tests/test_football_multi_factor_engine.py::TestFootballMultiFactorPredict::test_injury_custom_fallback`

**Interfaces:**
- Consumes: completed Tasks 1–3; engine already dual-reads injury
- Produces: docs updated; existing engine injury test still green

- [ ] **Step 1: Run focused regression suite**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_football_injury.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestInjuryImpactEnrich" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_football_multi_factor_engine.py::TestFootballMultiFactorPredict::test_injury_custom_fallback" `
  -q --tb=short
```

Expected: PASS (engine test unchanged; proves custom fallback path still works).

- [ ] **Step 2: Update CHANGELOG**

At top of `## Unreleased` in `CHANGELOG.md` (before P1-F2 section):

```markdown
### Football static injury impact (P1-F3)
- `football_injury`: Out-only role weights (star/starter/rotation/bench) → `injury_impact` in [0,1]
- Enrich dual-writes `player` + `custom` when static table has Out rows; missing → None
- WC player-status source remains fallback only when static is None; MultiFactor formula/weight unchanged
```

- [ ] **Step 3: Update backlog P1-F3 row**

Replace P1-F3 line in `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` with:

```markdown
| P1-F3 | injury / availability | ✅ 部分 2026-07-25：静态 Out + 角色加权 `injury_impact_*`（player/custom 双写；WC 源仅 static None fallback）；真伤病 API 与分钟/身价加权仍待 |
```

Keep table column structure consistent with neighboring rows.

- [ ] **Step 4: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F3 static injury changelog + backlog"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `football_injury` pure module + ROLE_WEIGHTS | Task 1–2 |
| Out-only; unknown role → bench; clamp [0,1] | Task 1–2 |
| Sample Real Madrid 0.35 / Bayern 0.26 | Task 2 |
| Dual write player + custom | Task 3 |
| Missing team → no 0.0 / omit keys | Task 3 |
| WC fallback when static None | Task 3 |
| Static not overwritten by WC | Task 3 |
| Engine formula/weight unchanged | Task 4 (verify only) |
| CHANGELOG + backlog | Task 4 |

## Placeholder / consistency self-review

- No TBD steps; full test and production code inlined.
- API names consistent with NBA: `ROLE_WEIGHTS`, `summarize_injury_impact`, `injury_impact_for_team`.
- Sample impacts fixed: Real Madrid CF `0.35`, FC Bayern München `0.26` (starter+rotation).
- Patch path for WC: `app.services.world_cup_player_status_source.get_team_injury_impact`.
- Density helper patches keep other enrich side effects out of injury tests.
