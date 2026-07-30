# Football Static Referee Bias Table (P1-F8) — Design

**Date:** 2026-07-26
**Status:** Approved for planning
**Backlog:** P1-F8 (partial → populate static referee home_bias when true referee stats API/DB is unavailable)

## Problem

`FootballMultiFactorEngine` already fuses a soft `referee` factor (weight 0.02):

```text
if custom.referee_home_win_rate present → use as home share
elif custom.referee_home_bias present → home_rate = 0.5 + 0.5 * bias
else → factor unavailable, weight redistributes

# 3-way soft:
draw_mass = 0.28
remain = 1 - draw_mass
home_win = remain * hr
away_win = remain * (1 - hr)
hr clamped to [0.20, 0.80]
```

The football adapter already has `enrich_referee_features`:

```text
1. Pass-through custom.referee_home_win_rate / referee_home_bias if set
2. Copy environment.referee → custom.referee_name
3. Lookup _REFEREE_HOME_BIAS[lower(name)] → write bias + referee_source=static_map
```

Today `_REFEREE_HOME_BIAS = {}` is **empty**. In production this means:

- Names may be copied from environment, but **bias is almost never written**
- MultiFactor referee soft almost always redistributes weight
- Backlog P1-F8 notes engine + enrich skeleton done; the gap is a **usable static data side** without a live referee API or DB column

## Goals

1. Add a pure module `football_referee.py` with a **code-local static table** of referee home bias.
2. Expose `bias_for_referee(name: str) -> float | None` (empty/unknown → `None`; hit → clamp `[-0.25, 0.25]`, `round(2)`).
3. Change `enrich_referee_features` to call `bias_for_referee` instead of the empty in-adapter map; remove adapter-local `_REFEREE_HOME_BIAS`.
4. Preserve pass-through priority: if `custom.referee_home_win_rate` or `custom.referee_home_bias` is already set, **do not overwrite**.
5. On static hit: write `custom.referee_home_bias` and `custom.referee_source = "static_map"` (keep existing source string for FE/tests).
6. On miss: keep `referee_name` only; leave rate/bias unset.
7. Leave MultiFactor referee formula, clamps, and registry weights **unchanged**.
8. Zero runtime network; no new config keys; no DB schema.

## Non-goals

- Live API-Football / Opta / league official referee stats pulls this round
- DB-backed referee history tables or kernel schema columns
- Writing `referee_home_win_rate` from static data this round (engine derives rate from bias)
- Changing MultiFactor draw mass, hr clamp, or weight profiles
- Full global referee coverage (top leagues + UCL-common names only)
- Weather / venue work (P1-F7 residual)

## Approved approach

**Option B — dedicated module + pure lookup (user-selected)**

1. Create `backend/app/sports/football/football_referee.py`:
   - `_REFEREE_HOME_BIAS: dict[str, float]` — keys already lower/strip form (and a few common aliases)
   - `_normalize(name) -> str` — lower + whitespace collapse (same spirit as `football_xg` / `football_style`)
   - `bias_for_referee(name: str) -> float | None`
2. In `enrich_referee_features` (`adapters/_shared.py`):
   - Keep name pass-through from `environment.referee` / `custom.referee_name`
   - Keep early return when rate or bias already present
   - Replace dict lookup with `bias_for_referee(name)`
   - On hit: set `referee_home_bias`, `referee_source="static_map"`, ensure `referee_name`
   - Delete module-level empty `_REFEREE_HOME_BIAS` from `_shared.py`
3. Wrap static path failures in the same best-effort style as other enrich helpers (existing function is not fully try/except-wrapped; keep behavior simple and avoid inventing rates on errors).

### Rejected alternatives

| Option | Why not |
|--------|---------|
| A. Inline expand `_REFEREE_HOME_BIAS` in adapter | Works, but diverges from F5/F6 pure-module pattern; harder pure unit tests |
| C. External JSON/CSV load | Path/packaging/missing-file branches; no ops requirement this round |
| Write both rate and bias from table | User chose bias-only; engine already converts bias → rate |
| Overwrite existing custom rate/bias | Breaks scrapers/tests that inject explicit stats |
| Runtime API first | Rate limits, keys; static soft matches P1-F5/F6 this cycle |

## Data model

### Lookup

```python
def bias_for_referee(name: str) -> float | None:
    """Soft home-win bias for a referee display name, or None if unknown/empty.

    Bias is in [-0.25, 0.25] where positive favors home win share via:
      home_rate = 0.5 + 0.5 * bias
    """
```

### Table entry

- Key: normalized English referee name as commonly seen on fixtures/feeds
  (e.g. `"michael oliver"`, `"anthony taylor"`, `"daniele orsato"`).
- Value: `float` soft home bias in **[-0.15, 0.15]** typical band (hard clamp still `[-0.25, 0.25]`).
- Aliases: small set only (punctuation / diacritics / common shortenings), e.g. `"cüneyt çakır"` / `"cuneyt cakir"`.
- Soft public-consensus directional priors — **not** a live season scrape. Module comment: operators update by PR.

### Coverage guidelines

- Primary: frequent EPL / La Liga / Serie A / Bundesliga / Ligue 1 / UCL match officials (~**20–40** rows).
- Directional checks in tests: known name returns non-null in band; unknown → `None`; empty → `None`.
- Do **not** invent extreme biases near clamp edges for “celebrity” names; keep soft and mild.

## Architecture / data flow

```text
environment.referee / custom.referee_name
    → custom.referee_name (pass-through)

custom.referee_home_win_rate or referee_home_bias already set?
    → return (no static write)

bias_for_referee(name)
    → hit:  custom.referee_home_bias + referee_source="static_map"
    → miss: name only (factor stays unavailable in MultiFactor)

FootballMultiFactorEngine
    → bias → home_rate → soft 3-way referee factor (unchanged)
```

## Adapter contract (enrich_referee_features)

| Case | custom fields after enrich |
|------|----------------------------|
| Rate/bias already set | unchanged; source not forced to static_map |
| Name known in static table | `referee_name`, `referee_home_bias`, `referee_source="static_map"` |
| Name unknown | `referee_name` only |
| No name | no referee fields invented |

## Testing

1. **Unit (`test_football_referee.py` or extend adapter-local pure tests):**
   - `bias_for_referee("")` / whitespace → `None`
   - Unknown name → `None`
   - Known name → float in `[-0.25, 0.25]`
   - Alias pair maps to same bias when aliases exist
2. **Adapter (`test_adapter_shared.py`):**
   - Static hit when only `environment.referee` present → bias + `static_map`
   - Existing `referee_home_bias` not overwritten
   - Existing `referee_home_win_rate` not overwritten / no forced static bias
   - Unknown name → name only, no bias
3. **Regression:** existing monkeypatch test that set `sh._REFEREE_HOME_BIAS` must be rewritten to either:
   - patch `bias_for_referee`, or
   - use a name present in the real static table
4. MultiFactor referee tests remain valid (they inject custom rate/bias directly).

## Documentation / backlog

- `CHANGELOG.md` Unreleased: static referee table + module + enrich call
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F8 row: static home_bias table via `bias_for_referee`; true stats API/DB still pending

## Risks / mitigations

| Risk | Mitigation |
|------|------------|
| Soft biases misread as real FA stats | Module + changelog state soft prior; mild values |
| Name mismatch (feeds use different spelling) | `_normalize` + small alias set; miss leaves factor off |
| Tests depend on removed `_REFEREE_HOME_BIAS` | Update adapter tests in same change |
| Scope creep into yellow/red card rates | Explicit non-goal; only home_bias |

## Acceptance criteria

1. `bias_for_referee` pure module exists with ≥20 static entries covering top-league names.
2. Adapter no longer holds `_REFEREE_HOME_BIAS`; uses `bias_for_referee`.
3. Static hit writes bias + `referee_source=static_map`; never overwrites existing rate/bias.
4. Unknown/empty names do not invent bias.
5. MultiFactor formula/weights unchanged; related unit + adapter tests green.
6. CHANGELOG + backlog P1-F8 updated.
