# LoL Esports Adapter (ADR-004) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire League of Legends into the Sports Prediction Kernel as sport `lol` / prefix `lol-`, behind `PHASE_LOL_ENABLED` (default OFF), with a dry-run import path and market-only engine — without inventing production fixtures or odds, and without registering a live vendor adapter until ADR-004 gates P1–P8 are documented.

**Architecture:** Parallel package `backend/app/sports/lol/` (same shape as `basketball/`). `LolAdapter` implements `DataAdapter` and reads **only** from local Kernel fixture tables + optional dry-run JSON import when `LOL_DRY_RUN_IMPORT=true`. A pluggable `LolScheduleSource` Protocol is defined for a future official/partner HTTP client, but **no production HTTP client is enabled** until Task 0 gates are checked off. `LolFeatureBuilder` + `LolMarketOnlyEngine` handle binary series winner (`home_win`/`away_win`). Registration mirrors NBA in `_get_kernel()`. Catalog/FE gain an optional `lol` card and `sport=lol` filter support while `esports` remains the umbrella coming_soon entry until flag is intentionally ON in a given environment.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy Kernel tables (`kernel_match_fixtures` / `kernel_match_results`), pytest, Next.js/TypeScript catalog helpers, existing MultiAdapter / MultiFeatureBuilder

## Global Constraints

1. Follow [ADR-004](../adr/004-esports-data-adapter.md) and [ESPORTS_BOUNDARY](../../dev/ESPORTS_BOUNDARY.md) — **no fake markets**, no auto-betting.
2. `PHASE_LOL_ENABLED` defaults **false**; when false, no `lol-` adapter registration; `lol-*` match_ids behave like unregistered prefixes.
3. `LOL_DRY_RUN_IMPORT` defaults **false**; when true, only loads fixtures from a **repo-local fixture file** under `backend/tests/fixtures/lol/` or path in `LOL_DRY_RUN_FIXTURES_PATH` — never scrapes the open web as settlement truth.
4. Do **not** implement a production HTTP client that hits a real vendor URL until Task 0 checklist file marks P2/P3/P6 complete with real endpoint docs (file may exist empty of secrets).
5. Do **not** reuse `EloOddsEngine`, `FootballMultiFactorEngine`, or `BasketballEngine` weights for LoL.
6. v1 market: **series winner only** (binary). Map handicap / totals = out of scope (document only).
7. match_id format: `lol-{external_series_id}` where `external_series_id` is alphanumeric + hyphen, e.g. `lol-lck-2026-s1-001`.
8. Reuse `kernel_match_fixtures` / `kernel_match_results` with `competition` values like `lol_lck` (underscore leagues from ADR examples); sport on fixture/metadata via existing columns or `raw_json`.
9. PredictionKernel core class: **zero modification** unless a Protocol gap is proven; prefer adapter/engine registration only.
10. World Cup `/api/world-cup/*` unchanged.
11. All new tests under `backend/tests/`; FE tests under existing vitest layout.
12. Secrets: never commit API keys; only empty placeholders in `.env.example`.
13. Catalog: keep `id=esports` placeholder; add `id=lol` kernel card that stays `coming_soon` until `PHASE_LOL_ENABLED` would make `adapter_likely` true (still may have zero fixtures).
14. Sport filter lists: frontend `SPORT_CODES` and backend sport filters must include `lol` when listing multi-sport chips (optional chip only if flag-aware or always show as future — prefer **always allow query param** `sport=lol`, empty list when disabled).
15. Subagent-driven or inline execution; commit after each task.

---

## File Structure

### New Files

| File | Responsibility | Task |
|------|----------------|------|
| `docs/dev/lol/GATES.md` | Living P1–P8 checklist with owners/dates | 0 |
| `backend/app/sports/lol/__init__.py` | Package marker | 2 |
| `backend/app/sports/lol/source.py` | `LolScheduleSource` Protocol + `NullLolScheduleSource` | 2 |
| `backend/app/sports/lol/dry_run_import.py` | Load series JSON → fixture rows | 3 |
| `backend/app/sports/lol/lol_adapter.py` | `LolAdapter` DataAdapter | 4 |
| `backend/app/sports/lol/feature_builder.py` | `LolFeatureBuilder` | 5 |
| `backend/app/sports/lol/engines/__init__.py` | Engines package | 6 |
| `backend/app/sports/lol/engines/market_only_engine.py` | `LolMarketOnlyEngine` | 6 |
| `backend/tests/fixtures/lol/sample_series.json` | Synthetic dry-run series (labeled) | 3 |
| `backend/tests/test_lol_dry_run_import.py` | Import tests | 3 |
| `backend/tests/test_lol_adapter.py` | Adapter contract tests | 4 |
| `backend/tests/test_lol_feature_builder.py` | Feature builder tests | 5 |
| `backend/tests/test_lol_market_only_engine.py` | Engine tests | 6 |
| `backend/tests/test_lol_kernel_registration.py` | Flag off/on registration | 7 |

### Modified Files

| File | Change | Task |
|------|--------|------|
| `backend/app/core/config.py` | `PHASE_LOL_ENABLED`, `LOL_DRY_RUN_IMPORT`, `LOL_DRY_RUN_FIXTURES_PATH` | 1 |
| `backend/.env.example` | Document LoL flags | 1 |
| `backend/app/kernel/competition_codes.py` | `lol` aliases, `lol-` prefix maps | 1 |
| `backend/app/kernel/betting_catalog.py` | `lol` competition row + `phase_lol_enabled` in flags / `adapter_likely` | 8 |
| `backend/app/api/routes/predictions.py` | Register LoL when flag ON; MultiFeatureBuilder include | 7 |
| `docs/ops/RUNBOOK.md` | Betting / LoL section | 9 |
| `frontend/src/lib/betting/competition-catalog.ts` | `lol` entry + normalize aliases | 8 |
| `frontend/src/app/sports/page.tsx` | Allow `sport=lol` in `SPORT_CODES` | 8 |
| `frontend/src/lib/sports-api/hooks/use-betting-catalog.ts` | Optional flag type for `phase_lol_enabled` | 8 |
| `docs/dev/ESPORTS_BOUNDARY.md` | Link plan path | 9 |
| `CHANGELOG.md` | Unreleased notes per commits | each |

---

## Task Dependency Graph

```
Task 0 (Gates doc) ── required before enabling production source; can parallel Tasks 1–6 for dry-run stack
Task 1 (Config + codes)
Task 2 (Package + Source Protocol)
Task 3 (Dry-run import) ── needs Task 1–2
Task 4 (LolAdapter) ── needs Task 2–3
Task 5 (FeatureBuilder) ── needs Task 2
Task 6 (MarketOnlyEngine) ── needs Task 5 types
Task 7 (Kernel registration) ── needs 1,4,5,6
Task 8 (Catalog + FE) ── needs 1,7 flags
Task 9 (RUNBOOK + boundary) ── needs 7–8
```

**Production HTTP vendor client is intentionally OUT of this plan** until Task 0 is fully checked. A later plan or Task 10+ can add `RiotPartnerClient` implementing `LolScheduleSource`.

---

### Task 0: Document ADR gates (blocking production source)

**Files:**
- Create: `docs/dev/lol/GATES.md`
- Modify: none required for code

**Interfaces:**
- Produces: human checklist only; implementers of Tasks 1–9 may proceed with dry-run path regardless
- Blocks: any future PR that sets a real `LOL_API_BASE_URL` + enables network fetch

- [ ] **Step 1: Create gates file**

```markdown
# LoL integration gates (ADR-004 P1–P8)

Status legend: `[ ]` open · `[x]` done

| Gate | Description | Status | Notes (no secrets) |
|------|-------------|--------|--------------------|
| P1 | v1 leagues in scope | [ ] | e.g. LCK, LPL, LEC, Worlds — list here when decided |
| P2 | Schedule source docs | [ ] | endpoint list, auth method name, rate limit, timezone |
| P3 | Result/settlement source | [ ] | same as P2 or conflict policy |
| P4 | Team identity map rules | [ ] | external_id → display name → market slug |
| P5 | Markets v1 = series winner only | [x] | ADR-004 locked |
| P6 | ToS / license OK for cache+display | [ ] | legal owner sign-off date |
| P7 | Empty-state UX copy | [ ] | FE strings reviewed |
| P8 | Contract tests + dry-run settle sample | [ ] | filled when Tasks 3–6 land |

**Rule:** Do not merge production HTTP schedule client until P2, P3, P6 are `[x]`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/dev/lol/GATES.md
git commit -m "docs(lol): add ADR-004 P1-P8 gates checklist"
```

---

### Task 1: Config + competition codes

**Files:**
- Modify: `backend/app/core/config.py` (near other `PHASE*_ENABLED` flags, ~line 1063)
- Modify: `backend/.env.example` (append LoL section after NBA/MLB/NHL)
- Modify: `backend/app/kernel/competition_codes.py`
- Test: `backend/tests/test_lol_config_codes.py` (new)

**Interfaces:**
- Produces: `settings.PHASE_LOL_ENABLED: bool`, `settings.LOL_DRY_RUN_IMPORT: bool`, `settings.LOL_DRY_RUN_FIXTURES_PATH: str`
- Produces: `PREFIX_TO_SPORT["lol-"] == "lol"`, `COMPETITION_SPORT["lol_lck"] == "lol"`, aliases `lol` → `lol`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_lol_config_codes.py
from app.core import config
from app.kernel.competition_codes import (
    COMPETITION_SPORT,
    PREFIX_TO_COMPETITION,
    PREFIX_TO_SPORT,
    normalize_competition_code,
)


def test_phase_lol_defaults_off():
    assert config.settings.PHASE_LOL_ENABLED is False
    assert config.settings.LOL_DRY_RUN_IMPORT is False


def test_lol_prefix_and_aliases():
    assert PREFIX_TO_SPORT["lol-"] == "lol"
    assert PREFIX_TO_COMPETITION["lol-"] == "lol"
    assert COMPETITION_SPORT["lol"] == "lol"
    assert COMPETITION_SPORT["lol_lck"] == "lol"
    assert normalize_competition_code("LOL") == "lol"
    assert normalize_competition_code("lol-lck") == "lol_lck"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd backend
set PYTHONPATH=.
python -m pytest tests/test_lol_config_codes.py -q
```

Expected: FAIL (settings attrs / keys missing)

- [ ] **Step 3: Implement config fields**

In `config.py` (same pattern as `PHASE4_NBA_ENABLED`):

```python
PHASE_LOL_ENABLED: bool = _env_bool("PHASE_LOL_ENABLED", "false")
LOL_DRY_RUN_IMPORT: bool = _env_bool("LOL_DRY_RUN_IMPORT", "false")
LOL_DRY_RUN_FIXTURES_PATH: str = os.getenv(
    "LOL_DRY_RUN_FIXTURES_PATH",
    "",
).strip()
```

`.env.example`:

```bash
# LoL esports (ADR-004) — default OFF; no production API until docs/dev/lol/GATES.md P2/P3/P6
PHASE_LOL_ENABLED=false
LOL_DRY_RUN_IMPORT=false
# LOL_DRY_RUN_FIXTURES_PATH=  # optional absolute/relative path to series JSON
```

In `competition_codes.py` add:

```python
# COMPETITION_ALIASES
"lol": "lol",
"lol_lck": "lol_lck",
"lol_lpl": "lol_lpl",
"lol_lec": "lol_lec",
"lol_worlds": "lol_worlds",

# PREFIX_TO_COMPETITION
"lol-": "lol",

# PREFIX_TO_SPORT
"lol-": "lol",

# COMPETITION_SPORT
"lol": "lol",
"lol_lck": "lol",
"lol_lpl": "lol",
"lol_lec": "lol",
"lol_worlds": "lol",
```

Ensure `normalize_competition_code("lol-lck")` maps hyphen→underscore then alias (existing logic already replaces `-` with `_`).

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_lol_config_codes.py -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/.env.example backend/app/kernel/competition_codes.py backend/tests/test_lol_config_codes.py
git commit -m "feat(lol): PHASE_LOL_ENABLED config and competition codes"
```

---

### Task 2: Package + LolScheduleSource Protocol

**Files:**
- Create: `backend/app/sports/lol/__init__.py`
- Create: `backend/app/sports/lol/source.py`
- Test: `backend/tests/test_lol_source_protocol.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class LolSeriesRecord:
    external_id: str          # without lol- prefix
    competition: str          # e.g. lol_lck
    home_name: str
    away_name: str
    home_code: str
    away_code: str
    kickoff_utc: datetime
    best_of: int              # 1, 3, or 5
    stage: str                # regular / playoffs / worlds
    status: str               # scheduled / live / finished

class LolScheduleSource(Protocol):
    def list_upcoming(self) -> list[LolSeriesRecord]: ...
    def get_result(self, external_id: str) -> dict | None: ...
    # result dict keys: home_score, away_score, winner ("home"|"away"), source

class NullLolScheduleSource:
    def list_upcoming(self) -> list[LolSeriesRecord]:
        return []
    def get_result(self, external_id: str) -> dict | None:
        return None
```

- [ ] **Step 1: Write failing test**

```python
from app.sports.lol.source import NullLolScheduleSource, LolSeriesRecord

def test_null_source_empty():
    src = NullLolScheduleSource()
    assert src.list_upcoming() == []
    assert src.get_result("x") is None
```

- [ ] **Step 2: Implement `source.py` + empty `__init__.py`**

- [ ] **Step 3: pytest pass + commit**

```bash
git add backend/app/sports/lol backend/tests/test_lol_source_protocol.py
git commit -m "feat(lol): schedule source protocol and null implementation"
```

---

### Task 3: Dry-run JSON import

**Files:**
- Create: `backend/app/sports/lol/dry_run_import.py`
- Create: `backend/tests/fixtures/lol/sample_series.json`
- Test: `backend/tests/test_lol_dry_run_import.py`

**Interfaces:**
- Produces: `import_lol_series_file(path: str | Path) -> int` — number of fixtures upserted
- Consumes: Kernel fixture write helpers used by NBA/MLB (locate `upsert` / `KernelMatchFixture` patterns in `nba_adapter.sync_schedule`)

Sample JSON (`backend/tests/fixtures/lol/sample_series.json`):

```json
{
  "label": "SYNTHETIC dry-run only — not real matches",
  "series": [
    {
      "external_id": "dry-lck-001",
      "competition": "lol_lck",
      "home_name": "T1",
      "away_name": "Gen.G",
      "home_code": "T1",
      "away_code": "GEN",
      "kickoff_utc": "2099-01-15T10:00:00Z",
      "best_of": 3,
      "stage": "regular",
      "status": "scheduled"
    }
  ]
}
```

Use **2099** kickoff so “today” lists stay empty in normal ops; adapter tests can filter by competition without depending on clock.

- [ ] **Step 1: Write failing test** that calls `import_lol_series_file` on sample path and asserts fixture row `match_id == "lol-dry-lck-001"` exists (use temp Kernel DB / existing test session fixture pattern from `test_nba_adapter.py`).

- [ ] **Step 2: Implement import** writing:
  - `match_id = f"lol-{external_id}"`
  - `competition` from JSON
  - team names
  - store `best_of`, `status` in fixture metadata/raw if column exists; else pack into a JSON column used by other sports

- [ ] **Step 3: pytest + commit**

```bash
git commit -m "feat(lol): dry-run series JSON import into kernel fixtures"
```

---

### Task 4: LolAdapter

**Files:**
- Create: `backend/app/sports/lol/lol_adapter.py`
- Test: `backend/tests/test_lol_adapter.py`

**Interfaces:**
- Produces: class `LolAdapter` implementing DataAdapter methods used by Kernel:
  - `fetch_schedule(filters) -> list[RawMatchData]`
  - `sync_schedule() -> int`
  - `get_match_identity(match_id) -> MatchIdentity`
  - `fetch_all_data(match) -> dict`
  - `fetch_outcome(match_id) -> MatchOutcome | None`
  - stubs for `fetch_team_data` / `fetch_player_data` / `fetch_market_data` returning `{}` or odds dict from raw if present
- Set `self._competition` for MultiAdapter short-circuit:

```python
from app.kernel.domain import CompetitionIdentity, SportIdentity
_LOL = SportIdentity(code="lol", name="League of Legends")
# competition code on adapter for default filter: "lol"
```

**sync_schedule behavior:**
1. If `settings.LOL_DRY_RUN_IMPORT` and path resolves → `import_lol_series_file` + return count
2. Else call `self._source.list_upcoming()` (default `NullLolScheduleSource`) and upsert; production source remains null → return 0
3. Never invent rows without source/import

**fetch_schedule:** query local fixtures with prefix `lol-` and optional competition filter.

- [ ] **Step 1: Failing contract tests** (mirror `test_nba_adapter` style):
  - empty schedule when no fixtures
  - after dry-run import, `fetch_schedule` returns 1 with `match_id` prefix `lol-`
  - `get_match_identity("lol-dry-lck-001")` returns team names from fixture
  - `sync_schedule` with dry-run off + null source returns 0

- [ ] **Step 2: Implement adapter**

- [ ] **Step 3: pytest + commit**

```bash
git commit -m "feat(lol): LolAdapter with dry-run and null schedule source"
```

---

### Task 5: LolFeatureBuilder

**Files:**
- Create: `backend/app/sports/lol/feature_builder.py`
- Test: `backend/tests/test_lol_feature_builder.py`

**Interfaces:**
- Produces:

```python
class LolFeatureBuilder:
    feature_version = "lol-market-0.1"
    def sport(self) -> SportIdentity: ...
    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet: ...
```

`FeatureSet` mapping (binary sport — follow basketball pattern):
- `custom["best_of"]` from raw
- `custom["series_format"]` = f"Bo{best_of}"
- market probs if present: `custom["mkt_home"]`, `custom["mkt_away"]` floats in (0,1)
- `data_quality`: `"partial"` if no market probs; `"real"` if both mkt probs present
- **No** football xG / Elo fields required

- [ ] **Step 1–4: TDD + commit**

```bash
git commit -m "feat(lol): LolFeatureBuilder for series market features"
```

---

### Task 6: LolMarketOnlyEngine

**Files:**
- Create: `backend/app/sports/lol/engines/__init__.py`
- Create: `backend/app/sports/lol/engines/market_only_engine.py`
- Test: `backend/tests/test_lol_market_only_engine.py`

**Interfaces:**
- Produces:

```python
class LolMarketOnlyEngine:
    def name(self) -> str:
        return "lol_market_only"
    def supported_sports(self) -> list[str]:
        return ["lol"]
    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult: ...
```

**Predict rules:**
1. Read `mkt_home` / `mkt_away` from `features.custom`; if missing, use 0.5 / 0.5 and confidence ≤ 0.2
2. Renormalize to sum 1.0
3. `outcome_probabilities = {"home_win": p_h, "away_win": p_a}` — **no draw**
4. `predicted_scores` may be empty dict or Bo series placeholder — do not invent map scores
5. explanation: one `ContributionItem` source=`market` describing “series moneyline only”

- [ ] **Step 1: Test** equal market → ~0.5/0.5; skewed market 0.7/0.3 preserved after norm

- [ ] **Step 2: Implement**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(lol): LolMarketOnlyEngine binary series winner"
```

---

### Task 7: Kernel registration in predictions routes

**Files:**
- Modify: `backend/app/api/routes/predictions.py` `_get_kernel()`
- Test: `backend/tests/test_lol_kernel_registration.py`

**Interfaces:**
- When `PHASE_LOL_ENABLED`:
  - `adapters["lol-"] = LolAdapter()`
  - `reg.register(LolMarketOnlyEngine(...))` if constructor needs registry, match BasketballEngine pattern
  - `builders["lol-"] = LolFeatureBuilder()`
  - `factor_registry.ensure_competition_factors("lol")` only if learning requires factors; for market-only, pass empty or single `market` factor — follow `ensure_competition_factors` existing API
  - Extend MultiFeatureBuilder condition:

```python
if (PHASE4_NBA_ENABLED or PHASE5_MLB_ENABLED or PHASE5_NHL_ENABLED or PHASE_LOL_ENABLED):
    feature_builder = MultiFeatureBuilder(builders)
```

- Optionally call dry-run import once at startup if `LOL_DRY_RUN_IMPORT` (prefer **not** at import time — only inside `LolAdapter.sync_schedule` to avoid side effects)

- [ ] **Step 1: Test with monkeypatch settings**
  - flag off → `registered_prefixes` no `lol-` (via MultiAdapter or betting status)
  - flag on → prefix present; `list_engines` includes `lol_market_only`

Use existing TestClient + patch pattern from `test_api_predictions.py`.

- [ ] **Step 2: Implement registration**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(lol): register LolAdapter and engine when PHASE_LOL_ENABLED"
```

---

### Task 8: Betting catalog + frontend sport

**Files:**
- Modify: `backend/app/kernel/betting_catalog.py`
- Modify: `frontend/src/lib/betting/competition-catalog.ts`
- Modify: `frontend/src/lib/betting/competition-catalog.test.ts`
- Modify: `frontend/src/app/sports/page.tsx` (`SPORT_CODES` add `"lol"`)
- Modify: `frontend/src/lib/sports-api/hooks/use-betting-catalog.ts` flags type
- Test: extend `backend/tests/test_betting_catalog_api.py`

**Catalog BE row** (append to `BETTING_COMPETITIONS`):

```python
{
    "id": "lol",
    "sport": "lol",
    "label": "英雄联盟",
    "short_label": "LoL",
    "description": "Kernel sport=lol；默认关闭，需 PHASE_LOL_ENABLED 与数据门禁",
    "status": "coming_soon",  # flip to "kernel" only when you also ship UX ready; recommend keep coming_soon until P7
    "href": "/sports/betting/lol",
    "competition_code": "lol",
    "kernel_sport": "lol",
    "track": "placeholder",  # change to "kernel" when status is kernel
    "section": "esports",
}
```

**adapter_likely** in `build_catalog_payload`:

```python
elif code == "lol" and flags.get("phase_lol_enabled"):
    item["adapter_likely"] = True
```

Add `phase_lol_enabled` to `_kernel_flags()`.

**FE static catalog:** mirror fields; `status: "coming_soon"`, section `esports`.

**normalizeCompetitionCode:** `lol_lck` etc. already via underscore.

- [ ] **Step 1: Tests** — catalog includes `lol`; flags key present; FE unit test for `getCompetitionById("lol")`

- [ ] **Step 2: Implement**

- [ ] **Step 3: `npm run typecheck` + vitest catalog + pytest catalog**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(lol): catalog entry and sport=lol filter support"
```

---

### Task 9: RUNBOOK + boundary + smoke

**Files:**
- Modify: `docs/ops/RUNBOOK.md` (after Betting / 联赛赛程)
- Modify: `docs/dev/ESPORTS_BOUNDARY.md` (link this plan)
- Modify: `CHANGELOG.md`
- Optionally: `backend/scripts/verify_local_stack.py` note — no new required route

**RUNBOOK section content:**

```markdown
### LoL esports (ADR-004)

- Flag: `PHASE_LOL_ENABLED=false` by default.
- Dry-run: `LOL_DRY_RUN_IMPORT=true` + path to series JSON; then
  `POST /api/predictions/schedule/sync?sport=lol` with write key.
- List: `GET /api/predictions/matches?sport=lol`
- Do not enable production vendor HTTP until `docs/dev/lol/GATES.md` P2/P3/P6 are checked.
- Engine: `lol_market_only` (series winner only).
```

- [ ] **Step 1: Write docs**

- [ ] **Step 2: Commit**

```bash
git commit -m "docs(lol): RUNBOOK and boundary for PHASE_LOL dry-run path"
```

---

## Out of scope (explicit)

| Item | Why |
|------|-----|
| Live Riot/partner HTTP client | Blocked on GATES P2/P3/P6 |
| Map winner / handicap markets | ADR v2+ |
| CS2 / Dota packages | Per-title future ADRs |
| Learning weight tuning for LoL | Needs real outcomes volume |
| Phase 7 market bridge for LoL slugs | After identity stable |
| Auto-enable flag in production | Operator decision |

---

## Spec coverage self-review

| ADR-004 requirement | Task |
|---------------------|------|
| D1 LoL only | All tasks |
| D2 sport=lol prefix lol- | 1, 4, 7 |
| D3 API gate / no fake prod source | 0, 2 Null source, 4 |
| D4 Adapter + series match + market-only engine | 4–6 |
| D4 Catalog/UI evolution | 8 |
| Dry-run import allowed | 3 |
| P1–P8 checklist | 0 (+ P8 tests 3–6) |
| Implementation order in ADR | Tasks 0→9 match |
| No skeleton without flag | 7 registration gated |

**Placeholder scan:** no TBD steps; vendor client deferred with named future path.  
**Type consistency:** `LolSeriesRecord`, `LolAdapter`, `lol_market_only`, `PHASE_LOL_ENABLED` used consistently.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-22-lol-esports-adapter.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — this session with executing-plans checkpoints  

**Which approach?** (Do not start coding until you pick one. Task 0 is docs-only and safe to run first either way.)
