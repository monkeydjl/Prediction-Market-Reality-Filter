# NBA Static Net Rating (P1-B4) — Design

**Date:** 2026-07-24  
**Status:** Approved for planning  
**Backlog:** P1-B4 (partial → team-specific static ORtg/DRtg)

## Problem

`BasketballEngine` already fuses a soft `net_rating` factor (weight ~0.13):

```
net_home = ortg_home - drtg_home
net_away = ortg_away - drtg_away
net_diff = net_home - net_away
p_net = clamp(0.5 + clamp(net_diff, -15, 15) * 0.012, 0.30, 0.70)
```

All four of `custom.ortg_home`, `drtg_home`, `ortg_away`, `drtg_away` must be non-null or the factor is unavailable and its weight redistributes.

Today `NBAAdapter.fetch_all_data` hard-codes the **same** ORtg/DRtg (and pace/tpct) for every match:

| Field | Stub value |
|-------|------------|
| `ortg_home` | 112.3 |
| `ortg_away` | 108.1 |
| `drtg_home` | 105.0 |
| `drtg_away` | 110.5 |
| `pace_*` / `tpct_*` | also fixed stubs |

That makes net_rating always “available” but **match-invariant**: every game gets the same efficiency edge. P1-B4 backlog text already notes engine soft path exists; the gap is real per-team ratings.

## Goals

1. Replace global hard-coded `ortg_*/drtg_*` with a **30-franchise static table** keyed by balldontlie-style full names.
2. New pure module `nba_team_ratings.py` owns the table and lookup API (same pattern as `nba_injury.py`).
3. Adapter injects all four `custom` fields **only when both teams resolve**.
4. Leave `BasketballEngine` formula, clamps, and registry weight **unchanged**.
5. Zero runtime network dependency; no new config keys.

## Non-goals

- Live balldontlie / NBA.com advanced stats pulls
- Possession-based ORtg derived from local box scores
- Realizing `pace_*` / `tpct_*` as team-specific signals this round
- Changing `* 0.012` sensitivity, ±15 clamp, or weight `0.13`
- Mid-season dynamic re-rankings

## Approved approach

**Option A — dedicated module + dual-side inject**

1. Create `backend/app/sports/basketball/nba_team_ratings.py`:
   - `_TEAM_RATINGS: dict[str, dict[str, float]]` — full name → `{ortg, drtg}`
   - `ratings_for_team(team_name: str) -> dict[str, float] | None`
2. In `NBAAdapter.fetch_all_data`:
   - Look up home and away.
   - If **both** non-null, set `ortg_home`, `drtg_home`, `ortg_away`, `drtg_away` on `custom`.
   - If either is missing, **omit all four** (do not leave one-sided partials; do not write league-average fakes).
3. Remove the hard-coded `ortg_*` / `drtg_*` stubs from the default `custom` dict.
4. Remove hard-coded `pace_*` / `tpct_*` from default `custom` as well (engine does not read them; stubs were false signal). Tests that asserted those stubs must stop depending on them or set them explicitly in fixtures.

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Write one side when only one resolves | Engine still requires all four → net unavailable; half-filled custom is misleading |
| C. League-average fallback for missing teams | Turns “unknown” into neutral-available and hides data gaps |
| Runtime API first | Rate limits, free-tier gaps; static soft matches park/injury pattern this cycle |

## Data model

### Team rating row

```python
{
    "ortg": float,  # points per 100 possessions (soft multi-year-ish)
    "drtg": float,  # points allowed per 100 possessions
}
```

### Coverage

- Exactly one primary entry per current NBA franchise full name used on fixtures (30 teams).
- Exact-name lookup only; empty / unknown → `None`.
- Optional aliases only if the codebase already dual-names a franchise (not required for acceptance).

### Value guidelines

- Soft public-consensus multi-year-ish levels (not live season scrape).
- Typical band roughly **ORtg/DRtg ∈ [105, 125]**.
- Net rating `ortg - drtg` should order strong vs weak franchises in an obviously correct direction for a few named checks (e.g. a top net team above a bottom net team).
- Comment in module: soft signal; operators update by PR.

## Architecture / data flow

```
_TEAM_RATINGS[team_name]
    → ratings_for_team(name)  # exact; None if missing
    → NBAAdapter.fetch_all_data
         if home and away both resolve:
             custom.ortg_home / drtg_home / ortg_away / drtg_away
         else:
             omit all four
    → BasketballEngine net_rating soft (unchanged)
```

### Module boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `nba_team_ratings.py` | Static table + pure lookup | none (no IO) |
| `NBAAdapter` | Dual-side inject / omit | `nba_team_ratings` |
| `BasketballEngine` | Unchanged consumer | existing |

## Error handling / defaults

- Exception inside ratings enrich: log at debug, leave all four unset.
- Missing key or empty name → `None` for that side → omit all four.
- No network, no new env vars, no DB schema.

## Testing

1. **`nba_team_ratings` unit**
   - 30 primary franchise keys present
   - All ortg/drtg in `[105, 125]`
   - At least one ordered pair: strong net > weak net
   - Unknown / empty name → `None`
2. **Adapter**
   - Known home+away (e.g. Boston Celtics vs Los Angeles Lakers) → four custom fields set from table
   - Unknown teams → none of the four keys present
3. **Engine**
   - Existing `test_basketball_net_rating.py` stays green (it supplies custom explicitly)
4. **Regression**
   - Tests that previously assumed adapter stub pace/ortg values updated accordingly

## Acceptance criteria

- [ ] `nba_team_ratings.py` with 30-team static ORtg/DRtg + `ratings_for_team`
- [ ] Adapter injects four fields only when both sides resolve; removes global ortg/drtg stubs
- [ ] Default adapter custom no longer hard-codes pace/tpct
- [ ] Engine / factor registry untouched for net_rating math/weight
- [ ] Unit + adapter tests cover coverage, range, direction, inject/omit
- [ ] Backlog P1-B4 note updated after implementation
- [ ] CHANGELOG entry on ship

## Follow-ups (explicitly later)

- Season-dynamic ratings from API or local possession model
- Team-specific pace / 3P% soft factors if an engine path consumes them
- Alias map for historical renames beyond exact fixture names

## Open decisions (resolved in brainstorm)

| Topic | Decision |
|-------|----------|
| Source | Code-local 30-team static table |
| Fields | ORtg + DRtg only |
| Module | Dedicated `nba_team_ratings` + adapter inject |
| Missing team | Omit all four (None path), not league avg |
| pace/tpct stubs | Remove from default custom |
| Engine | Unchanged |
