# Football Club Geo Travel + Venue Altitude (P1-F7) — Design

**Date:** 2026-07-26  
**Status:** Approved for planning  
**Backlog:** P1-F7 (partial → club geo travel + static venue altitude; weather still pending)

## Problem

`FootballMultiFactorEngine` already fuses:

| Factor | Weight (default) | Inputs |
|--------|------------------|--------|
| `travel` | ~0.03–0.05 | `custom.travel_km_away` (fallback `general.travel_distance_km`) + optional `timezone_offset_hours_away` via `travel_prob_home` |
| `altitude` | ~0.02 | `custom.venue_altitude_m` / `altitude_m`; soft home edge only when **≥ 1500 m** |

Adapter today (`adapters/_shared.py`):

1. **Altitude** — pass-through only if fixture/env/custom already carries a value. No static table → almost always empty for clubs.
2. **Travel** — `travel_between_teams(home, away, competition)` using `team_geo.resolve_city`. Football sport codes only consult **`_FOOTBALL_NATIONAL`** (capitals). Club names (Arsenal, Real Madrid CF, …) miss → `travel_known=false` → travel factor redistributes.

Weather fields exist on FeatureBuilder but MultiFactor does **not** consume them for football. True weather sources remain out of scope this round (backlog “俱乐部/天气真源仍待”).

## Goals

1. Extend `backend/app/sports/_shared/team_geo.py` with a **code-local club city table** `_FOOTBALL_CLUBS: dict[str, tuple[float, float, int]]` (lat, lon, utc_offset hours).
2. Change football `resolve_city` path: **club table first, then national table** for football/soccer/wc/epl/laliga/ucl (and related league codes already routed to football).
3. Add pure lookup `altitude_m_for_team(team_name: str) -> float | None` in `team_geo` (or same module) backed by a small static altitude map keyed by normalized club/national home venue.
4. Adapter altitude enrich: **fill only when** `custom.venue_altitude_m` / `altitude_m` / env altitude are **all missing** after existing pass-through; set `custom.venue_altitude_m` and `custom.altitude_source = "static_table"` when static hits. **Do not overwrite** an already-present altitude value.
5. Leave existing national travel path working; leave MultiFactor travel/altitude formulas, thresholds (≥1500 m), and weights **unchanged**.
6. Zero runtime network; no new config keys; no DB; **no weather** this round.

## Non-goals

- Live geocoding / weather APIs / stadium roof feeds
- Changing MultiFactor travel/altitude math or weights
- Per-stadium precise pitch coordinates (city-level is enough)
- Full lower-league / every national team altitude coverage
- Writing weather soft factors into MultiFactor
- Overwriting explicit fixture-provided altitude with static values

## Approved approach

**Option A — extend `team_geo` (club geo + altitude in same shared module)**

1. **`_FOOTBALL_CLUBS`** — English fixture-style keys (and small alias set matching xG/style hit rate: Man City, Real Madrid CF, FC Bayern München, …). Values: approximate home-city (lat, lon, utc_offset). Soft signal; operators update by PR.
2. **`resolve_city`** for football codes:
   ```text
   _lookup(name, _FOOTBALL_CLUBS) or _lookup(name, _FOOTBALL_NATIONAL)
   ```
3. **`_FOOTBALL_ALTITUDE_M: dict[str, float]`** — sparse; only venues that matter for the ≥1500 m gate or useful documentation (e.g. high Andean / Mexican / Ethiopian clubs if in coverage; most European clubs omit or store sea-level-ish and lookup returns None unless ≥ meaningful). Prefer: only store rows where altitude is **known and useful**; unknown → `None`. Engine only activates altitude factor at ≥1500 m, so lowland clubs need not appear.
4. **`altitude_m_for_team(name)`** — normalize + lookup; empty/unknown → `None`; defensive clamp e.g. [0, 4500].
5. **Adapter** — after current altitude pass-through block, if still no altitude: `alt = altitude_m_for_team(match.home.name)`; if not None, write `venue_altitude_m` + `altitude_source`.
6. Travel block already calls `travel_between_teams` — no structural change once `resolve_city` hits clubs.

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Separate `football_venue.py` only | Duplicates resolve/travel patterns already in `team_geo`; user chose extend `team_geo` |
| C. Geo in team_geo, altitude separate module | Extra file for one sparse map; acceptable later if altitude grows; YAGNI this round |
| Always overwrite altitude with static | Spec: fill-only when missing so fixture truth wins |
| Weather climate proxy | Explicitly out of scope this round |

## Data model

### Club geo row

```text
name → (lat: float, lon: float, utc_offset_hours: int)
```

- Keys: display-ish names as stored in tables (lookup already normalizes + fuzzy contains / last-token rules in `_lookup`).
- Include aliases used on fixtures (same spirit as `football_xg` / `football_style`).

### Altitude lookup

```python
def altitude_m_for_team(team_name: str) -> float | None:
    """Home-venue altitude meters, or None if unknown/empty."""
```

### Coverage guidelines

- **Clubs geo:** big-five + frequent UCL (~same set as static xG/style where practical).
- **Altitude:** sparse high-altitude and any known special cases; most clubs absent → `None` → altitude factor unavailable (weight redistributes) — correct.
- Directional travel check: e.g. London club vs Madrid club → `travel_known` True and positive km.

## Architecture / data flow

```text
resolve_city(team, football*)
    → club table → national table → None

travel_between_teams(home, away, competition)
    → both resolve → travel_km_away, timezone_offset_hours_away, travel_known

adapter (existing travel block)
    → general.travel_distance_km + custom travel keys

adapter altitude:
    pass-through env/custom if present
    else altitude_m_for_team(home)
         → venue_altitude_m + altitude_source=static_table

FeatureBuilder / MultiFactor
    → unchanged consumers
```

### Module boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `team_geo.py` | Club + national geo; altitude lookup; haversine / travel helpers | none (no IO) |
| `_shared` adapter | Pass-through altitude; fill-from-static; existing travel call | `team_geo` |
| MultiFactor | Unchanged | existing fields |

## Error handling / defaults

| Condition | Behavior |
|-----------|----------|
| Unknown club/national name | `resolve_city` → None; travel_known false |
| Only one side geo-known | travel_known false (existing dual-side rule) |
| Altitude already on raw | Keep; do not set static source |
| Static altitude miss | Leave altitude empty |
| Exception in enrich | Debug log; leave existing fields (fail-closed) |

## Testing

1. **Unit — `team_geo`**
   - Club name resolves (e.g. Arsenal / Real Madrid CF)
   - National still resolves (e.g. Brazil)
   - `travel_between_teams` two clubs → `travel_known` and km > 0
   - Unknown club → travel_known false
   - `altitude_m_for_team` known high venue in band / unknown None / empty None
2. **Adapter (optional focused)**
   - Static altitude fill when custom empty
   - Does not overwrite pre-set `venue_altitude_m`
3. **Regression**
   - Existing MultiFactor travel/altitude tests stay green
   - NBA/NHL/MLB resolve_city paths unchanged

## Documentation

- `CHANGELOG.md` Unreleased: P1-F7 club geo + static altitude
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F7 status → partial with club geo + static altitude; weather still pending
- This spec; plan under `docs/superpowers/plans/`

## Acceptance criteria

1. Club fixtures can get `travel_known` when both clubs are in the static geo table.
2. National-team travel still works.
3. Static altitude fills only when missing; never overwrites provided altitude.
4. MultiFactor travel/altitude formula/weight and FeatureBuilder unchanged.
5. No weather work; no network; no new env vars.
6. CHANGELOG + backlog updated.

## Implementation notes (for planning)

- TDD: RED geo/altitude unit tests → GREEN tables + resolve → adapter altitude fill tests → docs.
- Prefer Subagent-Driven Development; Python `C:\Python314\python.exe`, `PYTHONPATH=.../backend`.
- Do not push unless user asks.
- Reuse `_lookup` / `_normalize` already in `team_geo` (fuzzy match is intentional for US sports — keep behavior; club keys should still be precise enough for fixture names).
