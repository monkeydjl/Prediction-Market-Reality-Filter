# Football Club H2H from Kernel (P1-F4) — Design

**Date:** 2026-07-25  
**Status:** Approved for planning  
**Backlog:** P1-F4 (partial → club kernel head-to-head when historical CSV is empty)

## Problem

`FootballMultiFactorEngine` already fuses a soft `h2h` factor (weight ~0.05): it reads `TeamFeatures.h2h_home_win_rate` and `h2h_draw_rate`, derives away as `max(0, 1 - home - draw)`, normalizes to a 3-way distribution, and marks the factor available only when **both** rates are non-null.

Today enrichment writes those fields only from international historical CSV:

| Layer | Behavior |
|-------|----------|
| `get_historical_h2h` | National-team / name-matchable international results |
| Club competitions | Usually no CSV rows → fields unset → h2h weight redistributed |
| Kernel fixtures | Already used for club **form** via `team_form_from_kernel`; **no** pairwise H2H helper |

P1-F4 backlog: historical h2h exists; **club league H2H still pending**. This round closes the club wire with kernel fixtures+results, same write shape as CSV. No live scrape.

## Goals

1. When historical H2H is `None`, load pairwise results for the two club names from kernel and write `h2h_home_win_rate` / `h2h_draw_rate`.
2. Kernel H2H uses **current home-team perspective** (align with `get_historical_h2h`): for each finished meeting of the two teams, count a win for the side that is the **current match home**, regardless of which side hosted historically.
3. Prefer historical CSV when present; **do not merge** sources; **do not overwrite** historical with kernel.
4. Leave MultiFactor h2h formula, weight, and FeatureBuilder passthrough **unchanged**.
5. No new network, feature flag, or DB schema.

## Non-goals

- Venue-fixed H2H (only matches at current home ground)
- Split home/away H2H fields for the engine
- Merging CSV + kernel with dedupe
- Changing MultiFactor h2h weight or normalization
- Expanding fixture ingest or team-alias coverage
- Writing neutral rates when no meetings exist (omit keys → factor unavailable)

## Approved approach

**Option A — `h2h_from_kernel` helper + enrich fallback (recommended)**

1. Add pure-ish query helper next to club form (prefer `club_form.py` to reuse `_normalize` / session patterns; optional tiny sibling module only if file bloat becomes painful):

   ```python
   def h2h_from_kernel(
       home_team: str,
       away_team: str,
       *,
       competition: str | None = None,
       before: datetime | None = None,
       max_matches: int = 20,
   ) -> dict[str, Any] | None:
       ...
   ```

2. Return shape aligned with historical H2H (subset used by enrich):

   ```python
   {
       "matches_played": int,
       "home_wins": int,      # wins for *current* home side
       "draws": int,
       "away_wins": int,      # wins for *current* away side
       "data_source": "kernel_match_results",
   }
   ```

3. In `enrich_situational_features` (`adapters/_shared.py`):
   - Keep existing historical call first.
   - If `h2h` is still falsy / None and not World Cup-only path preference: call `h2h_from_kernel(home_name, away_name, competition=..., before=kickoff)`.
   - When any source returns a dict with `matches_played > 0`, write:

     ```python
     played = max(int(h2h["matches_played"] or 0), 1)  # same guard as today for rate
     raw["team"]["h2h_home_win_rate"] = round(home_wins / played, 4)
     raw["team"]["h2h_draw_rate"] = round(draws / played, 4)
     ```

   - Prefer extracting a tiny local write block so CSV and kernel share one rate conversion (optional refactor; YAGNI if copy is two lines).

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Inline query only in `_shared.py` | Adapter bloat; hard pure unit tests |
| C. Fall back inside `get_historical_h2h` | Couples WC CSV service to club kernel DB |
| Merge CSV + kernel | Dedupe/date identity ambiguity; out of scope |

## Query rules (`h2h_from_kernel`)

1. Require non-empty `home_team` and `away_team`; if either empty or same normalized name → `None`.
2. `before`: default `now(UTC)`; only fixtures with `kickoff_utc or finished_at` **strictly before** `before` (tz-aware like `team_form_from_kernel`).
3. Join `KernelMatchFixture` + `KernelMatchResult` with both scores non-null.
4. Keep rows where the two fixture team names (normalized) equal the unordered pair `{home, away}`.
5. Optional `competition` filter: when provided (club enrich passes match competition code), filter `KernelMatchFixture.competition == competition` — **same pattern as club form**.
6. Sort by kickoff descending; take at most `max_matches` (default **20**, match historical H2H).
7. For each row, map scores into **current home perspective**:
   - If fixture home == current home: use scores as-is for current home/away.
   - Else (fixture home == current away): swap scores when scoring the current home side.
   - Increment `home_wins` / `away_wins` / `draws` from that perspective.
8. No meetings → `None`.

## Data flow

```text
get_historical_h2h(home, away, before)
        │
        ├─ dict ──────────────────────────────┐
        │                                     ▼
        └─ None → h2h_from_kernel(...) ──► write team.h2h_* rates
                                              │
                                              ▼
                                   FootballFeatureBuilder passthrough
                                              │
                                              ▼
                                   MultiFactor h2h factor (unchanged)
```

World Cup / national paths that already get CSV H2H are unchanged. Club paths gain kernel fallback only when CSV returns nothing.

## Error handling

| Condition | Behavior |
|-----------|----------|
| Historical import/call fails | Existing debug log; treat as no historical H2H |
| Kernel empty / no pair | `None`; omit h2h keys |
| Kernel session/query exception | catch + debug log; omit h2h |
| Dirty scores | skip row if scores null (already filtered) |

Fail-closed: never invent H2H from Elo or form.

## Testing

1. **Unit — `h2h_from_kernel`** (seed kernel fixtures like `test_club_form`):
   - Two meetings: current home won once, drew once → rates `0.5` / `0.5`
   - Historical venue swap still counts for current home perspective
   - Unknown team / no meetings → `None`
   - `before` excludes future-as-of rows
   - Optional: competition filter only returns matching competition rows
2. **Adapter enrich**:
   - Historical returns data → h2h written; kernel not required
   - Historical `None` + kernel data → h2h written from kernel rates
   - Both empty → no h2h keys
3. **Regression:** MultiFactor h2h tests that inject rates directly stay green; no `rest_form` changes.

## Documentation

- `CHANGELOG.md` Unreleased: P1-F4 club kernel H2H
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F4 status update
- This spec; plan under `docs/superpowers/plans/`

## Acceptance criteria

1. Kernel H2H unit tests pass for perspective, as-of, and empty cases.
2. Club enrich with CSV miss + kernel meetings populates `h2h_home_win_rate` and `h2h_draw_rate`.
3. CSV hit is not overwritten by kernel.
4. MultiFactor h2h weight/formula and FeatureBuilder unchanged.
5. CHANGELOG + backlog updated.

## Risks

| Risk | Mitigation |
|------|------------|
| Sparse kernel club results | Fail-closed omit; no fake rates |
| Name mismatch vs form path | Same `_normalize` as club form |
| Competition filter too strict | Document; same as form; can loosen later |
| World Cup path accidentally filtered | WC already has CSV; kernel fallback only when historical None |

## Implementation sketch (for planning)

1. RED: unit tests for `h2h_from_kernel`.
2. GREEN: implement helper in `club_form.py` (or sibling).
3. Wire enrich fallback + adapter tests.
4. Docs: CHANGELOG + backlog.
