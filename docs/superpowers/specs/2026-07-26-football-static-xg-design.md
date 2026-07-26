# Football Static xG Table (P1-F5) — Design

**Date:** 2026-07-26  
**Status:** Approved for planning  
**Backlog:** P1-F5 (partial → static per-team xG/90 when true API is unavailable)

## Problem

`FootballMultiFactorEngine` already fuses a soft `xg` factor (weight ~0.07):

```text
share_h = xg_home / (xg_home + xg_away)
home_win ≈ 0.25 + share_h * 0.50
draw ≈ 0.28
away_win ≈ 0.25 + (1 - share_h) * 0.50
```

Both `custom.xg_home` and `custom.xg_away` must be non-null or the factor is unavailable and its weight redistributes.

Today `enrich_situational_features` writes those fields only from **goals-per-game** on historical / club form stats:

```text
custom.xg_* = goals_per_game  # proxy, not expected goals
```

That makes the soft path “available” when form stats exist, but the signal is **not xG**: finishing variance and shot quality are ignored, and many club sides still lack any value. Backlog P1-F5 notes MultiFactor fusion is done; the gap is a better data side without blocking on live API-Football.

## Goals

1. Add a pure module `football_xg.py` with a **code-local static table** of per-team attack xG per 90 (soft multi-season consensus).
2. After existing goals-proxy writes, if **both** home and away resolve in the table, **overwrite** `custom.xg_home` / `custom.xg_away` and set `custom.xg_source = "static_table"`.
3. If either side misses, **leave** whatever goals proxy (or empty) already wrote — never one-sided static overwrite.
4. Leave MultiFactor xG formula, clamps, and registry weights **unchanged**.
5. Zero runtime network dependency; no new config keys; no DB schema.

## Non-goals

- Live API-Football / Understat / FBref pulls this round
- True match-level pre-match xG models or Dixon-Coles parameter retune
- Writing only one side when the other is unknown
- Replacing form / rest / h2h sources
- Changing FeatureBuilder field names or engine weight profiles
- Full world-team coverage (national teams remain on goals proxy / CSV)

## Approved approach

**Option A — dedicated module + dual-side overwrite**

1. Create `backend/app/sports/football/football_xg.py`:
   - `_TEAM_XG: dict[str, float]` — normalized name → xG/90
   - `_normalize(name) -> str` — lower + whitespace collapse (same spirit as `club_form._normalize`)
   - `xg_for_team(team_name: str) -> float | None`
2. In `enrich_situational_features` (`adapters/_shared.py`), **after** goals-per-game proxy writes:
   - `xh = xg_for_team(home_name)`, `xa = xg_for_team(away_name)`
   - If both are non-null floats: set `custom.xg_home`, `custom.xg_away`, `custom.xg_source = "static_table"`
   - Else: do nothing (keep proxy or absence)
3. Wrap static enrich in try/except; on failure log debug and leave existing fields.

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Only fill when proxy missing | Leaves weak goals proxy in place for big clubs that already have GPG; user chose static-priority overwrite |
| C. Drop goals proxy entirely | Sparse table → many matches lose xG factor entirely; worse than dual-source |
| D. Inline dict in adapter | Hard to unit-test pure lookup; diverges from injury / NBA ratings pattern |
| Runtime API first | Rate limits, key dependency; static soft matches P1-B4 / P1-F3 this cycle |

## Data model

### Lookup

```python
def xg_for_team(team_name: str) -> float | None:
    """Return soft xG per 90 for a club name, or None if unknown/empty."""
```

### Table entry

- Key: normalized English club name as commonly used on fixtures (e.g. `"arsenal"`, `"real madrid"`, `"bayern munich"`).
- Value: `float` attack xG/90 in a soft band roughly **[0.8, 2.5]**.
- Optional aliases: only for well-known dual names already common in kernel feeds (e.g. `"man city"` / `"manchester city"`) if needed for hit rate; keep alias set small.

### Coverage guidelines

- Primary: current big-five league squads + frequent UCL participants (on the order of **~80–120** rows, not every lower-league club).
- Soft public-consensus multi-year-ish attack rates (not a live scrape snapshot).
- Ordering should be directionally correct for a few named checks (e.g. a top attack side above a mid-table side).
- Module comment: soft signal; operators update by PR.

## Architecture / data flow

```text
goals_per_game (historical / club_form)
    → optional custom.xg_* proxy write

xg_for_team(home), xg_for_team(away)
    → if both hit:
         custom.xg_home / xg_away  (overwrite)
         custom.xg_source = "static_table"
    → else:
         leave proxy or omit

FeatureBuilder passthrough (unchanged)
    → FootballMultiFactorEngine soft xg (unchanged)
```

### Module boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `football_xg.py` | Static table + pure lookup | none (no IO) |
| `enrich_situational_features` | Proxy first, dual-side static overwrite | `football_xg` |
| `FootballMultiFactorEngine` | Unchanged consumer | existing `custom.xg_*` |
| `FootballFeatureBuilder` | Unchanged passthrough | existing |

## Error handling / defaults

| Condition | Behavior |
|-----------|----------|
| Empty / unknown team name | `xg_for_team` → `None` |
| Only one side resolves | Do **not** overwrite; keep proxy / empty |
| Both resolve | Overwrite both xG fields + set `xg_source` |
| Import or lookup raises | Debug log; leave existing fields (fail-closed) |
| Proxy and static both absent | Omit `xg_*` (engine redistributes) |

Fail-closed: never invent xG from Elo or odds.

## Testing

1. **Unit — `football_xg`**
   - Known club returns float in band
   - Empty / unknown → `None`
   - Normalization: case / extra spaces hit the same row
   - Optional alias hits same value as primary key (if aliases included)
2. **Adapter enrich**
   - Both static hits → rates match table; `xg_source == "static_table"` (even if proxy was written first)
   - One side unknown → proxy (or absence) preserved; no partial static write
   - Both unknown → no static source key; proxy path behavior unchanged
3. **Regression**
   - Existing MultiFactor `test_xg_soft_factor_*` stay green (they inject `custom.xg_*` directly)
   - Do not change engine weights or formula tests

## Documentation

- `CHANGELOG.md` Unreleased: P1-F5 static xG table note
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F5 status → partial with static table description; true API still pending
- This spec; implementation plan under `docs/superpowers/plans/`

## Acceptance criteria

1. `xg_for_team` pure unit tests pass without network/DB.
2. Enrich overwrites `xg_*` only when **both** teams resolve from the static table.
3. Goals proxy remains fallback when static is incomplete.
4. MultiFactor xG formula/weight and FeatureBuilder unchanged.
5. CHANGELOG + backlog updated; no secrets, no new env vars.

## Implementation notes (for planning)

- Follow TDD: RED unit tests → GREEN module → adapter tests RED → GREEN enrich wire → docs.
- Prefer Subagent-Driven Development with the same runner as recent football tasks:  
  `C:\Python314\python.exe` and `PYTHONPATH=.../backend`.
- Do not push to origin unless the user explicitly asks.
