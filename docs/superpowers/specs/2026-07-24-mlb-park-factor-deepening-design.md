# MLB Park Factor Deepening (P1-M2) — Design

**Date:** 2026-07-24  
**Status:** Approved for planning  
**Backlog:** P1-M2 (partial → fuller static coverage)

## Problem

`MLBAdapter` injects a single scalar `custom.park_factor` for `BaseballEngine` soft factor `park`. Today `_PARK_FACTORS` covers only ~15 franchises with coarse hand-tuned values; missing teams silently default to **1.0** (neutral). That under-states extreme parks and inconsistently treats “unknown” as league-average.

## Goals

1. Cover **all 30 MLB franchises** with static multi-year-ish **runs** park factors (`1.0` = league-average scoring environment).
2. Keep the existing feature contract: one scalar `custom.park_factor`.
3. Leave engine formula/weights unchanged this round.
4. Prefer zero runtime network dependency (static table only).

## Non-goals

- HR-specific or LHB/RHB platoon park splits
- Season-dynamic factors from MLB Stats API
- External CSV/JSON asset pipeline
- Re-calibrating BaseballEngine `park` sensitivity (`(pf-1)*0.25`, clamp ±0.04) or weight `0.07`
- Venue moves mid-season modeling beyond franchise-name keys already used

## Current behavior (baseline)

| Layer | Behavior |
|-------|----------|
| Data | `_PARK_FACTORS` in `mlb_adapter.py` (~15 teams, ~0.93–1.15) |
| Lookup | `_park_factor_for_team`: exact → fuzzy substring → `1.0` |
| Inject | `fetch_all_data` → `custom.park_factor` |
| Engine | `p_park = 0.5 + clamp((pf-1)*0.25, -0.04, 0.04)`; weight ~0.07 |

## Approach (approved)

**In-place table expansion (Option A)**

1. Expand `_PARK_FACTORS` to all 30 clubs.
2. Keep alias keys already required by team canonicalization (`Athletics` / `Oakland Athletics`).
3. Refine existing entries and fill missing ones using public consensus ranges (FanGraphs / ESPN / Baseball-Reference style multi-year runs PF), clamped to roughly **0.90–1.20**.
4. Do not change `_park_factor_for_team` control flow or engine code unless a bug is found.

### Franchise coverage requirements

- Exactly one primary entry per current franchise name used by `_MLB_TEAM_IDS` / fixture home names.
- Explicit alias entries where the codebase already dual-names a club (e.g. Athletics).
- No silent reliance on fuzzy match for the canonical 30 names (fuzzy remains only as safety net).

### Value guidelines (illustrative, not hard-coded acceptance bands beyond tests)

| Band | Examples (direction) |
|------|----------------------|
| High | Colorado Rockies (Coors) |
| Mild high | Boston, Cincinnati, Texas, etc. |
| Neutral ~1.0 | Mid-pack parks |
| Mild low | Dodgers, Cardinals, etc. |
| Low | Miami, Seattle, San Francisco, San Diego |

Implementation will set concrete floats consistent with public multi-year runs factors and existing table scale.

## Architecture / data flow

```
MatchIdentity.home.name
    → _park_factor_for_team(name)
    → custom.park_factor (float)
    → BaseballEngine park soft factor
```

No new modules, APIs, or stores.

## Error handling / defaults

- Empty / unknown name → `1.0` (unchanged).
- Fuzzy match retained for odd display names; unit tests assert the **canonical 30** resolve via exact keys (or documented aliases), not fuzzy-only.

## Testing

1. **Coverage:** assert each of the 30 franchise names used by the adapter resolves to a table hit (not only the default path for “missing key”). Prefer asserting primary keys exist in `_PARK_FACTORS` for all 30 + required aliases.
2. **Directionality:** Coors highest (or among highest); low-run parks (e.g. Marlins / Mariners / Giants) below 1.0 and below Coors.
3. **Range:** all primary values in `[0.90, 1.20]`.
4. **Engine:** existing `test_baseball_park_and_bullpen` remains green without formula changes.
5. **No network** in these tests.

## Docs / backlog

- `CHANGELOG.md` Unreleased: 30-team static runs park table deepening (P1-M2).
- `OPPORTUNITY_BACKLOG_2026-07-17.md`: P1-M2 note updated to full static 30-team coverage; still note HR/split/dynamic as future.

## Acceptance criteria

- [ ] All 30 franchises have explicit static runs PF entries (plus Athletics aliases as needed).
- [ ] Adapter still emits single `custom.park_factor`.
- [ ] Engine park soft path unchanged.
- [ ] Unit tests for coverage + direction + range pass.
- [ ] CHANGELOG + backlog updated.

## Future extensions (out of scope)

- Multi-key custom: `park_factor_runs`, `park_factor_hr`, L/R splits.
- Season rolling PF from team home/away scoring.
- Config file for seasonal refresh without code edit.
