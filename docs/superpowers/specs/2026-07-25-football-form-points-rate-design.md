# Football Form Points Rate (P1-F1) — Design

**Date:** 2026-07-25  
**Status:** Approved for planning  
**Backlog:** P1-F1 (partial → points-per-game form rate for football adapter)

## Problem

`FootballMultiFactorEngine` fuses a soft `form` factor: it reads `team.form_home` / `team.form_away` as values in `[0, 1]`, takes `form_diff = clamp(home - away, -0.5, 0.5)`, and applies `form_diff * 0.25` as a home-edge adjustment. Missing either side redistributes the form weight.

Today the football adapter writes:

```text
form_* = wins / played
```

Draws contribute **zero**. That is a basketball-style win rate, not a football points rate. Two teams with identical win counts but different draw profiles look the same; a side that draws a lot is under-credited relative to league table semantics.

Data already available at the write site:

| Source | When used | Fields |
|--------|-----------|--------|
| `get_historical_team_stats` (international CSV) | National teams / name match | `wins`, `draws`, `losses`, `played` |
| `team_form_from_kernel` (`club_form`) | Club comps when CSV misses a side | same W/D/L/played shape |

Both paths already return draws; only the **conversion to `form_*`** ignores them.

## Goals

1. When W/D/L stats exist with `played > 0`, write  
   `form_* = (3*W + D) / (3*N)` in `[0, 1]` (rounded to 4 decimals).
2. Apply the same formula for **both** historical CSV and club kernel paths via a **single write site** in `enrich_situational_features`.
3. Leave MultiFactor form weight, sensitivity (`* 0.25`), and clamps **unchanged**.
4. Leave `FootballFeatureBuilder` passthrough unchanged.
5. Do **not** change NBA/MLB/NHL adapters or `sports/_shared/rest_form.py` (those stay win-rate form).
6. No new network calls, feature flags, or DB schema.

## Non-goals

- Exponential / recency-weighted form over the last N matches
- Goal-difference or xG blend into form
- Expanding kernel fixture coverage or team-alias matching
- Changing engine fusion formulas or default form weight
- Unifying football online form with Phase 9 `rest_form.form_as_of` (intentional split: football points rate online; US sports win rate in backtest helpers)
- Writing neutral `0.5` when no history (keep current: omit key → form unavailable)

## Approved approach

**Option A — single write-site formula swap (recommended)**

1. Add pure helper `points_form_rate(wins, draws, played) -> float | None` on the football form surface (prefer `club_form.py` to avoid a one-function module).
2. In `enrich_situational_features` (`adapters/_shared.py`), replace both:

   ```python
   raw["team"]["form_home"] = round(wins / played, 4)
   # and form_away
   ```

   with:

   ```python
   rate = points_form_rate(wins, draws, played)
   if rate is not None:
       raw["team"]["form_home"] = rate  # already rounded inside or at call site
   ```

3. Leave `get_historical_team_stats` and `team_form_from_kernel` return shapes as-is (still W/D/L/played).

### Rejected alternatives (this round)

| Option | Why not |
|--------|---------|
| B. Each data source returns `form_rate` | Two implementations of the same formula; WC service touch surface larger |
| C. Club-only points rate | Same field, two semantics (NT win rate vs club points) — hard to explain |
| Change `rest_form.form_as_of` | Cross-sport backtest impact; out of scope |

## Formula

```text
points_form_rate(W, D, N) =
    (3*W + D) / (3*N)   if N > 0
    None                otherwise
```

| Case | Result |
|------|--------|
| All wins (N=N) | `1.0` |
| All draws | `1/3 ≈ 0.3333` |
| All losses | `0.0` |
| W=1, D=1, N=2 | `(3+1)/6 ≈ 0.6667` (old win rate was `0.5`) |
| N≤0 / missing stats | do not write `form_*` |

Implementation notes:

- Coerce with non-negative ints: `W = max(0, int(wins or 0))`, same for D and N.
- Prefer `played` as N even if `W+D+L != N` (same as today’s use of `played`).
- `round(..., 4)` at the write site or inside the helper (pick one; tests assert `pytest.approx`).
- Never emit NaN or values outside `[0, 1]` for valid non-negative W,D,N with N>0 (mathematical bound holds when `W+D ≤ N`; if dirty data has `W+D > N`, clamp result to `[0, 1]`).

## Data flow

```text
historical CSV ──┐
                 ├─► stats {wins, draws, losses, played, ...}
club_form kernel ┘
                 │
                 ▼
enrich_situational_features
  form_* = points_form_rate(wins, draws, played)   ← only write site
                 │
                 ▼
FootballFeatureBuilder passthrough
                 │
                 ▼
FootballMultiFactorEngine form factor (unchanged)
```

Rest days, xG proxy from goals/game, h2h, injury, schedule density: **out of scope** (unchanged).

## Error handling

| Condition | Behavior |
|-----------|----------|
| Historical / club_form import or query fails | Existing try/except + debug log; no form write |
| Stats present but `played == 0` | `points_form_rate` → None; omit key |
| Only one side has form | Engine marks form unavailable (existing redistribution) |
| Dirty W/D/N | Non-negative ints; clamp rate to `[0, 1]` |

Fail-closed: never invent form from Elo or odds.

## Testing

1. **Unit — `points_form_rate`**
   - all wins / all draws / all losses
   - mixed W1 D1 N2 → ~0.6667
   - N=0 / None-ish → None
   - clamp when over-credited dirty inputs (optional if easy)
2. **Adapter enrich** (extend `test_adapter_shared` or dedicated): mock / seed stats with draws; assert `form_*` equals points rate, not win rate.
3. **Regression**
   - Update any football test that hard-codes old `wins/played` form expectations.
   - Do **not** change `test_rest_form` or NBA/MLB/NHL form expectations.
4. **Engine**
   - Existing MultiFactor form tests stay green if they inject form scalars directly (no change).

## Documentation

- `CHANGELOG.md` Unreleased: P1-F1 points-rate form note
- `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` P1-F1 status → partial with points rate description
- This spec; implementation plan under `docs/superpowers/plans/`

## Acceptance criteria

1. `points_form_rate(1, 1, 2) == pytest.approx(0.6667, rel=1e-3)` (or exact 0.6667 if rounded to 4dp).
2. When stats include draws, enrich `form_*` ≠ `wins/played` and equals points rate.
3. MultiFactor form weight / formula / FeatureBuilder unchanged.
4. `rest_form` and non-football form tests untouched and green.
5. CHANGELOG + backlog updated.

## Risks

| Risk | Mitigation |
|------|------------|
| Tests/docs assume win-rate form | Grep football tests for `form_home` / `wins / played`; update only football paths |
| Semantic split vs Phase 9 rest_form | Document intentional: football online = points rate; US sports helpers = win rate |
| All-draw teams ≈ 0.33 look “weak” vs 0.5 prior | Symmetric diff; no engine retune this round |

## Implementation sketch (for planning)

1. RED: unit tests for `points_form_rate` + enrich expectation with draws.
2. GREEN: implement helper in `club_form.py`; switch both form writes in `_shared.py`.
3. Run focused pytest (`test_club_form`, `test_adapter_shared`, multi-factor smoke).
4. Docs: CHANGELOG + backlog.
