# Football Schedule Density (P1-F2) — Design

**Date:** 2026-07-25  
**Status:** Approved for planning  
**Backlog:** P1-F2 (partial → true N-day match-count density)

## Problem

`FootballMultiFactorEngine` already fuses a soft `rest` factor (weight ~0.05) with:

- rest-days differential (`rest_days_home` − `rest_days_away`)
- back-to-back soft penalty when `b2b_*` or `rest_days <= 1`
- midweek congestion soft penalty when `schedule_congested_*` or `rest_days <= 2`

Today the football adapter sets congestion flags **only from rest days**:

```text
b2b_*                 = rest_days <= 1
schedule_congested_*  = rest_days <= 2
```

That is a proxy, not true schedule density. A team with `rest_days = 3` can still have played twice in the last week (cup + league); a team with `rest_days = 2` may have only one prior match in a quiet window. Backlog P1-F2 explicitly leaves “完整赛程密度（N 天内场次）” open.

## Goals

1. Add a pure helper `matches_in_window_as_of` on `rest_form` (same as-of style as `rest_days_as_of` / `form_as_of`).
2. In football `enrich_situational_features`, inject:
   - `custom.matches_last_7d_home` / `matches_last_7d_away` when computable
   - `custom.schedule_congested_*` driven by **window count when available**, else rest ≤ 2 fallback
3. Keep `b2b_*` semantics unchanged (`rest_days <= 1`).
4. Leave `FootballMultiFactorEngine` weight table and rest edge coefficients **unchanged**; engine already prefers `custom.schedule_congested_*` over rest proxy.
5. No new network dependency, env flags, or DB schema.

## Non-goals

- New soft factor `schedule_density` or MultiFactor weight reshuffle
- Cross-sport (NBA/NHL) wiring this round (helper is reusable later)
- Live API-Football / fixture scrape
- Changing b2b threshold or rest edge magnitudes (±0.03 / ±0.015)
- World Cup legacy `schedule_density` string path in rule engine
- Normalize-vs-exact team name unification beyond a single documented strategy

## Approved approach

**Option A — window match count + reuse existing rest factor**

1. Extend `backend/app/sports/_shared/rest_form.py` with:

```python
def matches_in_window_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    window_days: int = 7,
    exclude_match_id: str | None = None,
) -> int | None:
    ...
```

2. In `enrich_situational_features` (`adapters/_shared.py`), after existing rest/form enrichment:
   - Build a lightweight history list for the competition from `KernelMatchFixture` (kickoff + teams; **do not require** `KernelMatchResult` scores).
   - Compute counts for home/away with `exclude_match_id=match.match_id`.
   - Write `matches_last_7d_*` when count is not `None`.
   - Set congestion flags:

| Condition | `schedule_congested_*` |
|-----------|------------------------|
| count is not None and count >= 2 | `True` |
| count is not None and count < 2 | `False` (even if rest ≤ 2) |
| count is None | fallback: `rest_days <= 2` when rest known; omit if rest unknown |

3. Keep `b2b_*` from rest only (`<= 1`).

4. Engine: no formula change. Optional explanation detail may mention `matches_last_7d` if present (nice-to-have; not required for acceptance).

### Rejected alternatives

| Option | Why not |
|--------|---------|
| B. Inject counts only, engine untouched beyond flags | Weaker than A only if flags not set; pure inject without congest rewrite leaves behavior rest-proxy-only |
| C. New `schedule_density` factor + weights | Scope creep; YAGNI while rest path already has congest soft edge |
| Count only finished matches | Under-counts fixture congestion (midweek cup still loads the squad) |

## Data model

### Window count

```python
# custom keys (int when known)
matches_last_7d_home: int
matches_last_7d_away: int
```

Semantics: number of prior matches (kickoff strictly before this match) for that team with  
`0 < (as_of - kickoff).total_seconds() <= window_days * 86400`  
(or calendar-day equivalent consistent with `rest_days_as_of` style — implement with timedelta `days` comparison: include history rows where `0 <= (as_of - k).days <= window_days` and `k < as_of`).

**Include unfinished fixtures** (score may be null).  
**Exclude** current `match_id`.  
**Team match:** exact `home_team` / `away_team` string equality after the same strip rules as `rest_days_as_of` (no lowercasing in the pure helper). Adapter must pass DB names as stored.

### Congestion flags (unchanged key names)

```python
schedule_congested_home: bool
schedule_congested_away: bool
b2b_home: bool
b2b_away: bool
```

### Thresholds (fixed this round)

| Signal | Rule |
|--------|------|
| Window | `window_days = 7` |
| Congested | `matches_last_7d >= 2` |
| B2B | `rest_days <= 1` (unchanged) |
| Rest-proxy congest fallback | `rest_days <= 2` only when window count is `None` |

## Component boundaries

| Unit | Responsibility |
|------|----------------|
| `rest_form.matches_in_window_as_of` | Pure count; no DB |
| Football `enrich_situational_features` | Load history, call helper, write custom |
| `FootballMultiFactorEngine` rest block | Consume flags/rest (existing) |
| FeatureBuilder | Passthrough custom (already) |

## Error handling

- History load failure → skip density inject; keep rest-based flags if rest exists; log debug.
- Unknown team / empty history → count `None` or 0 only when history iterable was successfully built and team string is non-empty:
  - empty team or missing kickoff → `None`
  - valid team + kickoff + history list (possibly empty of matches for that team in window) → integer ≥ 0
- Never invent league-average density.

## Testing

1. **Unit (`test_rest_form.py`)**  
   - empty kickoff → None  
   - two prior matches within 7 days → 2  
   - match exactly on boundary day included/excluded consistently  
   - future matches excluded  
   - `exclude_match_id` drops self  
   - scores null still counted  

2. **Enrich / adapter**  
   - known history → `matches_last_7d_*` + congest when ≥ 2  
   - count 0/1 → congest False even if rest ≤ 2  
   - no history load → rest ≤ 2 still sets congest (fallback)

3. **Engine**  
   - existing `test_rest_congestion_penalty` remains green  
   - optional: rested rest_days (e.g. 4) + `schedule_congested_home=True` lowers home_win vs same rest without flag  

## Acceptance criteria

- [ ] `matches_in_window_as_of` documented and unit-tested  
- [ ] Football enrich writes `matches_last_7d_*` when computable  
- [ ] `schedule_congested_*` uses count ≥ 2 when count known; rest ≤ 2 only as fallback  
- [ ] `b2b_*` still rest-based only  
- [ ] MultiFactor weights / rest edge coefficients unchanged  
- [ ] CHANGELOG + backlog P1-F2 row updated  

## Implementation notes

- Prefer reusing one fixture query for both teams (filter in Python by team) to avoid double full-table scans when possible; if existing club_form patterns scan all rows, matching that pattern is acceptable for this round.
- Do not change `prediction_kernel.py` / frozen engine contracts.
- TDD: RED → GREEN → COMMIT per plan tasks.
- Python runner: `C:\Python314\python.exe` with `PYTHONPATH` = repo `backend`.
- Do not push to origin (standing instruction).

## Spec self-review

- No TBD placeholders for thresholds or key names.  
- Scope is single plan-sized slice (helper + football enrich + tests + docs).  
- Ambiguity resolved: unfinished fixtures count; exact name match in helper; congest false when count known and &lt; 2 overrides rest ≤ 2.
