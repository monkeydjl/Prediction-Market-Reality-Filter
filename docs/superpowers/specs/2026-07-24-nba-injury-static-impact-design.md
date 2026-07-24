# NBA Static Injury Impact (P1-B1) — Design

**Date:** 2026-07-24  
**Status:** Approved for planning  
**Backlog:** P1-B1 (partial → static Out list + role-weighted impact)

## Problem

`BasketballEngine` already fuses a soft `injury` factor (weight ~0.06): it reads `PlayerFeatures.injury_impact_home/away`, falls back to `custom.injury_impact_*`, and only marks the factor available when **both** sides are non-null. Higher impact means a worse side; `p_inj = clamp(0.5 + (inj_a - inj_h) * 0.12, 0.35, 0.65)`.

Today the pipeline never writes those fields:

| Layer | Behavior |
|-------|----------|
| `NBAAdapter.fetch_all_data` | No injury keys in `player` / `custom` |
| `BasketballFeatureBuilder` | Hard-codes `injury_impact_home/away = None` |
| Engine | Always `injury_available = False` → weight redistributed |

P1-B1 backlog text: engine soft path exists; **true roster source still pending**. This round closes the wire with a **static, code-local** Out list (no external injury API).

## Goals

1. Produce real `injury_impact_home` / `injury_impact_away` scalars in `[0, 1]` when the static table has Out rows for a team.
2. Quantify impact as **count of Out players × role tier weights**, then clamp.
3. Inject via adapter into **both** `player` and `custom` (engine dual-read, same pattern as football multi-factor).
4. Teach `BasketballFeatureBuilder` to **passthrough** `player_raw` injury fields (stop hard-coding `None`).
5. Leave `BasketballEngine` formula, clamps, and registry weight **unchanged**.
6. Zero runtime network dependency; no new config keys.

## Non-goals

- Live feeds (balldontlie injuries, ESPN/Rotowire scrape)
- Doubtful / Questionable partial weights
- Dynamic importance (minutes, PER, VORP, salary)
- Full 30-team mandatory table coverage this round (missing team → leave `None`)
- Changing engine sensitivity (`* 0.12`) or weight `0.06`
- Suspensions / load management as separate status kinds (only explicit `out` counts; load-management can be entered as `out` if operators choose)

## Approved approach

**Option A — dedicated module + adapter inject**

1. New module `backend/app/sports/basketball/nba_injury.py`:
   - Role weight constants
   - `_STATIC_INJURIES: dict[str, list[dict]]` (team full name → injury rows)
   - `summarize_injury_impact(rows) -> float | None`
   - Thin lookup helper `injury_impact_for_team(team_name: str) -> float | None`
2. `NBAAdapter.fetch_all_data` calls the helper for home/away and writes both layers when non-null.
3. `BasketballFeatureBuilder` maps `player_raw.get("injury_impact_*")` into `PlayerFeatures`.
4. Ship **1–2 example Out rows** on well-known franchise full names so unit tests and optional spot checks are non-vacuous; all other teams omit keys.

### Rejected alternatives (this round)

| Option | Why not |
|--------|---------|
| B. Everything inside `nba_adapter.py` | Adapter bloat; harder pure unit tests of weights |
| C. Compute impact only in FeatureBuilder | Diverges from MLB-style adapter scalar inject; engine already dual-reads custom |
| External API first | Rate limits / free-tier gaps / unstable schema; backlog allows static soft first |

## Data model

### Injury row (static table entry)

```python
{
    "player": str,   # display only; not used in formula this round
    "role": str,     # star | starter | rotation | bench (case-insensitive)
    "status": str,   # only "out" contributes (case-insensitive)
}
```

### Role weights (constants)

| Role | Weight |
|------|--------|
| star | 0.35 |
| starter | 0.18 |
| rotation | 0.08 |
| bench | 0.03 |

- Unknown / missing role → **bench** weight.
- Multiple Out rows → **sum** weights, then **clamp to [0, 1]**.
- Non-`out` statuses → ignored (not half-weight).
- Empty list, all non-out, or missing team key → **`None`** (do **not** write `0.0`; that would falsely claim “known healthy” and enable the injury factor with a neutral-looking side).

### Team keys

- Primary keys: balldontlie-style **full names** as stored on `KernelMatchFixture.home_team` / `away_team` (e.g. `"Boston Celtics"`).
- Optional alias keys only if the codebase already dual-names a franchise; not required for acceptance this round.
- Lookup: exact name only this round (no fuzzy); unknown → `None`.

## Architecture / data flow

```
_STATIC_INJURIES[team_name]
    → summarize_injury_impact(rows)  # Out-only, role sum, clamp; else None
    → NBAAdapter.fetch_all_data
         player.injury_impact_{home,away}
         custom.injury_impact_{home,away}
    → BasketballFeatureBuilder → PlayerFeatures.injury_impact_*
    → BasketballEngine injury soft (unchanged)
```

### Module boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `nba_injury.py` | Static table, weights, pure summarize + team lookup | none (no IO) |
| `NBAAdapter` | Resolve home/away names, inject when non-null | `nba_injury` |
| `BasketballFeatureBuilder` | Passthrough player injury fields | existing domain |
| `BasketballEngine` | Unchanged consumer | existing |

## Error handling / defaults

- Exception inside injury enrich: log at debug, leave fields unset (`None`).
- Missing table key / empty / no Out rows → `None` (omit keys or leave unset; do not force `0.0`).
- One side `None` and the other numeric → engine still treats injury as **unavailable** (both sides required); acceptable this round.
- No network, no new env vars, no DB schema.

## Testing

1. **`summarize_injury_impact` unit**
   - Single star Out → ~0.35
   - Multiple Outs sum and clamp ≤ 1.0
   - Doubtful/Questionable-only list → `None`
   - Unknown role → bench weight
   - Empty / `None` input → `None`
2. **`injury_impact_for_team`**
   - Known example franchise with Out rows → float in (0, 1]
   - Unknown franchise → `None`
3. **Adapter inject** (unit with table fixture / monkeypatch if needed)
   - When both teams resolve → `player` and `custom` both set
   - When neither resolves → no injury keys / still `None` path through feature builder
4. **FeatureBuilder**
   - `player_raw` values appear on `PlayerFeatures`
5. **Engine**
   - Existing basketball engine tests remain green; no formula changes required
   - Optional: one engine case with asymmetric impacts proves `injury` becomes available (if not already covered)

## Acceptance criteria

- [ ] `nba_injury.py` exists with documented weights, static table, pure summarize API.
- [ ] Adapter writes `injury_impact_*` to player + custom when table has Out data.
- [ ] FeatureBuilder no longer hard-codes injury impacts to `None`.
- [ ] Engine / factor registry untouched for injury math/weight.
- [ ] Unit tests cover summarize edge cases and inject/passthrough.
- [ ] Backlog P1-B1 note updated after implementation (static Out + role weights; live feed still later).
- [ ] CHANGELOG entry on ship.

## Follow-ups (explicitly later)

- Live injury feed behind the same `summarize_injury_impact` contract
- Doubtful/Questionable multipliers
- Minutes/usage-based role inference
- Writing explicit `0.0` when a fresh full-roster scan asserts “no outs” (requires a real source of truth)

## Open decisions (resolved in brainstorm)

| Topic | Decision |
|-------|----------|
| Source | Code-local static table |
| Status filter | Out only |
| Scoring | Role-tier sum, clamp [0,1] |
| Structure | Dedicated `nba_injury` module + adapter inject |
| Missing data | `None`, not `0.0` |
| Engine | Unchanged |
