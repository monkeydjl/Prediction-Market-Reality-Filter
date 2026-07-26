# Football Static Referee Bias (P1-F8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefer a code-local referee home-bias table so MultiFactor soft `referee` becomes available when fixture feeds only provide a referee name, without live APIs or engine changes.

**Architecture:** Pure `football_referee.py` owns a normalized-name static table + `bias_for_referee`. `enrich_referee_features` keeps pass-through priority for existing rate/bias, then fills `custom.referee_home_bias` + `referee_source="static_map"` on static hit. MultiFactor and FeatureBuilder stay untouched.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network. No DB.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-football-static-referee-design.md`
- Field this round: **home_bias only** (not `referee_home_win_rate`)
- Pass-through first: if `custom.referee_home_win_rate` or `custom.referee_home_bias` already set → **do not overwrite**
- Lookup: normalize lower + whitespace collapse; empty/unknown → `None`
- Return clamp: bias ∈ **[-0.25, 0.25]**; `round(2)`
- Soft table values typically mild ∈ **[-0.15, 0.15]**
- Coverage: EPL / La Liga / Serie A / Bundesliga / Ligue 1 / UCL-common officials, **≥20** rows (target ~20–40)
- Source string on static hit: exactly `static_map` (existing FE/tests)
- Do **not** change MultiFactor referee formula/weight or FeatureBuilder
- Do **not** call external referee APIs; no DB columns this round
- Do **not** push to origin unless user explicitly asks
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`
- Prefer basetemp under repo for Windows pytest cleanup noise:
  `--basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f8"`

## File Structure

### Created files
1. `backend/app/sports/football/football_referee.py` — static table + `bias_for_referee`
2. `backend/tests/test_football_referee.py` — pure unit tests

### Modified files
1. `backend/app/sports/football/adapters/_shared.py` — remove empty `_REFEREE_HOME_BIAS`; call `bias_for_referee`
2. `backend/tests/test_adapter_shared.py` — rewrite static map test (no monkeypatch on removed dict); add no-overwrite / known-name cases
3. `CHANGELOG.md` — Unreleased P1-F8 static note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F8 status line

### Unchanged (verify only)
1. `backend/app/sports/football/engines/football_multi_factor_engine.py` — soft referee path
2. `backend/tests/test_football_multi_factor_engine.py` — injects custom rate/bias directly

---

### Task 1: `bias_for_referee` unit tests (RED)

**Files:**
- Create: `backend/tests/test_football_referee.py`
- (No production implementation yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.football_referee.bias_for_referee`
- Produces: failing tests defining the lookup API for Task 2

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_football_referee.py`:

```python
"""Tests for football_referee.bias_for_referee (P1-F8)."""
import pytest

from app.sports.football.football_referee import bias_for_referee


class TestBiasForReferee:
    def test_known_referee_in_band(self):
        b = bias_for_referee("Michael Oliver")
        assert b is not None
        assert -0.25 <= float(b) <= 0.25

    def test_unknown_returns_none(self):
        assert bias_for_referee("NotARealRefereeXYZ") is None

    def test_empty_returns_none(self):
        assert bias_for_referee("") is None
        assert bias_for_referee("   ") is None

    def test_normalize_case_and_spaces(self):
        a = bias_for_referee("Michael Oliver")
        b = bias_for_referee("  michael oliver  ")
        c = bias_for_referee("MICHAEL OLIVER")
        assert a is not None
        assert a == b == c

    def test_alias_if_present(self):
        # Diacritic / ascii pair must share bias when both keys exist
        a = bias_for_referee("Cüneyt Çakır")
        b = bias_for_referee("Cuneyt Cakir")
        assert a is not None and b is not None
        assert a == b

    def test_mild_not_extreme(self):
        b = bias_for_referee("Anthony Taylor")
        assert b is not None
        assert abs(float(b)) <= 0.15
```

- [ ] **Step 2: Run tests — expect RED**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_referee.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f8"
```

Expected: FAIL (module or `bias_for_referee` import missing).

- [ ] **Step 3: Commit**

```powershell
git add backend/tests/test_football_referee.py
git commit -m "test(football): failing P1-F8 bias_for_referee unit tests"
```

---

### Task 2: Implement `football_referee` module (GREEN)

**Files:**
- Create: `backend/app/sports/football/football_referee.py`
- Test: `backend/tests/test_football_referee.py`

**Interfaces:**
- Produces:

```python
def bias_for_referee(name: str) -> float | None:
    ...
```

- [ ] **Step 1: Implement module**

Create `backend/app/sports/football/football_referee.py`:

```python
"""Static football referee home-bias (P1-F8).

Soft directional priors (not live FA/league season stats).
Missing / empty name → None. Engine formula lives in MultiFactor (unchanged).
"""
from __future__ import annotations

# Soft home bias in roughly [-0.15, 0.15]. Keys are _normalize()'d English names.
# Positive → slight home favor via home_rate = 0.5 + 0.5 * bias.
# Operators update by PR.
_REFEREE_HOME_BIAS: dict[str, float] = {
    # EPL
    "michael oliver": 0.04,
    "anthony taylor": 0.03,
    "paul tierney": 0.02,
    "stuart attwell": 0.01,
    "craig pawson": 0.02,
    "simon hooper": 0.01,
    "robert jones": 0.02,
    "john brooks": 0.01,
    "david coote": 0.00,
    "andre marriner": 0.02,
    "martin atkinson": 0.03,
    "mike dean": 0.05,
    "chris kavanagh": 0.01,
    "jarred gillett": 0.01,
    "thomas bramall": 0.00,
    "peter bankes": 0.01,
    "andy madley": 0.01,
    "tim robinson": 0.00,
    # La Liga / Spain
    "jesus gil manzano": 0.03,
    "jesús gil manzano": 0.03,
    "antonio mateu lahoz": 0.04,
    "carlos del cerro grande": 0.02,
    "jose maria sanchez martinez": 0.02,
    "josé maría sánchez martínez": 0.02,
    "alejandro hernandez hernandez": 0.02,
    "alejandro hernández hernández": 0.02,
    # Serie A / Italy
    "daniele orsato": 0.03,
    "davide massa": 0.02,
    "marco guida": 0.02,
    "maurizio mariani": 0.01,
    "fabio maresca": 0.01,
    "daniele doveri": 0.02,
    "simone sozza": 0.01,
    # Bundesliga / Germany
    "felix brych": 0.03,
    "daniel siebert": 0.02,
    "felix zwayer": 0.02,
    "tobias stieler": 0.01,
    "deniz aytekin": 0.02,
    "sascha stegemann": 0.01,
    # Ligue 1 / France
    "clement turpin": 0.03,
    "clément turpin": 0.03,
    "francois leterrier": 0.01,
    "françois leterrier": 0.01,
    "benoit bastien": 0.02,
    "benoît bastien": 0.02,
    "ruddy buquet": 0.01,
    # UEFA / international frequent
    "szymon marciniak": 0.03,
    "danny makkelie": 0.02,
    "bjorn kuipers": 0.03,
    "björn kuipers": 0.03,
    "cuneyt cakir": 0.02,
    "cüneyt çakır": 0.02,
    "slavko vincic": 0.02,
    "slavko vinčić": 0.02,
    "istvan kovacs": 0.01,
    "istván kovács": 0.01,
    "halil umut meler": 0.01,
    "artur soares dias": 0.01,
    "ovidiu hategan": 0.01,
    "ovidiu hațegan": 0.01,
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def bias_for_referee(name: str) -> float | None:
    """Soft home-win bias for a referee display name, or None if unknown/empty.

    Bias is in [-0.25, 0.25] where positive favors home win share via:
      home_rate = 0.5 + 0.5 * bias
    """
    key = _normalize(name)
    if not key:
        return None
    val = _REFEREE_HOME_BIAS.get(key)
    if val is None:
        return None
    try:
        b = float(val)
    except (TypeError, ValueError):
        return None
    if b < -0.25:
        b = -0.25
    elif b > 0.25:
        b = 0.25
    return round(b, 2)
```

Notes for implementer:
- Table must contain **≥20 unique normalized keys** (aliases count toward coverage for tests but unique officials preferred ≥20).
- Remove the duplicate `"michael oliver"` line if the dict literal would redefine it; keep a single entry.
- Ensure both `"cuneyt cakir"` and `"cüneyt çakır"` exist for the alias test.
- Ensure `"anthony taylor"` exists for mild-band test.
- Do not invent values outside `[-0.15, 0.15]` for table entries.

- [ ] **Step 2: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_referee.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f8"
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/football_referee.py backend/tests/test_football_referee.py
git commit -m "feat(football): static referee home-bias table (P1-F8)"
```

---

### Task 3: Adapter enrich uses `bias_for_referee` + tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (referee map + `enrich_referee_features`, ~218–261)
- Modify: `backend/tests/test_adapter_shared.py` (`TestEnrichRefereeFeatures`)

**Interfaces:**
- Consumes: `bias_for_referee(name: str) -> float | None`
- Produces: on static hit → `custom.referee_home_bias`, `custom.referee_source="static_map"`, `custom.referee_name`

- [ ] **Step 1: Update adapter tests (expect FAIL on old monkeypatch / missing static hit)**

Replace `TestEnrichRefereeFeatures` in `backend/tests/test_adapter_shared.py` with:

```python
class TestEnrichRefereeFeatures:
    def test_passthrough_rate(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {"referee_home_win_rate": 0.62}, "environment": {}}
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_home_win_rate"] == 0.62
        assert raw["custom"].get("referee_source") != "static_map"

    def test_passthrough_bias_not_overwritten(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {
            "custom": {"referee_home_bias": 0.11, "referee_name": "Michael Oliver"},
            "environment": {"referee": "Michael Oliver"},
        }
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_home_bias"] == pytest.approx(0.11)
        assert raw["custom"].get("referee_source") != "static_map"

    def test_environment_unknown_name_only(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {}, "environment": {"referee": "John Smith UnknownXYZ"}}
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_name"] == "John Smith UnknownXYZ"
        assert raw["custom"].get("referee_home_win_rate") is None
        assert raw["custom"].get("referee_home_bias") is None
        assert raw["custom"].get("referee_source") is None

    def test_static_map_bias_known_name(self):
        from app.sports.football.adapters._shared import enrich_referee_features
        from app.sports.football.football_referee import bias_for_referee

        raw = {"custom": {}, "environment": {"referee": "Michael Oliver"}}
        enrich_referee_features(raw, _make_match())
        expected = bias_for_referee("Michael Oliver")
        assert expected is not None
        assert raw["custom"]["referee_name"] == "Michael Oliver"
        assert raw["custom"]["referee_home_bias"] == pytest.approx(float(expected))
        assert raw["custom"]["referee_source"] == "static_map"
```

Ensure `pytest` is already imported at top of `test_adapter_shared.py` (it is used elsewhere).

- [ ] **Step 2: Run adapter referee tests — expect FAIL until wire-up**

```powershell
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestEnrichRefereeFeatures -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f8"
```

Expected before implementation: FAIL on static known-name (empty map) and/or monkeypatch removal if partially edited.

- [ ] **Step 3: Implement adapter wire-up**

In `backend/app/sports/football/adapters/_shared.py`:

1. Delete:

```python
# Optional static referee home-bias map (name lower → bias in [-0.25, 0.25]).
# Empty by default; operators / scrapers can populate via custom without code.
_REFEREE_HOME_BIAS: dict[str, float] = {}
```

2. Replace `enrich_referee_features` with:

```python
def enrich_referee_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through / soft-fill referee custom fields for multi-factor (P1-F8).

    Sources (first wins for rate/bias):
    1. Already-set ``custom.referee_home_win_rate`` / ``referee_home_bias``
    2. ``environment.referee`` / ``custom.referee_name`` + ``bias_for_referee`` static table

    Never invents rates without a name or explicit numeric field.
    """
    custom = raw.setdefault("custom", {})
    env = raw.get("environment") or {}
    if env.get("referee") and not custom.get("referee_name"):
        custom["referee_name"] = str(env["referee"]).strip()

    if (
        custom.get("referee_home_win_rate") is not None
        or custom.get("referee_home_bias") is not None
    ):
        return

    name = custom.get("referee_name") or env.get("referee")
    if not name:
        return
    custom["referee_name"] = str(name).strip()
    try:
        from app.sports.football.football_referee import bias_for_referee

        b = bias_for_referee(str(name))
    except Exception:  # noqa: BLE001
        logger.debug("referee static bias lookup skipped", exc_info=True)
        return
    if b is None:
        return
    custom["referee_home_bias"] = float(b)
    custom["referee_source"] = "static_map"
```

Keep the existing call site in `fetch_raw_match_data`:

```python
enrich_referee_features(raw, match)
```

- [ ] **Step 4: Run tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_referee.py tests/test_adapter_shared.py::TestEnrichRefereeFeatures -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f8"
```

Expected: all PASS.

Optional smoke (should still pass):

```powershell
C:\Python314\python.exe -m pytest tests/test_football_multi_factor_engine.py -k referee -v --tb=line --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f8"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): wire static referee bias into enrich (P1-F8)"
```

---

### Task 4: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

- [ ] **Step 1: CHANGELOG Unreleased** (above other Unreleased football sections if present):

```markdown
### Football static referee home-bias (P1-F8)

- `football_referee.bias_for_referee`: code-local soft home_bias by normalized referee name (top leagues + UCL-common)
- Adapter `enrich_referee_features`: pass-through rate/bias first; static fill writes `referee_home_bias` + `referee_source=static_map`
- MultiFactor referee formula/weight unchanged; true referee stats API/DB still pending
```

- [ ] **Step 2: Backlog P1-F8 row**

Replace the P1-F8 line with:

```markdown
| P1-F8 | 裁判 | ✅ 部分 2026-07-26：静态 `bias_for_referee` + enrich fill-only（`referee_source=static_map`）；真裁判统计源与库列仍待 |
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F8 static referee changelog + backlog"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Pure `football_referee.py` + `bias_for_referee` | 1–2 |
| ≥20 static entries, mild soft values | 2 |
| Normalize lower/whitespace; empty/unknown → None | 1–2 |
| Clamp [-0.25, 0.25], round(2) | 2 |
| Adapter removes empty `_REFEREE_HOME_BIAS` | 3 |
| Pass-through no overwrite | 3 |
| Static hit → bias + `static_map` | 3 |
| Unknown name → name only | 3 |
| MultiFactor unchanged | constraints + smoke |
| CHANGELOG + backlog | 4 |

## Placeholder / consistency scan

- No TBD placeholders.
- API names: `bias_for_referee`, `enrich_referee_features`, `referee_source=static_map`.
- Fixture names for tests: `Michael Oliver`, `Anthony Taylor`, `Cüneyt Çakır` / `Cuneyt Cakir`.
- Adapter tests must not monkeypatch removed `_REFEREE_HOME_BIAS`.
