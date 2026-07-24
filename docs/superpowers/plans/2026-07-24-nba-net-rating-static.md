# NBA Static Net Rating (P1-B4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace match-invariant hard-coded `ortg_*/drtg_*` stubs in `NBAAdapter` with a 30-franchise static ORtg/DRtg table so BasketballEngine soft `net_rating` reflects team-specific efficiency.

**Architecture:** New pure module `nba_team_ratings.py` owns `_TEAM_RATINGS` and `ratings_for_team`. Adapter injects all four `custom.ortg_home/drtg_home/ortg_away/drtg_away` only when both teams resolve; otherwise omits all four. Remove pace/tpct stubs from default custom. Engine formula/weights unchanged.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-nba-net-rating-static-design.md`
- Fields: **ORtg + DRtg only** (no pace/tpct realization this round)
- 30 primary franchise full names (balldontlie / `team_geo` style); include `LA Clippers` alias matching `Los Angeles Clippers`
- ORtg/DRtg soft multi-year-ish values roughly in **`[105, 125]`**
- Inject **only when both** home and away resolve; else omit all four (never league-average fake)
- Remove hard-coded `ortg_*`, `drtg_*`, `pace_*`, `tpct_*` from default adapter `custom`
- Do **not** change BasketballEngine net_rating formula (`net_diff * 0.012`, clamp ±15 → p in 0.30–0.70) or weight `0.13`
- Do **not** add live API, env vars, or DB schema
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`

## File Structure

### Created files
1. `backend/app/sports/basketball/nba_team_ratings.py` — static table + `ratings_for_team`
2. `backend/tests/test_nba_team_ratings.py` — coverage / range / direction / lookup tests

### Modified files
1. `backend/app/sports/basketball/nba_adapter.py` — remove stubs; inject ratings
2. `backend/tests/test_nba_adapter.py` — inject / omit tests
3. `CHANGELOG.md` — Unreleased note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-B4 status

### Unchanged (verify only)
1. `backend/app/sports/basketball/engines/basketball_engine.py` — net_rating soft path
2. `backend/tests/test_basketball_net_rating.py` — supplies custom explicitly
3. `backend/tests/test_basketball_feature_builder.py` — uses its own raw fixtures (may keep pace/ortg in fixture raw; not adapter stubs)

---

### Task 1: `nba_team_ratings` unit tests (RED)

**Files:**
- Create: `backend/tests/test_nba_team_ratings.py`
- (No production code yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.basketball.nba_team_ratings.ratings_for_team`, `PRIMARY_FRANCHISES` or `_TEAM_RATINGS`
- Produces: failing tests defining the table contract for Task 2

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nba_team_ratings.py`:

```python
# backend/tests/test_nba_team_ratings.py
"""Tests for NBA static team ORtg/DRtg (P1-B4)."""
from __future__ import annotations

import pytest

from app.sports.basketball.nba_team_ratings import (
    PRIMARY_FRANCHISES,
    _TEAM_RATINGS,
    ratings_for_team,
)


class TestPrimaryCoverage:
    def test_primary_franchises_are_thirty(self):
        assert len(PRIMARY_FRANCHISES) == 30

    def test_every_primary_has_table_key(self):
        missing = [n for n in PRIMARY_FRANCHISES if n not in _TEAM_RATINGS]
        assert missing == [], f"missing ratings: {missing}"

    def test_clippers_alias_matches_primary(self):
        assert "Los Angeles Clippers" in _TEAM_RATINGS
        assert "LA Clippers" in _TEAM_RATINGS
        assert _TEAM_RATINGS["LA Clippers"] == _TEAM_RATINGS["Los Angeles Clippers"]


class TestValueRanges:
    def test_all_primary_ortg_drtg_in_band(self):
        for name in PRIMARY_FRANCHISES:
            row = _TEAM_RATINGS[name]
            assert 105.0 <= float(row["ortg"]) <= 125.0, f"{name} ortg={row['ortg']}"
            assert 105.0 <= float(row["drtg"]) <= 125.0, f"{name} drtg={row['drtg']}"


class TestNetDirection:
    def test_strong_net_above_weak_net(self):
        """OKC-ish top net should beat WAS-ish bottom net (soft ordering)."""
        strong = _TEAM_RATINGS["Oklahoma City Thunder"]
        weak = _TEAM_RATINGS["Washington Wizards"]
        net_s = float(strong["ortg"]) - float(strong["drtg"])
        net_w = float(weak["ortg"]) - float(weak["drtg"])
        assert net_s > net_w
        assert net_s > 0
        assert net_w < 0


class TestRatingsForTeam:
    def test_known_team_returns_ortg_drtg(self):
        row = ratings_for_team("Boston Celtics")
        assert row is not None
        assert "ortg" in row and "drtg" in row
        assert 105.0 <= row["ortg"] <= 125.0

    def test_unknown_and_empty_return_none(self):
        assert ratings_for_team("Totally Fake FC") is None
        assert ratings_for_team("") is None
        assert ratings_for_team("   ") is None

    def test_returns_copy_not_live_table_row(self):
        row = ratings_for_team("Boston Celtics")
        assert row is not None
        row["ortg"] = -1.0
        again = ratings_for_team("Boston Celtics")
        assert again is not None
        assert again["ortg"] != -1.0
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_team_ratings.py" -q --tb=short
```

Expected: FAIL — `ModuleNotFoundError` for `app.sports.basketball.nba_team_ratings`.

- [ ] **Step 3: Commit tests only**

```powershell
git add backend/tests/test_nba_team_ratings.py
git commit -m "test(nba): failing P1-B4 static team ORtg/DRtg coverage tests"
```

---

### Task 2: Implement `nba_team_ratings.py` (GREEN)

**Files:**
- Create: `backend/app/sports/basketball/nba_team_ratings.py`
- Test: `backend/tests/test_nba_team_ratings.py`

**Interfaces:**
- Consumes: none
- Produces:
  - `PRIMARY_FRANCHISES: tuple[str, ...]` — 30 primary full names (Clippers primary = `Los Angeles Clippers`)
  - `_TEAM_RATINGS: dict[str, dict[str, float]]`
  - `ratings_for_team(team_name: str) -> dict[str, float] | None` — exact match; returns `{"ortg", "drtg"}` copy or None

- [ ] **Step 1: Write the module**

Create `backend/app/sports/basketball/nba_team_ratings.py` with the full content below (soft multi-year-ish consensus levels; operators update by PR):

```python
# backend/app/sports/basketball/nba_team_ratings.py
"""Static NBA team ORtg/DRtg for soft net_rating (P1-B4).

Soft multi-year-ish points-per-100 levels (not live season scrape).
Missing team → None. Engine formula lives in BasketballEngine (unchanged).
"""
from __future__ import annotations

PRIMARY_FRANCHISES: tuple[str, ...] = (
    "Atlanta Hawks",
    "Boston Celtics",
    "Brooklyn Nets",
    "Charlotte Hornets",
    "Chicago Bulls",
    "Cleveland Cavaliers",
    "Dallas Mavericks",
    "Denver Nuggets",
    "Detroit Pistons",
    "Golden State Warriors",
    "Houston Rockets",
    "Indiana Pacers",
    "Los Angeles Clippers",
    "Los Angeles Lakers",
    "Memphis Grizzlies",
    "Miami Heat",
    "Milwaukee Bucks",
    "Minnesota Timberwolves",
    "New Orleans Pelicans",
    "New York Knicks",
    "Oklahoma City Thunder",
    "Orlando Magic",
    "Philadelphia 76ers",
    "Phoenix Suns",
    "Portland Trail Blazers",
    "Sacramento Kings",
    "San Antonio Spurs",
    "Toronto Raptors",
    "Utah Jazz",
    "Washington Wizards",
)

# Soft static ORtg/DRtg (1.0-possession points per 100). Soft signal only.
# Alias keys mirror team_geo dual Clippers names.
_TEAM_RATINGS: dict[str, dict[str, float]] = {
    "Atlanta Hawks": {"ortg": 115.0, "drtg": 116.0},
    "Boston Celtics": {"ortg": 118.0, "drtg": 109.0},
    "Brooklyn Nets": {"ortg": 110.0, "drtg": 115.0},
    "Charlotte Hornets": {"ortg": 108.0, "drtg": 117.0},
    "Chicago Bulls": {"ortg": 112.0, "drtg": 114.0},
    "Cleveland Cavaliers": {"ortg": 116.0, "drtg": 110.0},
    "Dallas Mavericks": {"ortg": 114.0, "drtg": 113.0},
    "Denver Nuggets": {"ortg": 117.0, "drtg": 112.0},
    "Detroit Pistons": {"ortg": 109.0, "drtg": 116.0},
    "Golden State Warriors": {"ortg": 115.0, "drtg": 112.0},
    "Houston Rockets": {"ortg": 113.0, "drtg": 110.0},
    "Indiana Pacers": {"ortg": 116.0, "drtg": 114.0},
    "LA Clippers": {"ortg": 114.0, "drtg": 113.0},
    "Los Angeles Clippers": {"ortg": 114.0, "drtg": 113.0},
    "Los Angeles Lakers": {"ortg": 115.0, "drtg": 113.0},
    "Memphis Grizzlies": {"ortg": 111.0, "drtg": 116.0},
    "Miami Heat": {"ortg": 112.0, "drtg": 111.0},
    "Milwaukee Bucks": {"ortg": 114.0, "drtg": 112.0},
    "Minnesota Timberwolves": {"ortg": 114.0, "drtg": 109.0},
    "New Orleans Pelicans": {"ortg": 112.0, "drtg": 113.0},
    "New York Knicks": {"ortg": 117.0, "drtg": 111.0},
    "Oklahoma City Thunder": {"ortg": 118.0, "drtg": 106.0},
    "Orlando Magic": {"ortg": 111.0, "drtg": 108.0},
    "Philadelphia 76ers": {"ortg": 112.0, "drtg": 115.0},
    "Phoenix Suns": {"ortg": 115.0, "drtg": 114.0},
    "Portland Trail Blazers": {"ortg": 109.0, "drtg": 116.0},
    "Sacramento Kings": {"ortg": 114.0, "drtg": 115.0},
    "San Antonio Spurs": {"ortg": 111.0, "drtg": 114.0},
    "Toronto Raptors": {"ortg": 112.0, "drtg": 116.0},
    "Utah Jazz": {"ortg": 110.0, "drtg": 118.0},
    "Washington Wizards": {"ortg": 107.0, "drtg": 119.0},
}


def ratings_for_team(team_name: str) -> dict[str, float] | None:
    """Exact full-name lookup. Returns a shallow copy of {ortg, drtg} or None."""
    name = (team_name or "").strip()
    if not name:
        return None
    row = _TEAM_RATINGS.get(name)
    if row is None:
        return None
    return {"ortg": float(row["ortg"]), "drtg": float(row["drtg"])}
```

- [ ] **Step 2: Run tests to verify they pass**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_team_ratings.py" -q --tb=short
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/basketball/nba_team_ratings.py backend/tests/test_nba_team_ratings.py
git commit -m "feat(nba): static 30-team ORtg/DRtg table for net_rating (P1-B4)"
```

---

### Task 3: Adapter inject + remove stubs (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/basketball/nba_adapter.py` (`fetch_all_data` custom block + enrich)
- Modify: `backend/tests/test_nba_adapter.py`

**Interfaces:**
- Consumes: `ratings_for_team(team_name: str) -> dict[str, float] | None`
- Produces: when both resolve, `custom.ortg_home`, `drtg_home`, `ortg_away`, `drtg_away`; never default pace/tpct/ortg/drtg stubs

- [ ] **Step 1: Write failing adapter tests**

Append to `backend/tests/test_nba_adapter.py`:

```python
class TestNBAAdapterTeamRatings:
    def test_fetch_all_data_injects_ortg_drtg_for_known_teams(self):
        adapter = NBAAdapter()
        with patch.object(adapter, "_fetch_elo_ratings", return_value={}), \
             patch.object(adapter, "_compute_form", return_value=0.5), \
             patch.object(adapter, "_compute_rest_days", return_value=2):
            raw = adapter.fetch_all_data(_make_match())  # BOS vs LAL

        from app.sports.basketball.nba_team_ratings import ratings_for_team

        bos = ratings_for_team("Boston Celtics")
        lal = ratings_for_team("Los Angeles Lakers")
        assert bos is not None and lal is not None
        assert raw["custom"]["ortg_home"] == pytest.approx(bos["ortg"])
        assert raw["custom"]["drtg_home"] == pytest.approx(bos["drtg"])
        assert raw["custom"]["ortg_away"] == pytest.approx(lal["ortg"])
        assert raw["custom"]["drtg_away"] == pytest.approx(lal["drtg"])
        # Global stubs removed
        assert "pace_home" not in raw["custom"]
        assert "tpct_home" not in raw["custom"]

    def test_fetch_all_data_omits_ratings_when_unknown_teams(self):
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

        for key in (
            "ortg_home",
            "drtg_home",
            "ortg_away",
            "drtg_away",
            "pace_home",
            "pace_away",
            "tpct_home",
            "tpct_away",
        ):
            assert key not in raw["custom"], key
```

Ensure `pytest` is imported at top of `test_nba_adapter.py` (already added for injury tests if present; add if missing).

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterTeamRatings" -q --tb=short
```

Expected: FAIL — still has hard-coded stubs and/or missing table inject.

- [ ] **Step 3: Implement adapter changes**

In `backend/app/sports/basketball/nba_adapter.py`, replace the default `custom` dict so it **no longer** includes pace/ortg/drtg/tpct stubs — only b2b flags:

```python
            "custom": {
                # P1-B2: rest_days <= 1 treated as back-to-back
                "b2b_home": rest_home is not None and float(rest_home) <= 1.0,
                "b2b_away": rest_away is not None and float(rest_away) <= 1.0,
            },
```

After the injury enrich try/except (or after liquidity if preferred; order among soft enriches is flexible), add:

```python
        try:
            from app.sports.basketball.nba_team_ratings import ratings_for_team

            home_r = ratings_for_team(home_name)
            away_r = ratings_for_team(away_name)
            if home_r is not None and away_r is not None:
                raw["custom"]["ortg_home"] = float(home_r["ortg"])
                raw["custom"]["drtg_home"] = float(home_r["drtg"])
                raw["custom"]["ortg_away"] = float(away_r["ortg"])
                raw["custom"]["drtg_away"] = float(away_r["drtg"])
        except Exception:  # noqa: BLE001
            logger.debug("NBA team ratings enrich skipped", exc_info=True)
```

- [ ] **Step 4: Run adapter tests**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterTeamRatings" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterFetchAllData" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py::TestNBAAdapterInjuryImpact" `
  -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/basketball/nba_adapter.py backend/tests/test_nba_adapter.py
git commit -m "feat(nba): inject static team ORtg/DRtg; drop rating stubs (P1-B4)"
```

---

### Task 4: Docs + regression suite

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` (P1-B4 row)

**Interfaces:**
- Consumes: completed Tasks 1–3
- Produces: documentation + green related suite

- [ ] **Step 1: Update CHANGELOG**

At top of `## Unreleased` in `CHANGELOG.md`:

```markdown
### NBA static team ORtg/DRtg for net_rating (P1-B4)
- `nba_team_ratings`: 30-franchise static ORtg/DRtg (+ Clippers alias)
- Adapter injects four custom fields only when both sides resolve; omits otherwise
- Removes match-invariant ortg/drtg/pace/tpct stubs; BasketballEngine formula/weight unchanged
```

- [ ] **Step 2: Update backlog P1-B4 row**

Replace P1-B4 line with:

```markdown
| P1-B4 | ✅ 部分 2026-07-24：30 队静态 ORtg/DRtg → `custom` + BasketballEngine `net_rating` soft；真 possessions / 赛季动态源仍待 |
```

- [ ] **Step 3: Full related suite**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_team_ratings.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_nba_adapter.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_net_rating.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_engine.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_basketball_feature_builder.py" `
  -q --tb=short
```

Expected: PASS.  
Note: `test_basketball_feature_builder` still uses its **own** raw fixture with pace/ortg keys — that is fine (passthrough of caller-supplied custom, not adapter stubs).

- [ ] **Step 4: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(nba): P1-B4 static ORtg/DRtg changelog + backlog"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `nba_team_ratings.py` 30-team table + lookup | Task 2 |
| ORtg/DRtg band + strong/weak net direction | Task 1–2 |
| Clippers alias | Task 1–2 |
| Dual-side inject only | Task 3 |
| Omit all four when missing | Task 3 |
| Remove ortg/drtg/pace/tpct stubs | Task 3 |
| Engine formula/weight unchanged | Task 4 suite (no engine file edits) |
| CHANGELOG + backlog | Task 4 |

## Placeholder / consistency self-review

- No TBD steps; full table values inlined in Task 2.
- API names consistent: `PRIMARY_FRANCHISES`, `_TEAM_RATINGS`, `ratings_for_team`.
- OKC net > WAS net enforced by table values in Task 2.
- `ratings_for_team` returns a copy so mutation tests pass.
- Feature builder tests intentionally keep fixture custom keys; adapter no longer invents them.
