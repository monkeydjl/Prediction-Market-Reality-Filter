# Task 3 Report: Wire enrich overwrite + adapter tests (RED → GREEN)

## Status
DONE_WITH_CONCERNS

## Summary
Wired `enrich_situational_features` to dual-side static xG overwrite after goals proxy. Added `TestStaticXgOverwrite` and updated `test_enrich_form_and_h2h` assertions for Real Madrid CF / Bayern static values.

## Commits
- `855d7b6` feat(football): static team xG/90 table (P1-F5) (base)
- `0d4b30e` feat(football): static xG overwrite in enrich (P1-F5)

## Files changed
1. `backend/tests/test_adapter_shared.py`
   - Updated `test_enrich_form_and_h2h`: expect static `xg_home` / `xg_away` + `xg_source=static_table`
   - Added `TestStaticXgOverwrite`:
     - both static hits overwrite proxy
     - one side unknown keeps proxy (no `xg_source`)
     - both unknown: no xg keys / no source
2. `backend/app/sports/football/adapters/_shared.py`
   - After goals proxy (~337–354), before H2H: dual-side `xg_for_team` overwrite
   - Sets `custom.xg_home`, `xg_away`, `xg_source="static_table"` only when both resolve
   - Fail-closed: exception → debug log, leave existing fields

## TDD evidence
### RED
Command:
```
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestStaticXgOverwrite -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```
Result: **1 failed, 2 passed**
- `test_both_static_hits_overwrite_proxy` FAILED: `assert 1.1 == 2.1` (proxy remained; no static overwrite)
- partial / both-unknown already green (proxy/empty behavior)

### GREEN
Implemented enrich static overwrite block per plan verbatim.

Focused suite:
```
C:\Python314\python.exe -m pytest tests/test_football_xg.py tests/test_adapter_shared.py -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```
Result: **33 passed**

Smoke multi-factor xg (no engine edits):
```
C:\Python314\python.exe -m pytest tests/test_football_multi_factor_engine.py -k xg -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```
Result: **1 passed, 22 deselected** (`test_xg_soft_factor_favors_higher_attack`)

## Self-review
### Spec compliance
- [x] Adapter tests first (RED verified: proxy 1.1 remained)
- [x] Goals proxy writes retained as fallback
- [x] Dual-side only overwrite; no one-sided static
- [x] `xg_source=static_table` only on both-hit
- [x] Fail-closed exception path
- [x] `test_enrich_form_and_h2h` updated for static Real Madrid / Bayern
- [x] MultiFactor / FeatureBuilder not changed
- [x] Commit only the two planned files
- [x] No push to origin

### Code quality
- Minimal diff: enrich block + tests only
- Lazy import of `xg_for_team` mirrors other enrich helpers
- Exception handling matches form/H2H style

## Concerns
1. **task-3-brief.md was empty** (5 bytes); requirements taken from plan Task 3 in `docs/superpowers/plans/2026-07-26-football-static-xg.md`.
2. Windows pytest basetemp under `backend\.pytest_tmp_p1f5` used as specified; no cleanup PermissionError on this run.
3. MultiFactor soft xG path unchanged; true live xG API still pending (Task 4 docs).

## Out of scope (confirmed not touched)
- MultiFactor engine / FeatureBuilder
- `football_xg.py` table (Task 2)
- CHANGELOG / backlog (Task 4)

## Review fix (Important finding — injury dual-write custom assertions)

### Finding
`TestInjuryImpactEnrich.test_static_dual_writes_sample_teams` lost `custom.injury_impact_*` assertions (out of scope for P1-F5). Production still dual-writes player + custom in `adapters/_shared.py`.

### Fix
Restored in `backend/tests/test_adapter_shared.py`:
```python
assert raw["custom"]["injury_impact_home"] == pytest.approx(0.35)
assert raw["custom"]["injury_impact_away"] == pytest.approx(0.26)
```
No production injury logic changed.

### Verification
```
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestInjuryImpactEnrich tests/test_adapter_shared.py::TestStaticXgOverwrite -v --tb=short --basetemp="E:\Github\Prediction Market Reality Filter\backend\.pytest_tmp_p1f5"
```
Result: **7 passed** (4 InjuryImpactEnrich + 3 StaticXgOverwrite)

### Commit
`test(football): restore injury dual-write custom assertions`
