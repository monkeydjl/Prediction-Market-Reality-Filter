# Rest / Form Feature Enrichment (Phase 9 follow-up)

> **Status:** Implemented 2026-07-24  
> **Approach:** A — compute at load time (shared pure helpers + match_loader)  
> **Predecessor:** [Phase 9 Accuracy Sprint](./2026-07-16-sports-prediction-os-phase9-design.md), Optuna apply 2026-07-24

## 1. Goal

Make **rest** and **form** real, leakage-safe features on the **backtest / Optuna path**, so optimized factor weights match what online engines can actually use.

Success criteria:

1. `load_sport_matches_for_backtest(sport)` no longer emits constant `rest_days_*=2.0` / `form_*=0.5` for every row when history exists.
2. Features are **as-of kickoff**: only matches strictly before the target kickoff count.
3. Unknown rest (no prior fixture) is **`None`**, not `0` (so BacktestRunner marks rest unavailable and redistributes weight — no false B2B).
4. Form with zero prior finished games remains **`0.5`** (neutral prior; same semantic as adapters today).
5. Unit tests cover as-of filtering, empty history, and non-constant values on a small fixture sequence.
6. Optional smoke: one sport, few Optuna trials still completes after loader change.

Non-goals (this sprint):

- Pitcher ERA / goalie save% API wiring  
- BacktestRunner parity for B2B extra penalty, travel, net_rating, park, etc.  
- Schema migration / precomputed feature columns  
- Changing engine fusion formulas  
- Auto re-apply of Optuna candidates after re-tune  

## 2. Problem

Phase 9 design assumes “pre-computed rest/form/pitcher/goalie”. Implementation:

| Path | rest | form |
|------|------|------|
| Online adapters (`NBA/MLB/NHLAdapter._compute_*`) | From prior fixture kickoff | L10 win rate from finished fixtures |
| `match_loader.load_sport_matches_for_backtest` | **Hardcoded 2.0** | **Hardcoded 0.5** |

Consequences:

- Optuna trials cannot learn real rest/form signal; weights are partly noise.
- Applied weights (especially MLB high `rest`) were tuned against a flat world but run online against real rest.

## 3. Architecture

```
kernel fixtures (+ scores for form)
        │
        ▼
sports/_shared/rest_form.py     ← pure / in-memory helpers (new)
   form_as_of / rest_days_as_of
        │
        ├──────────────────────────────┐
        ▼                              ▼
match_loader (backtest)         adapters (optional follow-up)
  batch O(n log n)              call shared or keep DB queries
        │
        ▼
BacktestRunner (unchanged formulas; already handles None)
```

**Zero-invasion:** no changes to `PredictionKernel`, engines, or `domain.py`.  
Optional same-PR or follow-up: point adapters at shared helpers for one source of truth (see §6).

## 4. Shared module API

**New file:** `backend/app/sports/_shared/rest_form.py`

### 4.1 Input record shape

Minimal protocol (dicts or simple namespace) for in-memory batch use:

```python
# Each prior/current match record used by helpers:
{
  "match_id": str,
  "home_team": str,
  "away_team": str,
  "home_score": int | None,   # required for form when counting that match
  "away_score": int | None,
  "kickoff_utc": datetime | None,
}
```

Team name matching: exact string equality on `home_team` / `away_team` as stored in kernel (same as current adapters). No alias expansion in this sprint.

### 4.2 Functions

```python
def rest_days_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[MatchLike],
    *,
    exclude_match_id: str | None = None,
) -> float | None:
    """Days since team's previous kickoff strictly before `kickoff`.

    Returns None if kickoff is None or no prior match for team.
    Uses calendar day difference: max(0, (kickoff - prev_kickoff).days).
    Ignores records with missing kickoff; ignores exclude_match_id.
    """

def form_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[MatchLike],
    *,
    max_matches: int = 10,
    exclude_match_id: str | None = None,
    default: float = 0.5,
) -> float:
    """Win rate over up to `max_matches` finished games strictly before kickoff.

    A game counts if both scores are not None.
    Win: team's side score > opponent score (draws count as non-win, not excluded).
    If no prior finished games, return `default` (0.5).
    Order: most recent kickoff first, then match_id for stability.
    """

def enrich_matches_rest_form(
    matches: list[dict[str, Any]],
    *,
    max_form_matches: int = 10,
) -> list[dict[str, Any]]:
    """Mutate or return copies with rest_days_home/away and form_home/away filled.

    Assumes matches may be unsorted; sorts a working copy by (kickoff, match_id)
    for deterministic as-of, but preserves caller order in the returned list
    (or documents sort-in-place — implementer chooses preserve-by-id map).
    """
```

### 4.3 Semantics (explicit decisions)

| Case | rest | form |
|------|------|------|
| No prior match for team | `None` | `0.5` |
| Prior match exists, scores missing | counts for rest timeline if kickoff present | does **not** count toward form |
| Current match in history list | excluded via `exclude_match_id` or `kickoff < as_of` | same |
| Draw | n/a | not a win (win_rate can be &lt; 0.5) |
| Timezone-naive kickoff | treat as UTC (same as `club_form.team_form_from_kernel`) | same |

**Difference from current adapter rest unknown = 0:** intentional fix. Online adapters may be updated in the same change set (recommended) so online and backtest agree.

## 5. match_loader changes

**File:** `backend/app/kernel/backtest/match_loader.py`

1. Keep join of fixtures + results; keep score filter.
2. Build intermediate list **with** `kickoff_utc` retained through enrichment.
3. Call `enrich_matches_rest_form` (history = all scored matches for that sport in the load, including train and test — as-of still prevents leakage into each match’s own features).
4. After enrichment, sort for Elo/backtest chronological order (existing key).
5. Pop `kickoff_utc` only after enrichment if consumers still should not see it — **prefer keeping** `kickoff_utc` ISO string or datetime if harmless; current code strips it for “serialization”. Spec: **keep stripping** only if something breaks; otherwise retain ISO string for debugging. Default implement: strip datetime object as today, after features computed.

Do **not** use flat 2.0 / 0.5 fallbacks when history exists. Only form’s `default=0.5` applies for empty prior games.

Performance: single pass over ~3k–7k matches per sport; O(n²) naive per-team scan is acceptable at this scale. Optional optimization: per-team sorted index of past games (recommended if tests show slowness; not required for v1).

## 6. Adapter alignment (in-scope, same PR)

So live predictions and backtest share semantics:

1. Change rest unknown from **`0` → `None`** in all three adapters (return type becomes `int | float | None` as needed by callers).
2. Prefer implementing adapter methods as: query that team’s fixtures (as today) → map rows to MatchLike dicts → call `form_as_of` / `rest_days_as_of` with `before=kickoff`. Full rewrite to only shared batch path is not required if DB query shape stays efficient for single-match predict.
3. Form default stays **0.5** when no prior finished games.

**Engine behavior today:** rest available only if **both** `rest_days_home` and `rest_days_away` are non-None. Season-openers with one team missing history still mark rest unavailable — acceptable.

**Implement order (hard):**

1. shared module + unit tests  
2. match_loader wire-up + loader tests  
3. adapter rest `None` + helper reuse  
4. docs  

## 7. BacktestRunner

**No formula changes** this sprint. Existing:

- `None` rest/form → factor unavailable, weight redistributed  
- Non-None → same clamp formulas as today  

Optional later: B2B extra penalty parity with engines (out of scope).

## 8. Testing

| Test file | Cases |
|-----------|--------|
| `tests/test_rest_form.py` (new) | form L10 order; as-of excludes future; draw handling; rest day gap; no history → form 0.5 / rest None; exclude_match_id |
| `tests/test_match_loader.py` (extend) | with tmp kernel DB fixtures, assert not all rest==2.0; assert first games rest is None; form varies after a win streak |

Use existing pytest + tmp kernel DB patterns from Phase 9 tests.

## 9. Verification after implement

1. `pytest` for new/updated tests.  
2. Manual: load NBA matches, sample 20 rows — rest/form distributions not degenerate.  
3. Optional: `python scripts/run_phase9_optimize.py --sport nba --n-trials 10` smoke.  
4. Do **not** auto-apply new candidates; leave re-Optuna + human apply as follow-up.

## 10. Docs / backlog touch-ups

After implement:

- RUNBOOK Phase 9: note loader uses as-of rest/form (not flat).  
- OPPORTUNITY_BACKLOG P1-A3 follow-up: rest/form real features done; re-tune suggested.  
- CHANGELOG short entry.

## 11. Risks

| Risk | Mitigation |
|------|------------|
| Team rename / Athletics alias | Exact match only; MLB already canonicalized at ingest |
| Mid-season doubleheaders same day | `.days` may be 0 — correct “no rest day” signal |
| Adapter rest None changes live odds slightly | Document; desirable fix vs false B2B |
| Optuna scores shift after re-tune | Expected; re-apply only after review |

## 12. Implementation order

1. `rest_form.py` + unit tests  
2. Wire `match_loader`  
3. Adapter alignment (rest None + shared helpers)  
4. Docs  
5. Optional Optuna smoke (no apply)

## 13. Out of scope checklist (do not sneak in)

- [ ] pitcher / goalie  
- [ ] feature table migration  
- [ ] engine B2B / travel in BacktestRunner  
- [ ] auto monthly re-optimize apply  
