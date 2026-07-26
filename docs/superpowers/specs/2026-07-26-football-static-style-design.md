# Football Static Style Stats (P1-F6) — Design

**Date:** 2026-07-26  
**Status:** Approved for planning  
**Backlog:** P1-F6 (partial → static per-team possession / shots / PPDA when true stats API is unavailable)

## Problem

`FootballMultiFactorEngine` already fuses a soft `possession` factor (weight ~0.03–0.05). Consumer order:

```text
1. custom.possession_home + possession_away  → share = home / (home+away)
   (values > 1.5 treated as percent and /100)
2. else custom.shots_* or shots_on_target_* → shot share
3. else custom.ppda_* → invert (1/ppda) share  (lower PPDA = stronger press)
```

Both sides of a chosen signal must be non-null or the factor is unavailable and its weight redistributes.

Today the football adapter only writes a **form-share proxy** after situational enrich:

```text
custom.possession_* = form share mapped to percent
custom.possession_proxy = "form_share"
```

That makes the soft path non-null when form exists, but the signal is **highly collinear with form**, and shots / PPDA are never written. Backlog P1-F6 notes MultiFactor soft fusion is done; the gap is better data without blocking on live stats APIs.

## Goals

1. Add a pure module `football_style.py` with a **code-local static table** of per-team style stats.
2. After the existing form→possession proxy, if **both** home and away resolve in the table, **overwrite**:
   - `custom.possession_home` / `custom.possession_away` (percent scale, same as proxy)
   - `custom.shots_home` / `custom.shots_away` (per-90 total shots)
   - `custom.ppda_home` / `custom.ppda_away`
   - `custom.style_source = "static_table"`
   - Clear or replace `possession_proxy` so source is unambiguous (`del` when present, or leave unset after overwrite)
3. If either side misses, **leave** whatever form proxy (or empty) already wrote — never one-sided static overwrite.
4. Leave MultiFactor possession formula, clamps, and registry weights **unchanged**.
5. Zero runtime network dependency; no new config keys; no DB schema.

## Non-goals

- Live API-Football / FBref / Understat / Opta pulls this round
- Match-level pre-match projected possession models
- Writing only one side when the other is unknown
- Changing FeatureBuilder field names or engine weight profiles
- Full national-team coverage (clubs primary; national stay on form proxy when form exists)
- Replacing form / rest / h2h / xG sources

## Approved approach

**Option A — dedicated module + dual-side overwrite**

1. Create `backend/app/sports/football/football_style.py`:
   - `_TEAM_STYLE: dict[str, tuple[float, float, float]]` — normalized name → `(possession_pct, shots_per90, ppda)`
   - `_normalize(name) -> str` — lower + whitespace collapse (same spirit as `football_xg` / `club_form`)
   - `stats_for_team(team_name: str) -> dict[str, float] | None` with keys  
     `possession_pct`, `shots_per90`, `ppda` (or `None` if unknown/empty)
2. In `fetch_raw_match_data` path in `adapters/_shared.py`, **after** the form→possession proxy block (or inside enrich after that proxy if relocated):
   - `sh = stats_for_team(home_name)`, `sa = stats_for_team(away_name)`
   - If both are non-null dicts: write all six fields + `style_source`; remove `possession_proxy` if set
   - Else: do nothing
3. Wrap static enrich in try/except; on failure log debug and leave existing fields.

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Only fill when proxy missing | Leaves weak form-collinear proxy for big clubs that already have form; user chose static-priority overwrite |
| C. Drop form proxy entirely | Sparse table → many matches lose possession factor; worse than dual-source |
| D. Expand `football_xg.py` | Couples attack xG with style stats; separate pure modules match injury / xG pattern |
| E. Inline dict in adapter | Hard to unit-test pure lookup |
| Runtime API first | Rate limits, key dependency; static soft matches P1-F5 / P1-B4 this cycle |

## Data model

### Lookup

```python
def stats_for_team(team_name: str) -> dict[str, float] | None:
    """Soft style stats for a club name, or None if unknown/empty.

    Returns:
        {
          "possession_pct": float,  # ~35–70
          "shots_per90": float,     # ~7–20
          "ppda": float,            # ~6–18, lower = stronger press
        }
    """
```

### Table entry

- Key: normalized English club name as commonly used on fixtures (same alias spirit as `football_xg`).
- Values: soft multi-year-ish consensus (not a live season scrape). Operators update by PR.
- Clamp on return (defensive):
  - possession_pct ∈ [30, 75]
  - shots_per90 ∈ [5, 25]
  - ppda ∈ [5, 20]
- Round floats sensibly (e.g. possession 1 decimal, shots/ppda 2 decimals).

### Coverage guidelines

- Primary: big-five league squads + frequent UCL participants (~same hit-rate target as `football_xg`, reuse alias keys where practical).
- Directional checks: possession-dominant side (e.g. Man City / Barça) above mid-table; low PPDA press side below passive mid-table.
- Module comment: soft signal; not live stats.

## Architecture / data flow

```text
form_home / form_away
    → optional custom.possession_* + possession_proxy="form_share"

stats_for_team(home), stats_for_team(away)
    → if both hit:
         custom.possession_home/away  (overwrite)
         custom.shots_home/away
         custom.ppda_home/away
         custom.style_source = "static_table"
         remove possession_proxy if present
    → else:
         leave proxy or omit

FeatureBuilder passthrough (unchanged custom)
    → FootballMultiFactorEngine soft possession (unchanged priority)
```

### Wire location

Prefer a small block **immediately after** the existing form→possession proxy in `_shared.py` `fetch_raw_match_data` (or factor into a helper called from the same place), so order is:

1. `enrich_situational_features` (form, h2h, rest, xG, …)
2. form possession proxy
3. **static style overwrite (this feature)**
4. altitude / travel / liquidity / …

Do **not** put static style inside `enrich_situational_features` unless that keeps the after-proxy order clearer; either is fine as long as proxy runs first and dual-side static second.

### Module boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `football_style.py` | Static table + pure lookup | none (no IO) |
| `_shared` adapter wire | Proxy first, dual-side static overwrite | `football_style` |
| `FootballMultiFactorEngine` | Unchanged consumer | existing `custom.*` keys |
| `FootballFeatureBuilder` | Unchanged passthrough | existing |

## Error handling / defaults

| Condition | Behavior |
|-----------|----------|
| Empty / unknown team name | `stats_for_team` → `None` |
| Only one side resolves | Do **not** overwrite; keep proxy / empty |
| Both resolve | Overwrite possession + shots + ppda + set `style_source`; drop `possession_proxy` |
| Import or lookup raises | Debug log; leave existing fields (fail-closed) |
| Proxy and static both absent | Omit style keys (engine redistributes) |

Fail-closed: never invent style from Elo or odds alone beyond the existing form proxy.

## Testing

1. **Unit — `football_style`**
   - Known club returns dict with all three keys in band
   - Empty / unknown → `None`
   - Normalization: case / extra spaces hit the same row
   - Common alias (e.g. Man City) matches primary
   - Directional: top possession club > mid-table possession
2. **Adapter enrich**
   - Both static hits → values match table; `style_source == "static_table"`; no `possession_proxy` (or not form_share); even if form proxy ran first
   - One side unknown → form proxy (or absence) preserved; no partial static write; no `style_source`
   - Both unknown → no `style_source`; proxy path behavior unchanged
3. **Regression**
   - Existing MultiFactor possession soft tests stay green (they inject custom directly)
   - Do not change engine weights or formula tests

## Documentation

- `CHANGELOG.md` Unreleased: P1-F6 static style stats note
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F6 status → partial with static table description; true stats API still pending
- This spec; implementation plan under `docs/superpowers/plans/`

## Acceptance criteria

1. `stats_for_team` pure unit tests pass without network/DB.
2. Enrich overwrites possession/shots/ppda only when **both** teams resolve from the static table.
3. Form→possession proxy remains fallback when static is incomplete.
4. MultiFactor possession formula/weight and FeatureBuilder unchanged.
5. CHANGELOG + backlog updated; no secrets, no new env vars.

## Implementation notes (for planning)

- Follow TDD: RED unit tests → GREEN module → adapter tests RED → GREEN wire → docs.
- Prefer Subagent-Driven Development with the same runner as recent football tasks:  
  `C:\Python314\python.exe` and `PYTHONPATH=.../backend`.
- Do not push to origin unless the user explicitly asks.
