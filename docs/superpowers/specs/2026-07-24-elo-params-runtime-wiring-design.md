# Elo HFA/K Runtime Wiring (Phase 9 follow-up)

> **Status:** Design draft 2026-07-24 (awaiting user review)  
> **Approach:** A — shared `resolve_elo_params` (applied first, settings fallback)  
> **Predecessor:** Optuna apply NBA5/MLB6/NHL7; rest/form as-of enrichment

## 1. Goal

Make **applied** `kernel_optimized_params.elo_params` affect online prediction and Elo ratings the same way Optuna backtest does.

Success criteria:

1. `BasketballEngine` / `BaseballEngine` / `HockeyEngine` use **applied `hfa`** when an applied row exists for that sport; otherwise settings defaults (NBA playoff branch preserved only when no applied).
2. `HistoricalDataIngestor.seed_elo_ratings` / `_elo_params_for_sport` use applied **`k_regular` / `k_playoff` / `season_carry` / `initial`** when present.
3. `OptimizedParamsStore.apply` after writing factor weights:
   - refreshes in-process FactorRegistry (or resets kernel singleton) so weights take effect without relying on “remember to restart”;
   - triggers `seed_elo_ratings(sport=...)` so K/carry rewrite `kernel_elo_ratings`.
4. Unit tests cover resolve fallback, applied override, and apply side-effects (seed called; registry reload/singleton reset).
5. After ship: re-seed (or re-apply) NBA/MLB/NHL so current applied ids 5/6/7 actually update ratings.

Non-goals:

- Writing env files or mutating process-wide settings as the primary path  
- Changing Optuna search space (NBA still single `hfa`)  
- Auto monthly re-optimize / auto-apply  
- Modifying `PredictionKernel` / `domain.py` / engine fusion formulas  
- Wiring football ClubElo HFA/K  

## 2. Problem

| Path | factor weights | elo HFA | K / season_carry |
|------|----------------|---------|------------------|
| Optuna `BacktestRunner` | candidate | candidate | candidate (EloTimeMachine) |
| `apply()` today | → FactorRegistry/DB | **ignored** | **ignored** |
| Online engines | registry (if process reloaded) | **settings only** | n/a at predict |
| Elo seed | n/a | settings | **settings only** |

Also: `apply()` constructs a **new** `FactorRegistry` for `update_weight`, so the kernel singleton’s in-memory registry may stay stale until API restart.

## 3. Architecture

```
kernel_optimized_params (status=applied)
        │
        ▼
kernel/elo_params_resolve.py     ← new pure-ish resolver
   resolve_elo_params(sport)
        │
        ├──────────────────┬─────────────────────┐
        ▼                  ▼                     ▼
  engines.predict     _elo_params_for_sport   (tests)
  (hfa only)          seed_elo_ratings
                      (full dict)
        ▲
        │
apply() ──► update weights + reload/reset registry
         └──► seed_elo_ratings(sport)
```

**Zero-invasion:** no changes to `PredictionKernel`, `domain.py`, or fusion math—only HFA source and seed params.

## 4. API: `resolve_elo_params`

**New file:** `backend/app/kernel/elo_params_resolve.py`

```python
def settings_elo_params(sport: str) -> dict[str, float]:
    """Baseline from config.settings (same defaults as _elo_params_for_sport today)."""

def resolve_elo_params(sport: str) -> dict[str, float]:
    """Return {hfa, k_regular, k_playoff, season_carry, initial}.

    Prefer OptimizedParamsStore.get_applied(sport, sport).elo_params JSON.
    On missing applied, missing keys, or any error: settings_elo_params(sport).
    Coerce values to float; ignore non-dict / invalid JSON.
    """
```

Sport codes: `nba` | `mlb` | `nhl` (same as Optuna / store).

### 4.1 NBA playoff HFA

| Applied row? | Regular | Playoff |
|--------------|---------|---------|
| No | `NBA_ELO_HFA` | `NBA_ELO_HFA_PLAYOFF` |
| Yes | `applied.hfa` | **`applied.hfa`** (single HFA; matches Optuna/backtest) |

Document this explicitly so playoff soft-settings are not assumed after apply.

## 5. Engine changes

Files:

- `backend/app/sports/basketball/engines/basketball_engine.py`
- `backend/app/sports/baseball/engines/baseball_engine.py`
- `backend/app/sports/hockey/engines/hockey_engine.py`

Pattern:

```python
from app.kernel.elo_params_resolve import resolve_elo_params

params = resolve_elo_params("nba")  # or mlb / nhl
# NBA only: if no applied, keep playoff settings branch — implement via
# resolve helper or thin local: use resolve hfa always when applied exists.
hfa = params["hfa"]  # for MLB/NHL always; for NBA see §4.1
```

Recommended helper for NBA:

```python
def resolve_nba_hfa(*, playoff: bool) -> float:
    applied = ...  # or resolve_elo_params + flag
```

Minimal implementation acceptable:

- `resolve_elo_params` returns applied or settings **regular** defaults only.
- Basketball engine: if applied exists → `params["hfa"]`; else playoff ? `NBA_ELO_HFA_PLAYOFF` : `NBA_ELO_HFA`.

Detection of “applied exists”: `get_applied("nba","nba") is not None` (one extra call is fine; can cache later).

League avg totals remain settings-driven (not in Optuna `elo_params`).

## 6. Seed path

**File:** `backend/app/services/historical_data_ingestor.py`

- `_elo_params_for_sport(sport)` delegates to `resolve_elo_params(sport)` (or becomes a thin alias).
- `seed_elo_ratings` unchanged call sites; automatically picks up applied K/carry.

## 7. Apply path

**File:** `backend/app/kernel/optimized_params_store.py` — `apply(params_id)`

After successful weight updates and commit:

1. **Registry freshness (pick one, implement both if cheap):**
   - Add `FactorRegistry.reload_from_db()` clearing `_factors` then `_load_from_db()`.
   - Add `app.api.routes.predictions.reset_kernel_singleton()` that deletes `_get_kernel._instance` if present.
   - `apply` calls `reset_kernel_singleton()` so next HTTP request rebuilds kernel + registry from DB. Avoids needing a live reference to the kernel’s registry inside the store.
2. **Elo re-seed:**
   ```python
   from app.services.historical_data_ingestor import HistoricalDataIngestor
   seed_result = HistoricalDataIngestor().seed_elo_ratings(sport=target.sport)
   ```
   Catch exceptions → `elo_seed = {"ok": False, "error": str(e)}`; do **not** roll back applied status/weights.
3. Response dict adds:
   - `elo_params`: parsed dict (or None)
   - `elo_seed`: `{ok, teams?, sports?, errors?}` or error payload

Optional: `apply(..., *, reseed_elo: bool = True)` for tests that skip seed.

## 8. Post-deploy for already-applied 5/6/7

Implementation alone does not rewrite ratings until seed runs. After code lands:

1. Call `seed_elo_ratings` for nba/mlb/nhl (script or re-apply 5/6/7), **or**
2. One-shot CLI in RUNBOOK.

Prefer re-apply or explicit seed so operators see the same path as future applies.

## 9. Testing

| File | Cases |
|------|--------|
| `tests/test_elo_params_resolve.py` (new) | settings fallback; applied override; bad JSON → fallback; partial keys merge or full fallback (choose **full fallback to settings for missing required keys**, or fill missing from settings—prefer **fill missing keys from settings**, keep present applied keys) |
| `tests/test_optimized_params_store.py` | apply with mock seed; assert seed called with sport; elo_seed in result |
| Engine tests (extend or small new) | monkeypatch resolve / store → hfa used in contribution or via patch of resolve |

**Key resolution rule (explicit):** start from `settings_elo_params(sport)`, update with valid numeric keys from applied JSON. Invalid keys skipped. Empty applied elo_params → pure settings.

## 10. Docs

- RUNBOOK Phase 9 apply: note elo_params now drive HFA + re-seed K/carry; restart less critical for weights if singleton reset, still fine to restart.
- CHANGELOG short entry.
- OPPORTUNITY_BACKLOG P1-A4: Elo HFA/K 已接线.

## 11. Risks

| Risk | Mitigation |
|------|------------|
| Re-seed overwrites all team Elo for sport | Expected; same as manual seed; document duration |
| Apply latency (seconds) | Sync OK for admin endpoint; return seed summary |
| NBA playoff HFA loss after apply | Documented; Optuna parity |
| Circular imports store ↔ predictions | reset_kernel via lazy import inside apply |
| resolve on every predict hits DB | Accept for v1; optional short TTL cache later |

## 12. Implementation order

1. `elo_params_resolve.py` + unit tests  
2. Wire `_elo_params_for_sport`  
3. Wire three engines HFA  
4. `FactorRegistry.reload_from_db` + `reset_kernel_singleton` + apply re-seed  
5. Docs + re-seed applied sports  

## 13. Out of scope checklist

- [ ] env/settings mutation as primary  
- [ ] PredictionKernel protocol change  
- [ ] Auto Optuna schedule  
- [ ] Football Elo wiring  
