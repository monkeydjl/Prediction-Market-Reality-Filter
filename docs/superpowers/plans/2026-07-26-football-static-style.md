# Football Static Style Stats (P1-F6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefer code-local per-team possession / shots / PPDA over form-share possession proxy so MultiFactor soft `possession` reflects club style without live APIs or engine changes.

**Architecture:** Pure `football_style.py` owns a normalized-name static table + `stats_for_team`. After the existing form→possession proxy in `_shared.py`, if **both** home and away resolve from the table, overwrite `custom.possession_*`, `shots_*`, `ppda_*`, set `custom.style_source="static_table"`, and drop `possession_proxy`. FeatureBuilder and MultiFactor stay untouched.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network. No DB.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-football-static-style-design.md`
- Priority: **static table overwrites form proxy only when both sides hit**; single miss → leave proxy/empty
- Lookup: normalize lower + whitespace collapse; empty/unknown → `None`
- Dual-side only: never write one-sided static style fields
- `style_source="static_table"` only when static overwrite applied; remove `possession_proxy` when static wins
- Soft bands on return: possession_pct **[30, 75]**, shots_per90 **[5, 25]**, ppda **[5, 20]**
- Coverage: big-five + frequent UCL (~same keys as `football_xg`); not every lower-league club
- Do **not** change MultiFactor possession formula/weight or FeatureBuilder
- Do **not** call external stats APIs this round
- Fail-closed: exception in static enrich → debug log, leave existing fields
- Do **not** push to origin unless user explicitly asks
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`
- Prefer basetemp under repo for Windows pytest cleanup noise:
  `--basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6"`

## File Structure

### Created files
1. `backend/app/sports/football/football_style.py` — static table + `stats_for_team`
2. `backend/tests/test_football_style.py` — pure unit tests

### Modified files
1. `backend/app/sports/football/adapters/_shared.py` — after form possession proxy, dual-side static overwrite via helper
2. `backend/tests/test_adapter_shared.py` — enrich tests for overwrite / no partial / proxy fallback
3. `CHANGELOG.md` — Unreleased P1-F6 static note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F6 status line

### Unchanged (verify only)
1. `backend/app/sports/football/engines/football_multi_factor_engine.py` — soft possession path
2. `backend/app/sports/football/feature_builder.py` — custom passthrough

---

### Task 1: `stats_for_team` unit tests (RED)

**Files:**
- Create: `backend/tests/test_football_style.py`
- (No production implementation yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.football_style.stats_for_team`
- Produces: failing tests defining the lookup API for Task 2

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_football_style.py`:

```python
"""Tests for football_style.stats_for_team (P1-F6)."""
import pytest

from app.sports.football.football_style import stats_for_team


class TestStatsForTeam:
    def test_known_club_has_all_keys_in_band(self):
        s = stats_for_team("Arsenal")
        assert s is not None
        assert set(s.keys()) >= {"possession_pct", "shots_per90", "ppda"}
        assert 30.0 <= float(s["possession_pct"]) <= 75.0
        assert 5.0 <= float(s["shots_per90"]) <= 25.0
        assert 5.0 <= float(s["ppda"]) <= 20.0

    def test_top_possession_above_mid_table(self):
        top = stats_for_team("Manchester City")
        mid = stats_for_team("Everton")
        assert top is not None and mid is not None
        assert float(top["possession_pct"]) > float(mid["possession_pct"])

    def test_low_ppda_press_below_passive(self):
        # Lower PPDA = stronger press
        press = stats_for_team("Liverpool")
        passive = stats_for_team("Everton")
        assert press is not None and passive is not None
        assert float(press["ppda"]) < float(passive["ppda"])

    def test_unknown_returns_none(self):
        assert stats_for_team("NotAFootballClubXYZ") is None

    def test_empty_returns_none(self):
        assert stats_for_team("") is None
        assert stats_for_team("   ") is None

    def test_normalize_case_and_spaces(self):
        a = stats_for_team("Arsenal")
        b = stats_for_team("  arsenal  ")
        c = stats_for_team("ARSENAL")
        assert a is not None
        assert a == b == c

    def test_common_alias_man_city(self):
        primary = stats_for_team("Manchester City")
        alias = stats_for_team("Man City")
        assert primary is not None
        assert primary == alias

    def test_fixture_style_real_madrid_cf(self):
        s = stats_for_team("Real Madrid CF")
        assert s is not None
        assert 30.0 <= float(s["possession_pct"]) <= 75.0

    def test_fixture_style_bayern(self):
        s = stats_for_team("FC Bayern München")
        assert s is not None
        assert 5.0 <= float(s["shots_per90"]) <= 25.0
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_style.py -v --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6"
```

Expected: FAIL with `ImportError` / module not found for `football_style`.

- [ ] **Step 3: Commit failing tests**

```powershell
git add backend/tests/test_football_style.py
git commit -m "test(football): failing P1-F6 static style unit tests"
```

---

### Task 2: Implement `football_style` (GREEN)

**Files:**
- Create: `backend/app/sports/football/football_style.py`
- Test: `backend/tests/test_football_style.py`

**Interfaces:**
- Consumes: Task 1 test contract
- Produces:

```python
def stats_for_team(team_name: str) -> dict[str, float] | None:
    """Soft style stats for a club name, or None if unknown/empty.

    Returns keys: possession_pct, shots_per90, ppda.
    """
```

- [ ] **Step 1: Implement module**

Create `backend/app/sports/football/football_style.py` with:

1. Module docstring: soft static possession/shots/PPDA; operators update by PR; no network.
2. `_normalize(name: str) -> str` = `" ".join((name or "").lower().split())`
3. `_TEAM_STYLE: dict[str, tuple[float, float, float]]` keyed by **already-normalized** strings → `(possession_pct, shots_per90, ppda)`.
4. `stats_for_team` as below.

**Minimum table content (must include; may add more rows in same PR):**

Include at least these normalized keys. Values are soft consensus. Constraints for tests:
- `manchester city` possession_pct > `everton` possession_pct
- `liverpool` ppda < `everton` ppda
- All values within bands after clamp

```python
"""Static football style stats: possession / shots / PPDA (P1-F6).

Soft multi-year-ish consensus levels (not live scrape).
Missing / empty name → None. Engine formula lives in MultiFactor (unchanged).
"""
from __future__ import annotations

# Soft static style. Keys are _normalize()'d English fixture names.
# Values: (possession_pct, shots_per90, ppda). Lower PPDA = stronger press.
# Operators update by PR. Not a live season snapshot.
_TEAM_STYLE: dict[str, tuple[float, float, float]] = {
    # EPL
    "arsenal": (57.0, 14.5, 9.5),
    "aston villa": (54.0, 13.0, 10.5),
    "bournemouth": (48.0, 12.5, 11.5),
    "brentford": (47.0, 12.0, 12.0),
    "brighton": (56.0, 14.0, 10.0),
    "brighton and hove albion": (56.0, 14.0, 10.0),
    "chelsea": (58.0, 14.5, 9.8),
    "crystal palace": (45.0, 11.5, 12.5),
    "everton": (43.0, 11.0, 13.5),
    "fulham": (50.0, 12.0, 11.8),
    "ipswich": (42.0, 10.5, 14.0),
    "ipswich town": (42.0, 10.5, 14.0),
    "leicester": (44.0, 11.0, 13.0),
    "leicester city": (44.0, 11.0, 13.0),
    "liverpool": (60.0, 16.0, 8.5),
    "manchester city": (65.0, 17.5, 9.0),
    "man city": (65.0, 17.5, 9.0),
    "manchester united": (54.0, 13.5, 11.0),
    "man united": (54.0, 13.5, 11.0),
    "man utd": (54.0, 13.5, 11.0),
    "newcastle": (52.0, 13.5, 10.2),
    "newcastle united": (52.0, 13.5, 10.2),
    "nottingham forest": (44.0, 11.5, 12.8),
    "southampton": (46.0, 11.0, 12.5),
    "tottenham": (57.0, 15.0, 9.6),
    "tottenham hotspur": (57.0, 15.0, 9.6),
    "spurs": (57.0, 15.0, 9.6),
    "west ham": (46.0, 12.0, 12.2),
    "west ham united": (46.0, 12.0, 12.2),
    "wolves": (47.0, 11.5, 12.0),
    "wolverhampton": (47.0, 11.5, 12.0),
    "wolverhampton wanderers": (47.0, 11.5, 12.0),
    # La Liga
    "real madrid": (58.0, 16.0, 9.8),
    "real madrid cf": (58.0, 16.0, 9.8),
    "barcelona": (64.0, 15.5, 9.2),
    "fc barcelona": (64.0, 15.5, 9.2),
    "atletico madrid": (50.0, 12.5, 10.5),
    "atlético madrid": (50.0, 12.5, 10.5),
    "atletico de madrid": (50.0, 12.5, 10.5),
    "sevilla": (52.0, 12.0, 11.5),
    "real sociedad": (55.0, 13.0, 10.8),
    "villarreal": (53.0, 13.0, 11.0),
    "athletic bilbao": (51.0, 12.5, 10.5),
    "athletic club": (51.0, 12.5, 10.5),
    "real betis": (52.0, 12.5, 11.2),
    "girona": (56.0, 13.5, 10.5),
    # Serie A
    "inter": (57.0, 15.0, 9.5),
    "inter milan": (57.0, 15.0, 9.5),
    "internazionale": (57.0, 15.0, 9.5),
    "ac milan": (55.0, 14.0, 10.2),
    "milan": (55.0, 14.0, 10.2),
    "juventus": (53.0, 13.0, 11.0),
    "napoli": (56.0, 14.5, 10.0),
    "roma": (52.0, 13.0, 11.0),
    "as roma": (52.0, 13.0, 11.0),
    "lazio": (53.0, 13.0, 10.8),
    "atalanta": (54.0, 15.5, 9.2),
    "fiorentina": (54.0, 13.5, 10.5),
    # Bundesliga
    "bayern munich": (62.0, 17.0, 8.8),
    "fc bayern munich": (62.0, 17.0, 8.8),
    "bayern münchen": (62.0, 17.0, 8.8),
    "fc bayern münchen": (62.0, 17.0, 8.8),
    "borussia dortmund": (58.0, 15.0, 9.8),
    "dortmund": (58.0, 15.0, 9.8),
    "bvb": (58.0, 15.0, 9.8),
    "rb leipzig": (55.0, 14.5, 9.5),
    "leipzig": (55.0, 14.5, 9.5),
    "bayer leverkusen": (58.0, 15.5, 9.0),
    "leverkusen": (58.0, 15.5, 9.0),
    "eintracht frankfurt": (52.0, 13.0, 11.0),
    "wolfsburg": (50.0, 12.5, 11.5),
    "borussia monchengladbach": (51.0, 13.0, 11.2),
    "monchengladbach": (51.0, 13.0, 11.2),
    # Ligue 1
    "psg": (66.0, 16.5, 9.0),
    "paris saint-germain": (66.0, 16.5, 9.0),
    "paris saint germain": (66.0, 16.5, 9.0),
    "marseille": (54.0, 13.5, 10.8),
    "olympique marseille": (54.0, 13.5, 10.8),
    "lyon": (55.0, 13.5, 10.5),
    "olympique lyonnais": (55.0, 13.5, 10.5),
    "monaco": (54.0, 14.0, 10.2),
    "as monaco": (54.0, 14.0, 10.2),
    "lille": (53.0, 13.0, 10.8),
    "lens": (52.0, 12.5, 11.0),
    "nice": (51.0, 12.0, 11.5),
    # Other frequent UCL / European
    "ajax": (58.0, 14.5, 10.0),
    "porto": (56.0, 14.0, 10.5),
    "fc porto": (56.0, 14.0, 10.5),
    "benfica": (57.0, 14.5, 10.2),
    "sporting": (56.0, 14.0, 10.5),
    "sporting cp": (56.0, 14.0, 10.5),
    "sporting lisbon": (56.0, 14.0, 10.5),
    "celtic": (62.0, 15.0, 10.0),
    "rangers": (58.0, 14.0, 10.8),
    "galatasaray": (55.0, 14.0, 10.5),
    "fenerbahce": (54.0, 13.5, 11.0),
    "shakhtar donetsk": (55.0, 13.5, 10.8),
    "red star belgrade": (52.0, 12.5, 11.5),
    "club brugge": (54.0, 13.0, 11.0),
    "psv": (58.0, 15.0, 10.0),
    "psv eindhoven": (58.0, 15.0, 10.0),
    "feyenoord": (56.0, 14.5, 10.2),
    "salzburg": (55.0, 14.0, 9.8),
    "rb salzburg": (55.0, 14.0, 9.8),
    "dynamo kyiv": (52.0, 12.5, 11.5),
    "slavia prague": (54.0, 13.0, 11.0),
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def _clamp(val: float, lo: float, hi: float) -> float:
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def stats_for_team(team_name: str) -> dict[str, float] | None:
    """Return soft style stats for a club name, or None if unknown/empty."""
    key = _normalize(team_name)
    if not key:
        return None
    row = _TEAM_STYLE.get(key)
    if row is None:
        return None
    try:
        poss, shots, ppda = float(row[0]), float(row[1]), float(row[2])
    except (TypeError, ValueError, IndexError):
        return None
    return {
        "possession_pct": round(_clamp(poss, 30.0, 75.0), 1),
        "shots_per90": round(_clamp(shots, 5.0, 25.0), 2),
        "ppda": round(_clamp(ppda, 5.0, 20.0), 2),
    }
```

Do **not** import network/DB modules. Do **not** change engine files.

- [ ] **Step 2: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_style.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6"
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/football_style.py backend/tests/test_football_style.py
git commit -m "feat(football): static team style stats table (P1-F6)"
```

---

### Task 3: Wire static style overwrite + adapter tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` — add `enrich_style_features`; call it after form possession proxy in `fetch_raw_match_data`
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `stats_for_team(team_name: str) -> dict[str, float] | None`
- Produces: when both hit →  
  `custom.possession_home/away`, `shots_home/away`, `ppda_home/away`,  
  `style_source="static_table"`, and `possession_proxy` removed if present

- [ ] **Step 1: Add adapter tests first**

In `backend/tests/test_adapter_shared.py`:

1. Import `enrich_style_features` from `_shared` (add to existing import list).
2. Append class:

```python
class TestStaticStyleOverwrite:
    def test_both_static_hits_overwrite_proxy(self):
        match = _make_match("ucl-style-static")
        raw = {
            "team": {"form_home": 0.4, "form_away": 0.6},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {
                # Simulate form_share proxy already applied
                "possession_home": 40.0,
                "possession_away": 60.0,
                "possession_proxy": "form_share",
            },
        }
        enrich_style_features(raw, match)

        from app.sports.football.football_style import stats_for_team

        home = stats_for_team("Real Madrid CF")
        away = stats_for_team("FC Bayern München")
        assert home is not None and away is not None
        assert raw["custom"]["possession_home"] == pytest.approx(home["possession_pct"])
        assert raw["custom"]["possession_away"] == pytest.approx(away["possession_pct"])
        assert raw["custom"]["shots_home"] == pytest.approx(home["shots_per90"])
        assert raw["custom"]["shots_away"] == pytest.approx(away["shots_per90"])
        assert raw["custom"]["ppda_home"] == pytest.approx(home["ppda"])
        assert raw["custom"]["ppda_away"] == pytest.approx(away["ppda"])
        assert raw["custom"]["style_source"] == "static_table"
        assert "possession_proxy" not in raw["custom"]
        # Must not remain form proxy values
        assert raw["custom"]["possession_home"] != pytest.approx(40.0)

    def test_one_side_unknown_keeps_proxy(self):
        match = MatchIdentity(
            match_id="ucl-style-partial",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            away=TeamIdentity(code="ZZZ", name="Unknown Club XYZ", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {
                "possession_home": 55.0,
                "possession_away": 45.0,
                "possession_proxy": "form_share",
            },
        }
        enrich_style_features(raw, match)

        assert raw["custom"].get("possession_home") == pytest.approx(55.0)
        assert raw["custom"].get("possession_away") == pytest.approx(45.0)
        assert raw["custom"].get("possession_proxy") == "form_share"
        assert "style_source" not in raw["custom"]
        assert "shots_home" not in raw["custom"]
        assert "ppda_home" not in raw["custom"]

    def test_both_unknown_no_static_source(self):
        match = MatchIdentity(
            match_id="ucl-style-none",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="AAA", name="NoSuchHome FC", competition=_UCL),
            away=TeamIdentity(code="BBB", name="NoSuchAway FC", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {
                "possession_home": 50.0,
                "possession_away": 50.0,
                "possession_proxy": "form_share",
            },
        }
        enrich_style_features(raw, match)

        assert raw["custom"].get("possession_home") == pytest.approx(50.0)
        assert raw["custom"].get("possession_proxy") == "form_share"
        assert "style_source" not in raw["custom"]
        assert "shots_home" not in raw["custom"]
        assert "ppda_home" not in raw["custom"]
```

- [ ] **Step 2: Run adapter static-style tests — expect FAIL**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticStyleOverwrite -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6"
```

Expected: FAIL (`enrich_style_features` not importable / not defined).

- [ ] **Step 3: Implement `enrich_style_features` and call site**

In `backend/app/sports/football/adapters/_shared.py`:

**A. Add function** (near other enrich helpers, e.g. after `enrich_referee_features` or after `enrich_situational_features`):

```python
def enrich_style_features(raw: dict, match: MatchIdentity) -> None:
    """Static possession/shots/PPDA (P1-F6): overwrite form proxy only when both sides resolve."""
    try:
        from app.sports.football.football_style import stats_for_team

        home_name = match.home.name if match.home else ""
        away_name = match.away.name if match.away else ""
        sh = stats_for_team(home_name)
        sa = stats_for_team(away_name)
        if sh is None or sa is None:
            return
        custom = raw.setdefault("custom", {})
        custom["possession_home"] = float(sh["possession_pct"])
        custom["possession_away"] = float(sa["possession_pct"])
        custom["shots_home"] = float(sh["shots_per90"])
        custom["shots_away"] = float(sa["shots_per90"])
        custom["ppda_home"] = float(sh["ppda"])
        custom["ppda_away"] = float(sa["ppda"])
        custom["style_source"] = "static_table"
        custom.pop("possession_proxy", None)
    except Exception:  # noqa: BLE001
        logger.debug("Static style enrichment failed", exc_info=True)
```

**B. Wire after form possession proxy** in `fetch_raw_match_data` (immediately after the existing `except` that logs `"possession proxy skipped"`):

```python
    enrich_style_features(raw, match)
```

Do **not** remove the form proxy block. Order must remain: form proxy → static style.

- [ ] **Step 4: Run adapter + unit tests — expect PASS**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_style.py tests/test_adapter_shared.py::TestStaticStyleOverwrite -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6"
```

Expected: all PASS. (Windows basetemp cleanup PermissionError may appear after assertions — ignore if tests green.)

- [ ] **Step 5: Smoke MultiFactor possession still green (regression)**

```powershell
C:\Python314\python.exe -m pytest tests/ -k "possession or style or xg" --tb=line -q --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6" 2>&1 | Select-Object -Last 40
```

Expected: no new failures related to possession/style. If the `-k` set is large/noisy, at minimum re-run:

```powershell
C:\Python314\python.exe -m pytest tests/test_football_style.py tests/test_adapter_shared.py::TestStaticStyleOverwrite tests/test_adapter_shared.py::TestStaticXgOverwrite -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f6"
```

- [ ] **Step 6: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): static style overwrite in enrich (P1-F6)"
```

---

### Task 4: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

**Interfaces:**
- Consumes: implemented behavior from Tasks 1–3
- Produces: operator-facing status strings

- [ ] **Step 1: CHANGELOG Unreleased**

At top of `## Unreleased` (above P1-F5 section), add:

```markdown
### Football static style stats (P1-F6)

- Soft code-local per-club possession / shots / PPDA table (`football_style.stats_for_team`); when **both** sides resolve, overwrite form-share possession proxy and write shots/ppda with `custom.style_source=static_table`. MultiFactor soft possession path unchanged. True stats API still pending.
```

- [ ] **Step 2: Backlog P1-F6 row**

In `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`, replace the P1-F6 status cell with:

```markdown
| P1-F6 | PPDA / possession / shots | ✅ 部分 2026-07-26：静态 `stats_for_team` 双方命中覆盖 `custom.possession_*`/`shots_*`/`ppda_*`（form_share 代理回退）；真统计 API 仍待 |
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F6 static style changelog + backlog"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Pure `football_style.py` + `stats_for_team` | 1–2 |
| Dual-side overwrite of possession/shots/ppda | 3 |
| `style_source=static_table`; drop `possession_proxy` | 3 |
| Leave form proxy when incomplete | 3 |
| MultiFactor / FeatureBuilder unchanged | 3 regression + constraints |
| No network / no flag / no DB | all |
| CHANGELOG + backlog | 4 |
| TDD RED→GREEN | 1–3 |

## Placeholder / consistency scan

- No TBD/TODO placeholders.
- API name consistent: `stats_for_team` / `enrich_style_features` / `style_source`.
- Custom keys match engine: `possession_*`, `shots_*`, `ppda_*`.
- Fixture names in tests match `_make_match`: `Real Madrid CF`, `FC Bayern München`.
