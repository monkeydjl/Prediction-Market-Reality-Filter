# Football Static xG Table (P1-F5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefer code-local per-team attack xG/90 over goals-per-game proxy so MultiFactor soft `xg` reflects club strength without live APIs or engine changes.

**Architecture:** Pure `football_xg.py` owns a normalized-name static table + `xg_for_team`. `enrich_situational_features` keeps goals proxy writes first; if **both** home and away resolve from the table, overwrite `custom.xg_*` and set `custom.xg_source="static_table"`. FeatureBuilder and MultiFactor stay untouched.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network. No DB.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-football-static-xg-design.md`
- Priority: **static table overwrites goals proxy only when both sides hit**; single miss → leave proxy/empty
- Lookup: normalize lower + whitespace collapse; empty/unknown → `None`
- Dual-side only: never write one-sided static xG
- `xg_source="static_table"` only when static overwrite applied
- Soft xG/90 band roughly **[0.8, 2.5]**; directional ordering for named checks
- Coverage: big-five + frequent UCL (~80–120 rows); not every lower-league club
- Do **not** change MultiFactor xG formula/weight or FeatureBuilder
- Do **not** call API-Football / Understat / FBref this round
- Fail-closed: exception in static enrich → debug log, leave existing fields
- Do **not** push to origin unless user explicitly asks
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`
- Prefer basetemp under repo for Windows pytest cleanup noise:
  `--basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"`

## File Structure

### Created files
1. `backend/app/sports/football/football_xg.py` — static table + `xg_for_team`
2. `backend/tests/test_football_xg.py` — pure unit tests

### Modified files
1. `backend/app/sports/football/adapters/_shared.py` — after goals proxy, dual-side static overwrite
2. `backend/tests/test_adapter_shared.py` — enrich tests for overwrite / no partial / proxy fallback
3. `CHANGELOG.md` — Unreleased P1-F5 static note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F5 status line

### Unchanged (verify only)
1. `backend/app/sports/football/engines/football_multi_factor_engine.py` — soft xg path
2. `backend/app/sports/football/feature_builder.py` — custom passthrough

---

### Task 1: `xg_for_team` unit tests (RED)

**Files:**
- Create: `backend/tests/test_football_xg.py`
- (No production implementation yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.football_xg.xg_for_team`
- Produces: failing tests defining the lookup API for Task 2

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_football_xg.py`:

```python
"""Tests for football_xg.xg_for_team (P1-F5)."""
import pytest

from app.sports.football.football_xg import xg_for_team


class TestXgForTeam:
    def test_known_top_club_in_band(self):
        xg = xg_for_team("Arsenal")
        assert xg is not None
        assert 0.8 <= float(xg) <= 2.5

    def test_top_attack_above_mid_table(self):
        top = xg_for_team("Manchester City")
        mid = xg_for_team("Everton")
        assert top is not None and mid is not None
        assert float(top) > float(mid)

    def test_unknown_returns_none(self):
        assert xg_for_team("NotAFootballClubXYZ") is None

    def test_empty_returns_none(self):
        assert xg_for_team("") is None
        assert xg_for_team("   ") is None

    def test_normalize_case_and_spaces(self):
        a = xg_for_team("Arsenal")
        b = xg_for_team("  arsenal  ")
        c = xg_for_team("ARSENAL")
        assert a is not None
        assert a == b == c

    def test_common_alias_man_city(self):
        primary = xg_for_team("Manchester City")
        alias = xg_for_team("Man City")
        assert primary is not None
        assert primary == alias

    def test_fixture_style_real_madrid_cf(self):
        # _make_match in adapter tests uses "Real Madrid CF"
        xg = xg_for_team("Real Madrid CF")
        assert xg is not None
        assert 0.8 <= float(xg) <= 2.5

    def test_fixture_style_bayern(self):
        xg = xg_for_team("FC Bayern München")
        assert xg is not None
        assert 0.8 <= float(xg) <= 2.5
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_xg.py -v --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```

Expected: FAIL with `ImportError` / module not found for `football_xg`.

- [ ] **Step 3: Commit failing tests**

```powershell
git add backend/tests/test_football_xg.py
git commit -m "test(football): failing P1-F5 static xG unit tests"
```

---

### Task 2: Implement `football_xg` (GREEN)

**Files:**
- Create: `backend/app/sports/football/football_xg.py`
- Test: `backend/tests/test_football_xg.py`

**Interfaces:**
- Consumes: Task 1 test contract
- Produces:

```python
def xg_for_team(team_name: str) -> float | None:
    """Soft attack xG per 90 for a club name, or None if unknown/empty."""
```

- [ ] **Step 1: Implement module**

Create `backend/app/sports/football/football_xg.py` with:

1. Module docstring: soft static xG/90; operators update by PR; no network.
2. `_normalize(name: str) -> str` = `" ".join((name or "").lower().split())`
3. `_TEAM_XG: dict[str, float]` keyed by **already-normalized** strings.
4. `xg_for_team` as below.

**Minimum table content (must include; may add more rows in same PR):**

Include at least these normalized keys with soft values in **[0.8, 2.5]** and `manchester city` > `everton`:

```python
"""Static football attack xG per 90 (P1-F5).

Soft multi-year-ish consensus levels (not live scrape).
Missing / empty name → None. Engine formula lives in MultiFactor (unchanged).
"""
from __future__ import annotations

# Soft static attack xG/90. Keys are _normalize()'d English fixture names.
# Operators update by PR. Not a live season snapshot.
_TEAM_XG: dict[str, float] = {
    # EPL
    "arsenal": 1.85,
    "aston villa": 1.55,
    "bournemouth": 1.45,
    "brentford": 1.40,
    "brighton": 1.50,
    "brighton and hove albion": 1.50,
    "chelsea": 1.75,
    "crystal palace": 1.35,
    "everton": 1.20,
    "fulham": 1.40,
    "ipswich": 1.15,
    "ipswich town": 1.15,
    "leicester": 1.25,
    "leicester city": 1.25,
    "liverpool": 2.05,
    "manchester city": 2.15,
    "man city": 2.15,
    "manchester united": 1.60,
    "man united": 1.60,
    "man utd": 1.60,
    "newcastle": 1.65,
    "newcastle united": 1.65,
    "nottingham forest": 1.35,
    "southampton": 1.15,
    "tottenham": 1.70,
    "tottenham hotspur": 1.70,
    "spurs": 1.70,
    "west ham": 1.40,
    "west ham united": 1.40,
    "wolves": 1.30,
    "wolverhampton": 1.30,
    "wolverhampton wanderers": 1.30,
    # La Liga
    "real madrid": 2.10,
    "real madrid cf": 2.10,
    "barcelona": 2.00,
    "fc barcelona": 2.00,
    "atletico madrid": 1.55,
    "atlético madrid": 1.55,
    "atletico de madrid": 1.55,
    "sevilla": 1.35,
    "real sociedad": 1.45,
    "villarreal": 1.50,
    "athletic bilbao": 1.45,
    "athletic club": 1.45,
    "real betis": 1.40,
    "girona": 1.45,
    # Serie A
    "inter": 1.90,
    "inter milan": 1.90,
    "internazionale": 1.90,
    "ac milan": 1.70,
    "milan": 1.70,
    "juventus": 1.65,
    "napoli": 1.80,
    "roma": 1.55,
    "as roma": 1.55,
    "lazio": 1.50,
    "atalanta": 1.75,
    "fiorentina": 1.45,
    # Bundesliga
    "bayern munich": 2.20,
    "fc bayern munich": 2.20,
    "bayern münchen": 2.20,
    "fc bayern münchen": 2.20,
    "borussia dortmund": 1.85,
    "dortmund": 1.85,
    "bvb": 1.85,
    "rb leipzig": 1.75,
    "leipzig": 1.75,
    "bayer leverkusen": 1.90,
    "leverkusen": 1.90,
    "eintracht frankfurt": 1.50,
    "wolfsburg": 1.35,
    "borussia monchengladbach": 1.40,
    "monchengladbach": 1.40,
    # Ligue 1
    "psg": 2.15,
    "paris saint-germain": 2.15,
    "paris saint germain": 2.15,
    "marseille": 1.55,
    "olympique marseille": 1.55,
    "lyon": 1.50,
    "olympique lyonnais": 1.50,
    "monaco": 1.65,
    "as monaco": 1.65,
    "lille": 1.50,
    "lens": 1.45,
    "nice": 1.40,
    # Other frequent UCL / European
    "ajax": 1.55,
    "porto": 1.50,
    "fc porto": 1.50,
    "benfica": 1.55,
    "sporting": 1.50,
    "sporting cp": 1.50,
    "sporting lisbon": 1.50,
    "celtic": 1.45,
    "rangers": 1.40,
    "galatasaray": 1.50,
    "fenerbahce": 1.45,
    "shakhtar donetsk": 1.40,
    "red star belgrade": 1.30,
    "club brugge": 1.35,
    "psv": 1.55,
    "psv eindhoven": 1.55,
    "feyenoord": 1.50,
    "salzburg": 1.45,
    "rb salzburg": 1.45,
    "dynamo kyiv": 1.25,
    "slavia prague": 1.30,
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def xg_for_team(team_name: str) -> float | None:
    """Return soft attack xG per 90 for a club name, or None if unknown/empty."""
    key = _normalize(team_name)
    if not key:
        return None
    val = _TEAM_XG.get(key)
    if val is None:
        return None
    try:
        xg = float(val)
    except (TypeError, ValueError):
        return None
    if xg < 0.8:
        xg = 0.8
    elif xg > 2.5:
        xg = 2.5
    return round(xg, 4)
```

Do **not** import network/DB modules. Do **not** change engine files.

- [ ] **Step 2: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_xg.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/football_xg.py backend/tests/test_football_xg.py
git commit -m "feat(football): static team xG/90 table (P1-F5)"
```

---

### Task 3: Wire enrich overwrite + adapter tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (after goals proxy ~337–354, before H2H block)
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `xg_for_team(team_name: str) -> float | None`
- Produces: when both hit → `raw["custom"]["xg_home"]`, `xg_away`, `xg_source="static_table"`

- [ ] **Step 1: Add adapter tests first**

Append to `backend/tests/test_adapter_shared.py` (reuse `_make_match`; ensure `pytest` / `patch` already imported):

```python
class TestStaticXgOverwrite:
    def test_both_static_hits_overwrite_proxy(self):
        match = _make_match("ucl-xg-static")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        # Proxy would write 1.1 / 1.1; static for Real Madrid CF / Bayern must win
        hist = {
            "wins": 5,
            "draws": 2,
            "losses": 3,
            "played": 10,
            "goals_per_game": 1.1,
            "last_match_date": "2025-09-01",
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=hist,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        from app.sports.football.football_xg import xg_for_team

        assert raw["custom"]["xg_home"] == pytest.approx(
            float(xg_for_team("Real Madrid CF")),
        )
        assert raw["custom"]["xg_away"] == pytest.approx(
            float(xg_for_team("FC Bayern München")),
        )
        assert raw["custom"]["xg_source"] == "static_table"
        # Must not remain goals proxy
        assert raw["custom"]["xg_home"] != pytest.approx(1.1)

    def test_one_side_unknown_keeps_proxy(self):
        match = MatchIdentity(
            match_id="ucl-xg-partial",
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
            "custom": {},
        }
        hist = {
            "wins": 4,
            "draws": 3,
            "losses": 3,
            "played": 10,
            "goals_per_game": 1.25,
            "last_match_date": "2025-09-01",
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=hist,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert raw["custom"].get("xg_home") == pytest.approx(1.25)
        assert raw["custom"].get("xg_away") == pytest.approx(1.25)
        assert "xg_source" not in raw["custom"]

    def test_both_unknown_no_static_source(self):
        match = MatchIdentity(
            match_id="ucl-xg-none",
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
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "xg_home" not in raw["custom"]
        assert "xg_away" not in raw["custom"]
        assert "xg_source" not in raw["custom"]
```

- [ ] **Step 2: Run adapter static-xG tests — expect FAIL**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticXgOverwrite -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```

Expected: FAIL until enrich wired (proxy 1.1 remains / no `xg_source`).

- [ ] **Step 3: Implement enrich static overwrite**

In `enrich_situational_features` in `_shared.py`, **immediately after** the home/away goals-proxy blocks (after away `xg_away` proxy write, **before** H2H), insert:

```python
    # Static xG/90 (P1-F5): overwrite goals proxy only when both sides resolve
    try:
        from app.sports.football.football_xg import xg_for_team

        xh = xg_for_team(home_name)
        xa = xg_for_team(away_name)
        if xh is not None and xa is not None:
            custom = raw.setdefault("custom", {})
            custom["xg_home"] = float(xh)
            custom["xg_away"] = float(xa)
            custom["xg_source"] = "static_table"
    except Exception:  # noqa: BLE001
        logger.debug("Static xG enrichment failed", exc_info=True)
```

Notes:
- Do not remove goals proxy writes above — they remain fallback when static incomplete.
- Existing `test_enrich_form_and_h2h` uses Real Madrid CF / Bayern → after this change `xg_home` will be **static**, not `1.8`. **Update that assertion** in the same task:

In `TestFetchEloAndOdds.test_enrich_form_and_h2h`, replace:

```python
        assert raw["custom"]["xg_home"] == 1.8
```

with:

```python
        from app.sports.football.football_xg import xg_for_team

        assert raw["custom"]["xg_home"] == pytest.approx(
            float(xg_for_team("Real Madrid CF")),
        )
        assert raw["custom"]["xg_away"] == pytest.approx(
            float(xg_for_team("FC Bayern München")),
        )
        assert raw["custom"]["xg_source"] == "static_table"
```

- [ ] **Step 4: Run focused tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_xg.py tests/test_adapter_shared.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```

Expected: PASS (ignore Windows tmp PermissionError if all assertions green).

- [ ] **Step 5: Smoke multi-factor xg (no engine edits)**

```powershell
C:\Python314\python.exe -m pytest tests/test_football_multi_factor_engine.py -k xg -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```

Expected: xg-related tests PASS. Do not fix unrelated pre-existing multi_factor failures.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): static xG overwrite in enrich (P1-F5)"
```

---

### Task 4: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

**Interfaces:**
- Consumes: implemented behavior from Tasks 2–3
- Produces: documentation only

- [ ] **Step 1: CHANGELOG**

Under `## Unreleased`, add near other football entries:

```markdown
### Football static xG table (P1-F5)
- `football_xg`: code-local attack xG/90 by normalized club name (big-five + UCL-ish)
- Enrich: goals_per_game proxy first; both-sides static hit overwrites `xg_*` + `xg_source=static_table`
- MultiFactor xG formula/weight unchanged; true xG API still pending
```

- [ ] **Step 2: Backlog P1-F5 row**

Replace P1-F5 status cell with:

```markdown
| P1-F5 | 真实 xG | ✅ 部分 2026-07-26：静态 `xg_for_team` 双方命中覆盖 `custom.xg_*`（goals 代理回退）；真 xG API 仍待 | MultiFactor soft xg 已在 |
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F5 static xG changelog + backlog"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| `football_xg` pure module + table | Task 1–2 |
| `xg_for_team` normalize / None | Task 1–2 |
| Dual-side overwrite only | Task 3 |
| Goals proxy fallback | Task 3 |
| `xg_source=static_table` | Task 3 |
| MultiFactor / FeatureBuilder unchanged | Task 3 smoke |
| Fail-closed exception | Task 3 |
| Coverage big-five + UCL-ish + fixture aliases | Task 2 table |
| CHANGELOG + backlog | Task 4 |

Placeholder scan: none.  
Type consistency: `xg_for_team(str) -> float | None` used in Tasks 1–3; custom keys `xg_home` / `xg_away` / `xg_source`.
