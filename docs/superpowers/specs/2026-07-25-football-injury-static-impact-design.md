# Football Static Injury Impact (P1-F3) — Design

**Date:** 2026-07-25  
**Status:** Approved for planning  
**Backlog:** P1-F3 (partial → static Out list + role-weighted impact)

## Problem

`FootballMultiFactorEngine` already fuses a soft `injury` factor (weight ~0.05): it reads `PlayerFeatures.injury_impact_home/away`, falls back to `custom.injury_impact_*`, and only marks the factor available when **both** sides are non-null. Higher impact means a worse side; edge uses `inj_diff * 0.12` (same pattern as basketball).

Today the club pipeline almost never writes those fields:

| Layer | Behavior |
|-------|----------|
| `enrich_situational_features` | Optional WC-only `get_team_injury_impact`; no dual-write to `custom`; club matches typically stay empty |
| `FootballFeatureBuilder` | Already passthroughs `player_raw.injury_impact_*` |
| Engine | Injury weight redistributed when either side is null |

P1-F3 backlog text: player + custom `injury_impact_*` passthrough exists; **true roster source and importance weighting still pending**. This round closes the club wire with a **static, code-local** Out list + role weights (mirror NBA P1-B1). No external injury API.

## Goals

1. Produce real `injury_impact_home` / `injury_impact_away` scalars in `[0, 1]` when the static table has Out rows for a team.
2. Quantify impact as **sum of role-tier weights for Out rows**, then clamp to `[0, 1]`.
3. Inject via football enrich into **both** `player` and `custom` when non-null (engine dual-read).
4. Keep `FootballFeatureBuilder` passthrough as-is (already correct).
5. Leave MultiFactor injury formula, clamps, and weight table **unchanged**.
6. Zero runtime network dependency; no new config keys or DB schema.
7. Preserve existing WC player-status source as **fallback only when static returns None** for that side.

## Non-goals

- Live feeds (API-Football injuries, Transfermarkt scrape, media scrape)
- Doubtful / Questionable / suspended partial weights (only explicit `out` counts this round)
- Dynamic importance (minutes, market value, xG contribution)
- Full squad-table coverage (missing team → leave `None`)
- Changing engine sensitivity (`* 0.12`) or injury weight `0.05`
- Cross-sport shared injury module extraction (optional later; copy NBA pattern for now)
- Writing explicit `0.0` for “known healthy” without a full-roster source of truth

## Approved approach

**Option A — dedicated module + enrich inject (recommended)**

1. New module `backend/app/sports/football/football_injury.py`:
   - `ROLE_WEIGHTS` (same tiers/values as NBA)
   - `_STATIC_INJURIES: dict[str, list[dict]]` (exact club full name → rows)
   - `summarize_injury_impact(rows) -> float | None`
   - `injury_impact_for_team(team_name: str) -> float | None`
2. In `enrich_situational_features` (`adapters/_shared.py`), replace/extend the injury block:
   - Call static helper for home/away first.
   - When non-null: write **player + custom**.
   - When still None: optional WC `get_team_injury_impact` (existing try); if WC returns a value, write **player + custom** for that side only.
3. Ship **2–4 sample clubs** with example Out rows so unit tests are non-vacuous; all other teams omit keys.

### Rejected alternatives (this round)

| Option | Why not |
|--------|---------|
| B. Everything inside `_shared.py` | Adapter bloat; harder pure unit tests of weights |
| C. Shared cross-sport injury module | Extra abstraction; NBA module already works; extract later if a third sport needs it |
| External API first | Keys/rate limits/schema churn; backlog allows static soft first |

## Data model

### Injury row (static table entry)

```python
{
    "player": str,   # display only; not used in formula this round
    "role": str,     # star | starter | rotation | bench (case-insensitive)
    "status": str,   # only "out" contributes (case-insensitive)
}
```

### Role weights (constants — match NBA P1-B1)

| Role | Weight |
|------|--------|
| star | 0.35 |
| starter | 0.18 |
| rotation | 0.08 |
| bench | 0.03 |

- Unknown / missing role → **bench** weight.
- Multiple Out rows → **sum** weights, then **clamp to [0, 1]**.
- Non-`out` statuses → ignored (not half-weight).
- Empty list, all non-out, or missing team key → **`None`** (do **not** write `0.0`).

### Team keys

- Primary keys: fixture **full names** as used by football adapters / kernel fixtures, e.g.:
  - `"Real Madrid CF"`
  - `"FC Bayern München"`
  - optional third sample e.g. `"Arsenal FC"` if useful for EPL path tests
- Lookup: **exact** name only (no fuzzy, no ClubElo short-code mapping this round).
- Optional alias only if the same franchise already has dual full names in adapters and tests need both; not required for acceptance.

### Sample table intent (illustrative; plan pins exact rows)

| Team key | Example rows (roles) | Expected impact |
|----------|----------------------|-----------------|
| Real Madrid CF | 1× star out | `0.35` |
| FC Bayern München | 1× starter + 1× rotation out | `0.26` |
| (optional third) | 1× bench out | `0.03` |

Player display names may be fictional placeholders labeled as examples (same as NBA static table).

## Architecture / data flow

```
_STATIC_INJURIES[team_name]
    → summarize_injury_impact(rows)   # Out-only, role sum, clamp; else None
    → injury_impact_for_team(name)
    → enrich_situational_features
         1) static non-null → player + custom injury_impact_{home,away}
         2) still None → WC get_team_injury_impact (optional); non-null → player + custom
    → FootballFeatureBuilder → PlayerFeatures.injury_impact_*
    → FootballMultiFactorEngine injury factor (unchanged math)
```

### Inject rules

| Condition | Write |
|-----------|-------|
| Static returns float | `player` + `custom` for that side |
| Static None, WC returns float | `player` + `custom` for that side |
| Both None | omit keys / leave unset |
| Never write `0.0` for “no row” | — |

Static wins when present (do not overwrite static with WC).

## Testing strategy

1. **Unit (`test_football_injury.py`)**  
   - summarize: empty / non-out only → None  
   - single star out → 0.35  
   - starter+rotation → 0.26  
   - unknown role → bench weight  
   - clamp when sum > 1  
   - `injury_impact_for_team` exact hit / miss / empty name  

2. **Enrich inject (`test_adapter_shared.py` or focused injury class)**  
   - patch/monkeypatch static table or call with sample team names from `_make_match`  
   - both sides resolve → player + custom set  
   - unknown teams → no injury keys from static path  
   - WC fallback only when static None (patch static None + WC return)  

3. **Engine**  
   - Existing `test_injury_custom_fallback` remains green; no formula changes required.  
   - Optional: no new engine test unless inject path needs end-to-end smoke (not required if unit+enrich cover scalars).

## Acceptance criteria

- [ ] `football_injury.py` exists with documented weights, static sample table, pure summarize + team lookup API.
- [ ] Enrich writes `injury_impact_*` to **player + custom** when static (or WC fallback) returns non-null.
- [ ] Static present values are not overwritten by WC.
- [ ] Missing team → no `0.0` claim; keys omitted / None path.
- [ ] MultiFactor injury math/weights untouched.
- [ ] Unit + enrich tests green.
- [ ] Backlog P1-F3 row updated (static Out + role weights; live feed still later).
- [ ] CHANGELOG Unreleased note on ship.

## Follow-ups (explicitly later)

- Live injury feed behind the same `summarize_injury_impact` contract
- Suspension / doubtful multipliers
- Minutes / market-value role inference
- ClubElo short-code → full-name alias map if production names diverge
- Explicit healthy `0.0` when a full-roster scan asserts no outs
- Optional extract shared injury static helper with NBA

## Open decisions (resolved in brainstorm)

| Topic | Decision |
|-------|----------|
| Scope | Static role-weighted Out list (not live API) |
| Coverage | 2–4 sample clubs |
| Structure | Dedicated `football_injury` module + enrich inject |
| Status filter | Out only |
| Scoring | Role-tier sum, clamp [0,1]; same weights as NBA |
| Missing data | `None`, not `0.0` |
| WC source | Fallback when static None for that side |
| Dual write | player + custom |
| Engine | Unchanged |
