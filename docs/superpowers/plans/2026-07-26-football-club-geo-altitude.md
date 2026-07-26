# Football Club Geo Travel + Venue Altitude (P1-F7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make club football fixtures resolve soft travel via static city geo, and fill missing home venue altitude from a sparse static table, without live APIs or MultiFactor formula changes.

**Architecture:** Extend `team_geo.py` with `_FOOTBALL_CLUBS` and sparse `_FOOTBALL_ALTITUDE_M`. Football `resolve_city` tries clubs then nationals. New `altitude_m_for_team`. Adapter keeps pass-through altitude first; **fill-only** from static when still empty; existing `travel_between_teams` call picks up clubs automatically.

**Tech Stack:** Python 3.12+, pytest. No new dependencies. No network. No DB.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-football-club-geo-altitude-design.md`
- Club geo first, national fallback for football sport codes
- Dual-side geo for travel (existing `travel_between_teams` rule)
- Altitude: **fill only when missing** after pass-through; never overwrite; set `altitude_source=static_table` only on static fill
- MultiFactor travel/altitude formulas, ≥1500 m gate, weights **unchanged**
- No weather this round
- No network / new env / DB
- Do **not** push unless user asks
- TDD: RED → GREEN → COMMIT
- Python: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`
- basetemp: `--basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7"`
- Preserve NBA/NHL/MLB `resolve_city` behavior

## File Structure

### Modified files
1. `backend/app/sports/_shared/team_geo.py` — club table, resolve_city, altitude table + `altitude_m_for_team`
2. `backend/tests/test_team_geo.py` — club travel + altitude unit tests
3. `backend/app/sports/football/adapters/_shared.py` — static altitude fill after pass-through
4. `backend/tests/test_adapter_shared.py` — altitude fill / no-overwrite tests
5. `CHANGELOG.md` — Unreleased P1-F7
6. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F7 status line

### Unchanged (verify only)
1. `football_multi_factor_engine.py` — travel/altitude consumers
2. NBA/NHL/MLB tables and tests

---

### Task 1: Club geo + altitude unit tests (RED)

**Files:**
- Modify: `backend/tests/test_team_geo.py`
- (Production tables may not yet resolve clubs / altitude API may be missing)

**Interfaces:**
- Consumes (target): `resolve_city`, `travel_between_teams`, `altitude_m_for_team` from `app.sports._shared.team_geo`
- Produces: failing tests defining Task 2 contract

- [ ] **Step 1: Append failing tests**

Append to `backend/tests/test_team_geo.py` (update imports at top):

```python
"""Tests for team_geo travel / timezone soft signals."""
from app.sports._shared.team_geo import (
    altitude_m_for_team,
    haversine_km,
    resolve_city,
    travel_between_teams,
    travel_prob_home,
)


def test_haversine_same_point_zero():
    assert haversine_km(40.0, -74.0, 40.0, -74.0) < 0.01


def test_nba_cross_country_distance():
    t = travel_between_teams("Boston Celtics", "Los Angeles Lakers", "nba")
    assert t["travel_known"] is True
    assert t["travel_km_away"] is not None
    assert t["travel_km_away"] > 3000
    assert abs(t["timezone_offset_hours_away"]) >= 2


def test_nhl_canadian_cross_zone():
    t = travel_between_teams("Toronto Maple Leafs", "Vancouver Canucks", "nhl")
    assert t["travel_known"] is True
    assert t["travel_km_away"] > 2000
    assert abs(t["timezone_offset_hours_away"]) >= 2


def test_travel_prob_long_haul_favors_home():
    p_short, ok1 = travel_prob_home(100.0, 0)
    p_long, ok2 = travel_prob_home(4000.0, 3)
    assert ok1 and ok2
    assert p_long > p_short
    assert 0.4 <= p_long <= 0.6


def test_unknown_team_unavailable():
    t = travel_between_teams("Unknown FC", "Also Unknown", "nba")
    assert t["travel_known"] is False
    p, ok = travel_prob_home(None, None)
    assert ok is False
    assert p == 0.5


# --- P1-F7 football club geo + altitude ---


def test_football_club_resolves_city():
    city = resolve_city("Arsenal", "epl")
    assert city is not None
    lat, lon, tz = city
    assert 51.0 < lat < 52.0
    assert -1.0 < lon < 1.0
    assert tz == 0


def test_football_club_alias_man_city():
    a = resolve_city("Manchester City", "football")
    b = resolve_city("Man City", "ucl")
    assert a is not None and b is not None
    assert a[0] == b[0] and a[1] == b[1]


def test_football_fixture_real_madrid_cf():
    city = resolve_city("Real Madrid CF", "ucl")
    assert city is not None


def test_football_fixture_bayern():
    city = resolve_city("FC Bayern München", "ucl")
    assert city is not None


def test_football_national_still_resolves():
    city = resolve_city("Brazil", "wc")
    assert city is not None
    assert city[0] < 0  # southern hemisphere capital-ish


def test_club_travel_london_to_madrid():
    t = travel_between_teams("Arsenal", "Real Madrid CF", "ucl")
    assert t["travel_known"] is True
    assert t["travel_km_away"] is not None
    assert t["travel_km_away"] > 1000
    assert t["travel_km_home"] == 0.0


def test_unknown_football_club_travel_unknown():
    t = travel_between_teams("NoSuchHome FC", "NoSuchAway FC", "epl")
    assert t["travel_known"] is False


def test_altitude_high_venue_in_band():
    # Toluca / Mexico City area — must be in static altitude table ≥1500
    alt = altitude_m_for_team("Toluca")
    assert alt is not None
    assert 1500.0 <= float(alt) <= 4500.0


def test_altitude_unknown_none():
    assert altitude_m_for_team("NotAFootballClubXYZ") is None


def test_altitude_empty_none():
    assert altitude_m_for_team("") is None
    assert altitude_m_for_team("   ") is None
```

- [ ] **Step 2: Run tests — expect RED**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_team_geo.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7"
```

Expected: FAIL on club resolve / `altitude_m_for_team` import or assertions (clubs not in table yet).

- [ ] **Step 3: Commit**

```powershell
git add backend/tests/test_team_geo.py
git commit -m "test(geo): failing P1-F7 football club geo + altitude tests"
```

---

### Task 2: Implement club geo + altitude_m_for_team (GREEN)

**Files:**
- Modify: `backend/app/sports/_shared/team_geo.py`
- Test: `backend/tests/test_team_geo.py`

**Interfaces:**
- Produces:

```python
def resolve_city(team_name: str, sport: str) -> tuple[float, float, int] | None:
    # football*: club first, then national

def altitude_m_for_team(team_name: str) -> float | None:
    ...
```

- [ ] **Step 1: Add `_FOOTBALL_CLUBS` after `_FOOTBALL_NATIONAL` (before `_MLB_CITIES`)**

Minimum keys (may add more). Values: (lat, lon, utc_offset). Soft city-level:

```python
# Club home cities for football travel soft signal (P1-F7).
# Keys match common fixture English names; _lookup also fuzzy-matches.
_FOOTBALL_CLUBS: dict[str, tuple[float, float, int]] = {
    # EPL / London & England
    "Arsenal": (51.555, -0.108, 0),
    "Aston Villa": (52.509, -1.885, 0),
    "Bournemouth": (50.735, -1.838, 0),
    "Brentford": (51.491, -0.289, 0),
    "Brighton": (50.862, -0.083, 0),
    "Brighton and Hove Albion": (50.862, -0.083, 0),
    "Chelsea": (51.482, -0.191, 0),
    "Crystal Palace": (51.398, -0.086, 0),
    "Everton": (53.439, -2.966, 0),
    "Fulham": (51.475, -0.222, 0),
    "Ipswich": (52.055, 1.145, 0),
    "Ipswich Town": (52.055, 1.145, 0),
    "Leicester": (52.620, -1.142, 0),
    "Leicester City": (52.620, -1.142, 0),
    "Liverpool": (53.431, -2.961, 0),
    "Manchester City": (53.483, -2.200, 0),
    "Man City": (53.483, -2.200, 0),
    "Manchester United": (53.463, -2.291, 0),
    "Man United": (53.463, -2.291, 0),
    "Man Utd": (53.463, -2.291, 0),
    "Newcastle": (54.975, -1.622, 0),
    "Newcastle United": (54.975, -1.622, 0),
    "Nottingham Forest": (52.940, -1.133, 0),
    "Southampton": (50.906, -1.391, 0),
    "Tottenham": (51.604, -0.066, 0),
    "Tottenham Hotspur": (51.604, -0.066, 0),
    "Spurs": (51.604, -0.066, 0),
    "West Ham": (51.539, -0.017, 0),
    "West Ham United": (51.539, -0.017, 0),
    "Wolves": (52.590, -2.130, 0),
    "Wolverhampton": (52.590, -2.130, 0),
    "Wolverhampton Wanderers": (52.590, -2.130, 0),
    # La Liga
    "Real Madrid": (40.453, -3.688, 1),
    "Real Madrid CF": (40.453, -3.688, 1),
    "Barcelona": (41.381, 2.123, 1),
    "FC Barcelona": (41.381, 2.123, 1),
    "Atletico Madrid": (40.436, -3.599, 1),
    "Atlético Madrid": (40.436, -3.599, 1),
    "Atletico de Madrid": (40.436, -3.599, 1),
    "Sevilla": (37.384, -5.971, 1),
    "Real Sociedad": (43.301, -1.974, 1),
    "Villarreal": (39.944, -0.104, 1),
    "Athletic Bilbao": (43.264, -2.949, 1),
    "Athletic Club": (43.264, -2.949, 1),
    "Real Betis": (37.356, -5.982, 1),
    "Girona": (41.961, 2.829, 1),
    # Serie A
    "Inter": (45.478, 9.124, 1),
    "Inter Milan": (45.478, 9.124, 1),
    "Internazionale": (45.478, 9.124, 1),
    "AC Milan": (45.478, 9.124, 1),
    "Milan": (45.478, 9.124, 1),
    "Juventus": (45.110, 7.641, 1),
    "Napoli": (40.828, 14.193, 1),
    "Roma": (41.934, 12.455, 1),
    "AS Roma": (41.934, 12.455, 1),
    "Lazio": (41.934, 12.455, 1),
    "Atalanta": (45.709, 9.681, 1),
    "Fiorentina": (43.781, 11.282, 1),
    # Bundesliga
    "Bayern Munich": (48.219, 11.625, 1),
    "FC Bayern Munich": (48.219, 11.625, 1),
    "Bayern München": (48.219, 11.625, 1),
    "FC Bayern München": (48.219, 11.625, 1),
    "Borussia Dortmund": (51.493, 7.452, 1),
    "Dortmund": (51.493, 7.452, 1),
    "BVB": (51.493, 7.452, 1),
    "RB Leipzig": (51.346, 12.348, 1),
    "Leipzig": (51.346, 12.348, 1),
    "Bayer Leverkusen": (51.038, 7.002, 1),
    "Leverkusen": (51.038, 7.002, 1),
    "Eintracht Frankfurt": (50.069, 8.645, 1),
    "Wolfsburg": (52.433, 10.804, 1),
    "Borussia Monchengladbach": (51.175, 6.385, 1),
    "Monchengladbach": (51.175, 6.385, 1),
    # Ligue 1
    "PSG": (48.841, 2.253, 1),
    "Paris Saint-Germain": (48.841, 2.253, 1),
    "Paris Saint Germain": (48.841, 2.253, 1),
    "Marseille": (43.270, 5.396, 1),
    "Olympique Marseille": (43.270, 5.396, 1),
    "Lyon": (45.765, 4.982, 1),
    "Olympique Lyonnais": (45.765, 4.982, 1),
    "Monaco": (43.728, 7.415, 1),
    "AS Monaco": (43.728, 7.415, 1),
    "Lille": (50.612, 3.130, 1),
    "Lens": (50.433, 2.815, 1),
    "Nice": (43.705, 7.193, 1),
    # Europe / UCL regulars
    "Ajax": (52.314, 4.942, 1),
    "Porto": (41.162, -8.584, 0),
    "FC Porto": (41.162, -8.584, 0),
    "Benfica": (38.753, -9.184, 0),
    "Sporting": (38.761, -9.161, 0),
    "Sporting CP": (38.761, -9.161, 0),
    "Sporting Lisbon": (38.761, -9.161, 0),
    "Celtic": (55.850, -4.206, 0),
    "Rangers": (55.853, -4.309, 0),
    "Galatasaray": (41.103, 28.991, 3),
    "Fenerbahce": (40.988, 29.037, 3),
    "Shakhtar Donetsk": (50.433, 30.522, 2),
    "Red Star Belgrade": (44.783, 20.465, 1),
    "Club Brugge": (51.193, 3.180, 1),
    "PSV": (51.442, 5.467, 1),
    "PSV Eindhoven": (51.442, 5.467, 1),
    "Feyenoord": (51.894, 4.523, 1),
    "Salzburg": (47.816, 13.049, 1),
    "RB Salzburg": (47.816, 13.049, 1),
    "Dynamo Kyiv": (50.433, 30.522, 2),
    "Slavia Prague": (50.068, 14.471, 1),
    # High-altitude / altitude-table partners (geo for travel if needed)
    "Toluca": (19.287, -99.667, -6),
    "Club America": (19.303, -99.151, -6),
    "Club América": (19.303, -99.151, -6),
    "Pumas UNAM": (19.333, -99.192, -6),
    "Bolivar": (-16.499, -68.123, -4),
    "Bolívar": (-16.499, -68.123, -4),
    "The Strongest": (-16.499, -68.123, -4),
    "LDU Quito": (-0.178, -78.476, -5),
    "Independiente del Valle": (-0.238, -78.527, -5),
}
```

- [ ] **Step 2: Add sparse altitude table + `altitude_m_for_team`**

After `_FOOTBALL_CLUBS` (or near end of module before helpers is fine; prefer after club table):

```python
# Sparse home-venue altitudes (m). Only useful / high venues required for ≥1500m gate.
# Keys normalized via _normalize for lookup. Operators update by PR.
_FOOTBALL_ALTITUDE_M: dict[str, float] = {
    "toluca": 2667.0,
    "club america": 2240.0,
    "club américa": 2240.0,
    "pumas unam": 2240.0,
    "mexico": 2240.0,  # national home (Azteca area)
    "bolivar": 3600.0,
    "bolívar": 3600.0,
    "the strongest": 3600.0,
    "ldu quito": 2850.0,
    "independiente del valle": 2500.0,
    "ecuador": 2850.0,
    "bolivia": 3600.0,
    "colombia": 2640.0,  # Bogotá
    "bogota": 2640.0,
    "addis ababa": 2355.0,
    "ethiopia": 2355.0,
}


def altitude_m_for_team(team_name: str) -> float | None:
    """Home-venue altitude in meters, or None if unknown/empty."""
    key = _normalize(team_name)
    if not key:
        return None
    # exact normalized key
    if key in _FOOTBALL_ALTITUDE_M:
        val = _FOOTBALL_ALTITUDE_M[key]
    else:
        # reuse fuzzy spirit: scan table keys
        val = None
        for k, v in _FOOTBALL_ALTITUDE_M.items():
            if key == k or key in k or k in key:
                val = v
                break
        if val is None:
            return None
    try:
        alt = float(val)
    except (TypeError, ValueError):
        return None
    if alt < 0.0:
        alt = 0.0
    elif alt > 4500.0:
        alt = 4500.0
    return round(alt, 1)
```

- [ ] **Step 3: Update `resolve_city` football branch**

Replace the football branch so clubs are tried first; expand league codes:

```python
    if code in (
        "football",
        "soccer",
        "wc",
        "world_cup",
        "epl",
        "laliga",
        "ucl",
        "bundesliga",
        "seriea",
        "serie_a",
        "ligue1",
        "ligue_1",
    ):
        return _lookup(team_name, _FOOTBALL_CLUBS) or _lookup(
            team_name, _FOOTBALL_NATIONAL
        )
```

Also update the final fallback chain to include clubs:

```python
    return (
        _lookup(team_name, _NBA_CITIES)
        or _lookup(team_name, _NHL_CITIES)
        or _lookup(team_name, _MLB_CITIES)
        or _lookup(team_name, _FOOTBALL_CLUBS)
        or _lookup(team_name, _FOOTBALL_NATIONAL)
    )
```

Update module docstring first lines to mention football clubs + altitude (P1-F7).

Do **not** change `travel_prob_home` math or MultiFactor.

- [ ] **Step 4: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_team_geo.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7"
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/_shared/team_geo.py backend/tests/test_team_geo.py
git commit -m "feat(geo): football club cities + static altitude (P1-F7)"
```

---

### Task 3: Adapter altitude fill-only + tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (altitude block ~154–167)
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `altitude_m_for_team(team_name: str) -> float | None`
- Produces: when altitude still missing after pass-through and static hits home team → `custom.venue_altitude_m`, `custom.altitude_source="static_table"`

- [ ] **Step 1: Add adapter tests**

Append to `backend/tests/test_adapter_shared.py`:

```python
class TestStaticAltitudeFill:
    def test_static_fill_when_missing(self):
        # Home side must be in altitude table (Toluca)
        match = MatchIdentity(
            match_id="ucl-alt-fill",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="TOL", name="Toluca", competition=_UCL),
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
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        from app.sports._shared.team_geo import altitude_m_for_team

        expected = altitude_m_for_team("Toluca")
        assert expected is not None
        assert raw["custom"]["venue_altitude_m"] == pytest.approx(float(expected))
        assert raw["custom"]["altitude_source"] == "static_table"

    def test_does_not_overwrite_existing(self):
        match = MatchIdentity(
            match_id="ucl-alt-keep",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="TOL", name="Toluca", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {"venue_altitude_m": 1234.0},
        }
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        assert raw["custom"]["venue_altitude_m"] == pytest.approx(1234.0)
        assert raw["custom"].get("altitude_source") != "static_table"

    def test_unknown_home_no_static_altitude(self):
        match = MatchIdentity(
            match_id="ucl-alt-none",
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
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        assert "venue_altitude_m" not in raw["custom"]
        assert "altitude_source" not in raw["custom"]
```

Prefer exporting `enrich_altitude_features` (extract current pass-through + static fill) so tests do not need full `fetch_raw_match_data`.

- [ ] **Step 2: Run adapter altitude tests — expect FAIL**

```powershell
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticAltitudeFill -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7"
```

Expected: FAIL (`enrich_altitude_features` missing).

- [ ] **Step 3: Implement `enrich_altitude_features` and wire it**

In `_shared.py`, replace the inline altitude try-block with a helper and call it from `fetch_raw_match_data` at the same position:

```python
def enrich_altitude_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through altitude, then static fill for home venue when still missing (P1-F7)."""
    try:
        env = raw.setdefault("environment", {})
        custom = raw.setdefault("custom", {})
        alt = (
            custom.get("venue_altitude_m")
            or custom.get("altitude_m")
            or env.get("altitude_m")
            or env.get("venue_altitude_m")
        )
        if alt is not None:
            custom["venue_altitude_m"] = float(alt)
            return
        from app.sports._shared.team_geo import altitude_m_for_team

        home_name = match.home.name if match.home else ""
        static_alt = altitude_m_for_team(home_name)
        if static_alt is not None:
            custom["venue_altitude_m"] = float(static_alt)
            custom["altitude_source"] = "static_table"
    except Exception:  # noqa: BLE001
        logger.debug("altitude enrich skipped", exc_info=True)
```

In `fetch_raw_match_data`, replace the old altitude try-block with:

```python
    enrich_altitude_features(raw, match)
```

Update the travel comment from "clubs stay empty" to note clubs resolve via team_geo (optional comment cleanup only).

- [ ] **Step 4: Run tests**

```powershell
C:\Python314\python.exe -m pytest tests/test_team_geo.py tests/test_adapter_shared.py::TestStaticAltitudeFill -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7"
```

Expected: all PASS.

Optional smoke:

```powershell
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticStyleOverwrite tests/test_adapter_shared.py::TestStaticXgOverwrite -v --tb=line --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f7"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): static altitude fill-only enrich (P1-F7)"
```

---

### Task 4: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

- [ ] **Step 1: CHANGELOG Unreleased** (above P1-F6 section):

```markdown
### Football club geo travel + venue altitude (P1-F7)

- `team_geo`: `_FOOTBALL_CLUBS` city coordinates; football `resolve_city` prefers clubs then nationals so club fixtures get `travel_known`
- Sparse `altitude_m_for_team`; adapter fills `venue_altitude_m` only when missing (`altitude_source=static_table`)
- MultiFactor travel/altitude formulas unchanged; weather still pending
```

- [ ] **Step 2: Backlog P1-F7 row**

```markdown
| P1-F7 | 场地 / 旅行 / 海拔 / 天气 | ✅ 部分 2026-07-26：俱乐部 `team_geo` 旅行 + 静态海拔 fill-only（≥1500m 引擎 soft）；天气真源仍待 |
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F7 club geo + altitude changelog + backlog"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `_FOOTBALL_CLUBS` + resolve club→national | 1–2 |
| National still works | 1–2 |
| `altitude_m_for_team` sparse | 1–2 |
| Adapter fill-only altitude + source | 3 |
| No overwrite existing altitude | 3 |
| Travel via existing `travel_between_teams` | 2 (automatic) |
| MultiFactor unchanged | constraints + smoke |
| No weather | all |
| CHANGELOG + backlog | 4 |

## Placeholder / consistency scan

- No TBD placeholders.
- API names: `altitude_m_for_team`, `enrich_altitude_features`, `altitude_source`.
- Fixture names: Real Madrid CF, FC Bayern München, Toluca for ≥1500 altitude test.
- League codes include epl/ucl/bundesliga/seriea/ligue1.
