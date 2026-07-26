# Football Static Climate Weather Fill (P1-F7 residual) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pass through fixture weather when present, otherwise fill soft home-city×month climate into football adapter environment so FeatureBuilder sees non-empty weather without network or MultiFactor changes.

**Architecture:** Pure `football_weather.py` owns normalized home-team climate table + `climate_for_home(team, month)`. `enrich_weather_features` pass-through first; fill-only writes `environment.weather_temp_c` / `weather_condition` + `custom.weather_source="static_climate"`. MultiFactor stays untouched (no weather factor this round).

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network. No DB.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-football-static-weather-design.md`
- Pass-through first: if environment/custom already has usable `weather_temp_c` **or** `weather_condition` → normalize, **do not** overwrite with static
- Fill only when **both** temp and condition are still missing after pass-through
- Lookup: normalize lower + whitespace collapse; empty/unknown team → `None`
- Month must be **1–12** or lookup returns `None`
- Kickoff month from `match.kickoff_utc` (UTC); missing kickoff → no static fill
- Return: `temp_c` clamp **[-15, 45]**, `round(1)`; `condition` ∈ `{clear, mild, rain, cold, hot}`
- Coverage: big-five + UCL-common clubs, **≥20** unique clubs with 12-month rows
- Source string on static hit: exactly `static_climate`
- Write **environment** fields (FeatureBuilder reads them); also mirror to **custom** for consistency with other soft sources
- Do **not** add MultiFactor weather factor or change weights
- Do **not** call Open-Meteo / any network this round
- Do **not** push to origin unless user explicitly asks
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`
- Prefer basetemp:
  `--basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7w"`

## File Structure

### Created files
1. `backend/app/sports/football/football_weather.py` — climate table + `climate_for_home`
2. `backend/tests/test_football_weather.py` — pure unit tests

### Modified files
1. `backend/app/sports/football/adapters/_shared.py` — `enrich_weather_features` + wire after altitude
2. `backend/tests/test_adapter_shared.py` — weather pass-through / fill / no-overwrite tests
3. `CHANGELOG.md` — Unreleased P1-F7 weather note
4. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F7 status line update

### Unchanged (verify only)
1. `backend/app/sports/football/engines/football_multi_factor_engine.py`
2. `backend/app/sports/football/feature_builder.py` — already maps env weather fields

---

### Task 1: `climate_for_home` unit tests (RED)

**Files:**
- Create: `backend/tests/test_football_weather.py`
- (No production implementation yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.football_weather.climate_for_home`
- Produces: failing tests defining the lookup API for Task 2

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_football_weather.py`:

```python
"""Tests for football_weather.climate_for_home (P1-F7 residual)."""
from app.sports.football.football_weather import climate_for_home

_CONDITIONS = {"clear", "mild", "rain", "cold", "hot"}


class TestClimateForHome:
    def test_known_club_month_has_keys_in_band(self):
        c = climate_for_home("Arsenal", 9)
        assert c is not None
        assert set(c.keys()) >= {"temp_c", "condition"}
        assert -15.0 <= float(c["temp_c"]) <= 45.0
        assert c["condition"] in _CONDITIONS

    def test_unknown_returns_none(self):
        assert climate_for_home("NotAFootballClubXYZ", 6) is None

    def test_empty_returns_none(self):
        assert climate_for_home("", 6) is None
        assert climate_for_home("   ", 6) is None

    def test_bad_month_returns_none(self):
        assert climate_for_home("Arsenal", 0) is None
        assert climate_for_home("Arsenal", 13) is None

    def test_normalize_case_and_spaces(self):
        a = climate_for_home("Arsenal", 6)
        b = climate_for_home("  arsenal  ", 6)
        c = climate_for_home("ARSENAL", 6)
        assert a is not None
        assert a == b == c

    def test_northern_winter_colder_than_summer(self):
        winter = climate_for_home("Manchester United", 1)
        summer = climate_for_home("Manchester United", 7)
        assert winter is not None and summer is not None
        assert float(winter["temp_c"]) < float(summer["temp_c"])

    def test_mediterranean_warmer_winter_than_scotland(self):
        seville = climate_for_home("Sevilla", 1)
        celtic = climate_for_home("Celtic", 1)
        assert seville is not None and celtic is not None
        assert float(seville["temp_c"]) > float(celtic["temp_c"])
```

- [ ] **Step 2: Run tests — expect RED**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_weather.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7w"
```

Expected: FAIL (module or `climate_for_home` import missing).

- [ ] **Step 3: Commit**

```powershell
git add backend/tests/test_football_weather.py
git commit -m "test(football): failing P1-F7 climate_for_home unit tests"
```

---

### Task 2: Implement `football_weather` module (GREEN)

**Files:**
- Create: `backend/app/sports/football/football_weather.py`
- Test: `backend/tests/test_football_weather.py`

**Interfaces:**
- Produces:

```python
def climate_for_home(team_name: str, month: int) -> dict[str, float | str] | None:
    ...
```

- [ ] **Step 1: Implement module**

Create `backend/app/sports/football/football_weather.py` with seasonal templates expanded to 12 months (keeps table maintainable while meeting ≥20 clubs). Use the following structure **verbatim enough** that tests pass; soft temps are priors, not forecasts:

```python
"""Static football home-city climate by month (P1-F7 residual weather).

Soft multi-year climate priors (not live forecasts). Missing / empty /
bad month → None. MultiFactor does not consume weather this round.
"""
from __future__ import annotations

_CONDITIONS = frozenset({"clear", "mild", "rain", "cold", "hot"})

# Seasonal templates: (DJF, MAM, JJA, SON) each (temp_c, condition)
_TEMPLATES: dict[str, tuple[tuple[float, str], tuple[float, str], tuple[float, str], tuple[float, str]]] = {
    "london": ((5.0, "rain"), (11.0, "mild"), (18.0, "mild"), (12.0, "rain")),
    "manchester": ((4.0, "rain"), (10.0, "mild"), (17.0, "mild"), (11.0, "rain")),
    "liverpool": ((5.0, "rain"), (10.0, "mild"), (17.0, "mild"), (11.0, "rain")),
    "birmingham": ((4.5, "rain"), (10.5, "mild"), (17.5, "mild"), (11.5, "rain")),
    "newcastle": ((4.0, "cold"), (9.0, "mild"), (16.0, "mild"), (10.0, "rain")),
    "madrid": ((7.0, "mild"), (14.0, "clear"), (28.0, "hot"), (16.0, "clear")),
    "barcelona": ((10.0, "mild"), (15.0, "mild"), (26.0, "hot"), (18.0, "clear")),
    "seville": ((12.0, "mild"), (17.0, "clear"), (30.0, "hot"), (20.0, "clear")),
    "bilbao": ((9.0, "rain"), (13.0, "mild"), (21.0, "mild"), (15.0, "rain")),
    "milan": ((4.0, "cold"), (13.0, "mild"), (25.0, "hot"), (14.0, "mild")),
    "rome": ((8.0, "mild"), (14.0, "mild"), (27.0, "hot"), (17.0, "clear")),
    "naples": ((10.0, "mild"), (15.0, "mild"), (27.0, "hot"), (18.0, "clear")),
    "turin": ((3.0, "cold"), (12.0, "mild"), (24.0, "hot"), (13.0, "mild")),
    "munich": ((0.0, "cold"), (10.0, "mild"), (19.0, "mild"), (10.0, "rain")),
    "dortmund": ((2.0, "cold"), (10.0, "mild"), (19.0, "mild"), (11.0, "rain")),
    "leipzig": ((0.5, "cold"), (10.0, "mild"), (20.0, "mild"), (10.5, "rain")),
    "paris": ((5.0, "rain"), (12.0, "mild"), (21.0, "mild"), (13.0, "mild")),
    "marseille": ((9.0, "mild"), (14.0, "clear"), (26.0, "hot"), (17.0, "clear")),
    "lyon": ((4.0, "cold"), (12.0, "mild"), (23.0, "hot"), (13.0, "mild")),
    "amsterdam": ((4.0, "rain"), (10.0, "mild"), (18.0, "mild"), (11.0, "rain")),
    "lisbon": ((12.0, "mild"), (15.0, "mild"), (24.0, "hot"), (18.0, "clear")),
    "porto": ((10.0, "rain"), (14.0, "mild"), (21.0, "mild"), (16.0, "rain")),
    "glasgow": ((3.0, "cold"), (8.0, "rain"), (15.0, "mild"), (9.0, "rain")),
    "istanbul": ((6.0, "cold"), (12.0, "mild"), (24.0, "hot"), (15.0, "mild")),
}


def _months_from_template(
    tpl: tuple[tuple[float, str], tuple[float, str], tuple[float, str], tuple[float, str]],
) -> list[tuple[float, str]]:
    """Expand DJF/MAM/JJA/SON into 12 (temp, condition) rows (Jan=1 index 0)."""
    djf, mam, jja, son = tpl
    out: list[tuple[float, str]] = []
    for m in range(1, 13):
        if m in (12, 1, 2):
            out.append(djf)
        elif m in (3, 4, 5):
            out.append(mam)
        elif m in (6, 7, 8):
            out.append(jja)
        else:
            out.append(son)
    return out


# club normalize key → template name
_CLUB_TEMPLATE: dict[str, str] = {
    # EPL / England
    "arsenal": "london",
    "chelsea": "london",
    "tottenham": "london",
    "tottenham hotspur": "london",
    "spurs": "london",
    "west ham": "london",
    "west ham united": "london",
    "crystal palace": "london",
    "fulham": "london",
    "brentford": "london",
    "manchester city": "manchester",
    "man city": "manchester",
    "manchester united": "manchester",
    "man united": "manchester",
    "man utd": "manchester",
    "liverpool": "liverpool",
    "everton": "liverpool",
    "aston villa": "birmingham",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "brighton": "london",
    "brighton and hove albion": "london",
    "wolves": "birmingham",
    "wolverhampton": "birmingham",
    "wolverhampton wanderers": "birmingham",
    "nottingham forest": "birmingham",
    # Spain
    "real madrid": "madrid",
    "real madrid cf": "madrid",
    "atletico madrid": "madrid",
    "atlético madrid": "madrid",
    "atletico de madrid": "madrid",
    "barcelona": "barcelona",
    "fc barcelona": "barcelona",
    "sevilla": "seville",
    "real betis": "seville",
    "athletic bilbao": "bilbao",
    "athletic club": "bilbao",
    "real sociedad": "bilbao",
    "villarreal": "barcelona",
    "girona": "barcelona",
    # Italy
    "inter": "milan",
    "inter milan": "milan",
    "internazionale": "milan",
    "ac milan": "milan",
    "milan": "milan",
    "juventus": "turin",
    "napoli": "naples",
    "roma": "rome",
    "as roma": "rome",
    "lazio": "rome",
    "atalanta": "milan",
    "fiorentina": "rome",
    # Germany
    "bayern munich": "munich",
    "fc bayern munich": "munich",
    "bayern münchen": "munich",
    "fc bayern münchen": "munich",
    "borussia dortmund": "dortmund",
    "dortmund": "dortmund",
    "bvb": "dortmund",
    "rb leipzig": "leipzig",
    "leipzig": "leipzig",
    "bayer leverkusen": "dortmund",
    "leverkusen": "dortmund",
    "eintracht frankfurt": "munich",
    # France
    "psg": "paris",
    "paris saint-germain": "paris",
    "paris saint germain": "paris",
    "marseille": "marseille",
    "olympique marseille": "marseille",
    "lyon": "lyon",
    "olympique lyonnais": "lyon",
    "monaco": "marseille",
    "as monaco": "marseille",
    "lille": "paris",
    "lens": "paris",
    "nice": "marseille",
    # Europe
    "ajax": "amsterdam",
    "psv": "amsterdam",
    "psv eindhoven": "amsterdam",
    "feyenoord": "amsterdam",
    "porto": "porto",
    "fc porto": "porto",
    "benfica": "lisbon",
    "sporting": "lisbon",
    "sporting cp": "lisbon",
    "sporting lisbon": "lisbon",
    "celtic": "glasgow",
    "rangers": "glasgow",
    "galatasaray": "istanbul",
    "fenerbahce": "istanbul",
}

_MONTHLY: dict[str, list[tuple[float, str]]] = {
    k: _months_from_template(v) for k, v in _TEMPLATES.items()
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def climate_for_home(team_name: str, month: int) -> dict[str, float | str] | None:
    """Soft home-city climate for a fixture month, or None if unknown/empty/bad month."""
    key = _normalize(team_name)
    if not key:
        return None
    try:
        m = int(month)
    except (TypeError, ValueError):
        return None
    if m < 1 or m > 12:
        return None
    tpl_name = _CLUB_TEMPLATE.get(key)
    if tpl_name is None:
        return None
    months = _MONTHLY.get(tpl_name)
    if not months:
        return None
    temp, cond = months[m - 1]
    try:
        t = float(temp)
    except (TypeError, ValueError):
        return None
    if t < -15.0:
        t = -15.0
    elif t > 45.0:
        t = 45.0
    c = str(cond).strip().lower()
    if c not in _CONDITIONS:
        c = "mild"
    return {"temp_c": round(t, 1), "condition": c}
```

Ensure unique clubs mapped ≥20 (the map above exceeds this via aliases + clubs).

- [ ] **Step 2: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_weather.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7w"
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/football_weather.py backend/tests/test_football_weather.py
git commit -m "feat(football): static home climate by month (P1-F7 weather)"
```

---

### Task 3: Adapter `enrich_weather_features` + tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py`
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `climate_for_home(team_name: str, month: int) -> dict | None`
- Produces: environment weather fields + optional custom mirrors + `weather_source`

- [ ] **Step 1: Append adapter tests**

Append to `backend/tests/test_adapter_shared.py` (pytest and MatchIdentity helpers already present):

```python
class TestStaticWeatherFill:
    def test_static_fill_when_missing(self):
        match = MatchIdentity(
            match_id="ucl-wx-fill",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
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
        from app.sports.football.adapters._shared import enrich_weather_features
        from app.sports.football.football_weather import climate_for_home

        enrich_weather_features(raw, match)
        expected = climate_for_home("Arsenal", 9)
        assert expected is not None
        assert raw["environment"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))
        assert raw["environment"]["weather_condition"] == expected["condition"]
        assert raw["custom"]["weather_source"] == "static_climate"
        assert raw["custom"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))
        assert raw["custom"]["weather_condition"] == expected["condition"]

    def test_does_not_overwrite_existing_temp(self):
        match = MatchIdentity(
            match_id="ucl-wx-keep",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {"weather_temp_c": 21.5, "weather_condition": "clear"},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_weather_features

        enrich_weather_features(raw, match)
        assert raw["environment"]["weather_temp_c"] == pytest.approx(21.5)
        assert raw["environment"]["weather_condition"] == "clear"
        assert raw["custom"].get("weather_source") != "static_climate"

    def test_unknown_home_no_static_weather(self):
        match = MatchIdentity(
            match_id="ucl-wx-none",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="NoSuchHome FC", competition=_UCL),
            away=TeamIdentity(code="YYY", name="NoSuchAway FC", competition=_UCL),
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
        from app.sports.football.adapters._shared import enrich_weather_features

        enrich_weather_features(raw, match)
        assert raw["environment"].get("weather_temp_c") is None
        assert raw["environment"].get("weather_condition") is None
        assert "weather_source" not in raw["custom"]
```

- [ ] **Step 2: Run adapter weather tests — expect FAIL**

```powershell
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticWeatherFill -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7w"
```

Expected: FAIL (`enrich_weather_features` missing).

- [ ] **Step 3: Implement enrich + wire**

Add near other enrich helpers in `backend/app/sports/football/adapters/_shared.py`:

```python
def enrich_weather_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through weather, then static climate fill when still missing (P1-F7)."""
    try:
        env = raw.setdefault("environment", {})
        custom = raw.setdefault("custom", {})
        temp = (
            env.get("weather_temp_c")
            or custom.get("weather_temp_c")
            or env.get("temp_c")
            or custom.get("temp_c")
        )
        cond = (
            env.get("weather_condition")
            or custom.get("weather_condition")
            or env.get("condition")
        )
        if temp is not None or cond is not None:
            if temp is not None:
                env["weather_temp_c"] = float(temp)
                custom.setdefault("weather_temp_c", float(temp))
            if cond is not None:
                env["weather_condition"] = str(cond).strip()
                custom.setdefault("weather_condition", str(cond).strip())
            return

        kickoff = getattr(match, "kickoff_utc", None)
        if kickoff is None:
            return
        month = int(kickoff.month)
        home_name = match.home.name if match.home else ""
        from app.sports.football.football_weather import climate_for_home

        climate = climate_for_home(home_name, month)
        if climate is None:
            return
        env["weather_temp_c"] = float(climate["temp_c"])
        env["weather_condition"] = str(climate["condition"])
        custom["weather_temp_c"] = float(climate["temp_c"])
        custom["weather_condition"] = str(climate["condition"])
        custom["weather_source"] = "static_climate"
    except Exception:  # noqa: BLE001
        logger.debug("weather enrich skipped", exc_info=True)
```

In `fetch_raw_match_data`, immediately after `enrich_altitude_features(raw, match)`:

```python
    enrich_altitude_features(raw, match)
    enrich_weather_features(raw, match)
```

- [ ] **Step 4: Run tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_football_weather.py tests/test_adapter_shared.py::TestStaticWeatherFill -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7w"
```

Expected: all PASS.

Optional smoke (should still pass):

```powershell
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticAltitudeFill tests/test_adapter_shared.py::TestEnrichRefereeFeatures -v --tb=line --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7w"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): static climate weather fill-only enrich (P1-F7)"
```

---

### Task 4: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

- [ ] **Step 1: CHANGELOG Unreleased** (near other P1-F7 notes):

```markdown
### Football static climate weather fill (P1-F7 residual)

- `football_weather.climate_for_home`: code-local home-city×month soft temp/condition (big-five + UCL-common)
- Adapter `enrich_weather_features`: pass-through first; static fill writes environment + custom mirrors with `weather_source=static_climate`
- MultiFactor unchanged (no weather factor this round); true forecast API still pending
```

- [ ] **Step 2: Backlog P1-F7 row**

Replace the P1-F7 line with:

```markdown
| P1-F7 | 场地 / 旅行 / 海拔 / 天气 | ✅ 部分 2026-07-26：俱乐部城市+海拔 fill-only + 静态气候 `climate_for_home`（`weather_source=static_climate`）；真预报 API 仍待 |
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F7 static climate weather changelog + backlog"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Pure `football_weather.py` + `climate_for_home` | 1–2 |
| ≥20 clubs, 12 months, closed condition vocab | 2 |
| Normalize / empty / bad month → None | 1–2 |
| temp clamp [-15,45], condition vocabulary | 2 |
| Adapter pass-through first | 3 |
| Static fill only when both missing | 3 |
| environment + custom + `static_climate` | 3 |
| Wire after altitude | 3 |
| MultiFactor unchanged | constraints |
| CHANGELOG + backlog | 4 |

## Placeholder / consistency scan

- No TBD placeholders.
- API names: `climate_for_home`, `enrich_weather_features`, `weather_source=static_climate`.
- Test clubs: Arsenal, Manchester United, Sevilla, Celtic, Real Madrid CF.
- Kickoff month: September 2025 fixtures → month 9.
