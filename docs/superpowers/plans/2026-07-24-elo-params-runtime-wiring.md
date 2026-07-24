# Elo HFA/K Runtime Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Applied Optuna `elo_params` drive online HFA and Elo seed K/carry, with apply re-seeding and kernel singleton reset.

**Architecture:** `resolve_elo_params(sport)` merges settings defaults with applied JSON. Engines use HFA; seed uses full dict; `apply` re-seeds and resets prediction kernel singleton.

**Tech Stack:** Python 3.12+, existing OptimizedParamsStore, FactorRegistry, pytest. Runner: `C:\Python314\python.exe`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-elo-params-runtime-wiring-design.md`
- Do not modify PredictionKernel / domain.py / fusion formulas
- Do not push to origin
- TDD where new modules
- Applied keys fill over settings; invalid keys skipped

## File Structure

### New
1. `backend/app/kernel/elo_params_resolve.py`
2. `backend/tests/test_elo_params_resolve.py`

### Modified
1. `backend/app/services/historical_data_ingestor.py` — `_elo_params_for_sport`
2. `backend/app/sports/basketball/engines/basketball_engine.py` — HFA
3. `backend/app/sports/baseball/engines/baseball_engine.py` — HFA
4. `backend/app/sports/hockey/engines/hockey_engine.py` — HFA
5. `backend/app/kernel/factor_registry.py` — `reload_from_db`
6. `backend/app/api/routes/predictions.py` — `reset_kernel_singleton`
7. `backend/app/kernel/optimized_params_store.py` — apply re-seed + reset
8. `backend/tests/test_optimized_params_store.py`
9. Docs: RUNBOOK, CHANGELOG, backlog

---

### Task 1: resolve_elo_params + tests

**Files:** create `elo_params_resolve.py`, `test_elo_params_resolve.py`

- [x] Implement settings + resolve (fill missing from settings)
- [x] Tests: fallback, applied override, bad JSON, partial keys
- [ ] Commit

### Task 2: seed + engines HFA

- [x] `_elo_params_for_sport` → `resolve_elo_params`
- [x] Three engines use resolve hfa (NBA playoff only when no applied)
- [ ] Commit

### Task 3: apply re-seed + singleton reset

- [x] `FactorRegistry.reload_from_db`
- [x] `reset_kernel_singleton` in predictions.py
- [x] apply: reseed + reset; `reseed_elo=True` kwarg for tests
- [x] Tests with mock seed
- [ ] Commit

### Task 4: re-seed applied sports + docs

- [x] seed nba/mlb/nhl with applied params
- [x] Docs
- [ ] Commit
