# Football Static Climate Weather Fill (P1-F7 residual) — Design

**Date:** 2026-07-26
**Status:** Approved for planning
**Backlog:** P1-F7 (partial → weather data side after club geo + altitude; true forecast API still pending)

## Problem

P1-F7 already delivered:

- Club city geo → travel soft via `team_geo`
- Sparse static altitude fill-only → MultiFactor altitude when ≥1500 m

Weather remains a gap:

1. `FootballFeatureBuilder` already maps `environment.weather_temp_c` / `weather_condition` into the kernel environment layer.
2. Football adapter `fetch_raw_match_data` does **not** systematically pass through or fill weather fields for club fixtures.
3. `FootballMultiFactorEngine` has **no** weather factor (unlike MLB soft weather). This round does **not** add engine fusion — only make environment weather **non-empty when possible** for FeatureBuilder, FE display, future soft factors, and parity with altitude/travel enrich style.
4. Legacy World Cup path has Open-Meteo (`world_cup_weather_service`); club kernel path should not depend on network for a soft climate prior.

Backlog line still says weather 真源仍待. This design covers the **offline climate soft fill** slice only.

## Goals

1. Add pure module `backend/app/sports/football/football_weather.py` with code-local **city×month** climate priors keyed by normalized **home team** name (same alias spirit as `football_xg` / `football_style`).
2. Expose `climate_for_home(team_name: str, month: int) -> dict[str, float | str] | None` returning at least:
   - `temp_c`: float soft typical temperature (°C)
   - `condition`: coarse soft label (`clear` | `mild` | `rain` | `cold` | `hot` — allow small fixed vocabulary)
3. Add `enrich_weather_features(raw, match)` in football `adapters/_shared.py`:
   - **Pass-through first:** if any of environment/custom already has usable `weather_temp_c` or `weather_condition`, normalize into `environment` (and optional custom mirror) and **do not** overwrite with static climate.
   - **Fill-only:** when both temp and condition are still missing, call `climate_for_home(home_name, kickoff_month_utc)`; on hit write environment fields + `custom.weather_source = "static_climate"` (and optional custom mirrors of temp/condition for consistency with other soft sources).
4. Wire into `fetch_raw_match_data` near altitude enrich (after altitude is fine).
5. Zero runtime network; no new config keys; no DB schema.
6. Leave MultiFactor weights/formulas **unchanged** (no new weather factor this round).

## Non-goals

- Open-Meteo / any live forecast API for club fixtures this round
- Adding MultiFactor `weather` weight or formula (defer until data path is proven)
- Wind / humidity / precipitation mm (keep temp + coarse condition only)
- Away-team climate or travel-weather interaction models
- Roof / indoor stadium logic for football (most top venues outdoor; YAGNI)
- Changing FeatureBuilder field names
- Full lower-league or every national team climate coverage

## Approved approach

**Option A — dedicated `football_weather.py` + adapter enrich (user-selected)**

1. **Static table** — normalized home club name → 12 monthly soft rows, or compact seasonal templates expanded to 12 months at module load / in data definition. Prefer explicit 12-month tuples for clarity in tests.
2. **`_normalize`** — lower + whitespace collapse (same as xG/style).
3. **`climate_for_home(name, month)`** — month must be 1–12; else `None`. Empty/unknown name → `None`. Clamp `temp_c` to a defensive band e.g. **[-15, 45]**; `condition` must be in the fixed vocabulary or coerce to `mild`.
4. **Kickoff month** — from `match.kickoff_utc` (timezone-aware or treat as UTC). If kickoff missing, skip static fill (pass-through only).
5. **Enrich contract:**

| Case | After enrich |
|------|----------------|
| Fixture already has temp and/or condition | Normalized environment (and optional custom); **no** `weather_source=static_climate` forced |
| Missing both; static hit | `environment.weather_temp_c`, `environment.weather_condition`; optional `custom.weather_temp_c` / `custom.weather_condition`; `custom.weather_source="static_climate"` |
| Missing both; static miss | leave empty |
| No home name / no kickoff | no invent |

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Climate inside `team_geo` | Couples altitude/geo with climate; user chose independent module |
| C. Open-Meteo first | Network, cache, rate limits; conflicts with static soft cycle |
| League-only climate bands | Weaker hit quality for north/south clubs in same league (user chose team×month) |
| Annual mean temp only | User chose month-aware climate |
| Add MultiFactor weather this round | Scope creep; data path first |

## Data model

### Lookup

```python
def climate_for_home(team_name: str, month: int) -> dict[str, float | str] | None:
    """Soft home-city climate for a fixture month, or None if unknown/empty/bad month.

    Returns:
        {
          "temp_c": float,      # typical monthly temperature
          "condition": str,    # clear | mild | rain | cold | hot
        }
    """
```

### Table entry

- Key: normalized English club name as on fixtures (aliases: Man City, Real Madrid CF, …).
- Value: 12 soft months of `(temp_c, condition)` or equivalent structure.
- Soft multi-year climate priors — **not** live forecasts. Module comment: operators update by PR.
- Directional sanity: northern winter months colder than summer for same club; Mediterranean clubs warmer winters than Scottish/northern English clubs when both covered.

### Coverage guidelines

- Primary: big-five + frequent UCL clubs (~same hit-rate target as `football_xg` / `football_style`).
- National teams optional only if cheap aliases already exist; not required for acceptance.
- Target **≥20** unique clubs with full 12-month rows (aliases may share climate rows via key duplication or shared city template).

### Condition vocabulary (closed)

```text
clear | mild | rain | cold | hot
```

- `cold`: soft label for typically cold months (not a hard °C gate in lookup; table author chooses).
- `hot`: soft label for hot months.
- `rain`: soft wet-season / maritime winter prior (not match-day rain probability).
- `clear` / `mild`: default fair-weather priors.

## Architecture / data flow

```text
fixture / env / custom weather_* (if present)
    → pass-through normalize into environment (+ optional custom)
    → return (no static)

else:
climate_for_home(home_name, kickoff.month UTC)
    → hit: environment.weather_temp_c / weather_condition
           custom mirrors optional
           custom.weather_source = "static_climate"
    → miss: leave empty

FootballFeatureBuilder
    → environment.weather_temp_c / weather_condition (unchanged mapping)

FootballMultiFactorEngine
    → unchanged (no weather factor this round)
```

## Adapter placement

In `fetch_raw_match_data`, call after `enrich_altitude_features` (or immediately nearby):

```python
enrich_weather_features(raw, match)
```

Best-effort: wrap body in try/except; on failure log debug and leave fields.

## Testing

1. **Unit (`test_football_weather.py`):**
   - Known club + valid month → keys present; temp in band; condition in vocabulary
   - Unknown club → `None`
   - Empty name → `None`
   - Month 0 / 13 → `None`
   - Winter colder than summer for a northern club (directional)
   - Normalize case/spaces
2. **Adapter (`test_adapter_shared.py`):**
   - Static fill when missing → environment fields + `weather_source=static_climate`
   - Existing temp/condition not overwritten; source not forced to static
   - Unknown home → no invented weather
3. MultiFactor suite: no new requirements (smoke optional only).

## Documentation / backlog

- `CHANGELOG.md` Unreleased: static climate weather fill (P1-F7 residual)
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F7 row: note static climate fill; true forecast API still pending

## Risks / mitigations

| Risk | Mitigation |
|------|------------|
| Soft climate misread as match-day forecast | Source tag `static_climate`; module/changelog wording |
| Month timezone edge near midnight UTC | Document kickoff_utc month; acceptable soft error |
| Table maintenance cost | Share climate templates across city aliases; PR updates |
| Scope creep into engine weather | Explicit non-goal |

## Acceptance criteria

1. `climate_for_home` pure module exists with ≥20 clubs × 12 months soft climate.
2. Adapter `enrich_weather_features` pass-through first; static fill-only when temp and condition both missing.
3. Static hit writes environment weather fields + `custom.weather_source="static_climate"`.
4. Unknown/empty/missing kickoff do not invent weather.
5. MultiFactor formula/weights unchanged.
6. Unit + adapter tests green; CHANGELOG + backlog P1-F7 updated.
