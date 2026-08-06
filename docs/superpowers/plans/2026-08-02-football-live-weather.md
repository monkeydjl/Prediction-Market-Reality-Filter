# Football Live Weather Forecast Fill (P1-F7 residual) Implementation Plan

> **Retroactive record.** This plan documents work that shipped in `bd89fd5` (2026-08-02) without a written plan. Boxes are checked because the steps are done; it is preserved so the P1-F7 weather slice has the same spec+plan pairing as its sibling artifacts, and so the next weather round starts from a written baseline rather than from source archaeology.

**Goal:** Fill match-day weather from an optional, configurable forecast provider when one is configured and the fixture is near kickoff, falling back silently to the existing static climate prior in every other case.

**Architecture:** `live_weather_for_match(match)` in `football_weather.py` owns the gates, the HTTP call, the TTL cache, and the WMO→vocabulary normalization. `enrich_weather_features` gains one branch between the explicit-data pass-through and the static climate fill. MultiFactor stays untouched.

**Tech Stack:** Python 3.11+, `httpx` (already a runtime dep), pytest. No new dependencies. No DB. Five new optional config keys.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-football-live-weather-design.md`
- **Off by default:** empty `FOOTBALL_LIVE_WEATHER_URL` → zero HTTP, behaviour identical to pre-change
- Fill order: env explicit (zero-safe) → `live_forecast` → `static_climate`
- Explicit fixture weather is authoritative; `0.0 °C` must beat both fill paths (`is not None`, never truthiness)
- `live_weather_for_match` **never raises** — every failure path returns `None`
- Return keys are the adapter's own: `weather_temp_c`, `weather_condition`
- temp clamp **[-15.0, 45.0]**, `round(1)`; condition ∈ `{clear, mild, rain, cold, hot}`
- Source string on live hit: exactly `live_forecast`
- All settings read via `getattr(settings, KEY, default)`
- Cache is in-memory only, keyed `(round(lat,2), round(lon,2), kickoff date)`, expiry via `time.monotonic()`
- Tests patch `app.sports.football.football_weather.settings.*` — never the real environment, never a real network call
- Do **not** add a MultiFactor weather factor or change weights
- Do **not** push to origin unless the user explicitly asks
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Users\Alin\AppData\Local\Programs\Python\Python311\python.exe`, run from `backend/`

## File Structure

### Modified files
1. `backend/app/core/config.py` — five `FOOTBALL_LIVE_WEATHER_*` settings
2. `backend/.env.example` — document the five keys with defaults
3. `backend/app/sports/football/football_weather.py` — `live_weather_for_match`, `_condition_from_code`, `_LIVE_CACHE`, `_clear_live_weather_cache`, `_utcnow`
4. `backend/app/sports/football/adapters/_shared.py` — live branch inside `enrich_weather_features`
5. `backend/tests/test_football_weather.py` — `TestLiveWeatherForMatch`
6. `backend/tests/test_adapter_shared.py` — `TestLiveWeatherFill`
7. `CHANGELOG.md`, `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

### Unchanged (verify only)
1. `backend/app/sports/football/engines/football_multi_factor_engine.py`
2. `backend/app/sports/football/feature_builder.py`
3. `backend/app/sports/_shared/team_geo.py` — `resolve_city` is consumed as-is

---

### Task 1: Config keys

**Files:**
- Modify: `backend/app/core/config.py`, `backend/.env.example`

**Interfaces:**
- Produces: `settings.FOOTBALL_LIVE_WEATHER_{URL,API_KEY,TIMEOUT_S,HORIZON_HOURS,CACHE_TTL_HOURS}`

- [x] **Step 1: Declare the settings** with an explanatory comment that an empty URL means "behaves exactly as before". Defaults: `""`, `""`, `5.0`, `72.0`, `6.0`.
- [x] **Step 2: Mirror into `.env.example`** near the other football keys, under a Chinese block comment stating that an empty URL keeps the old behaviour. Written uncommented with their defaults (`FOOTBALL_LIVE_WEATHER_URL=""` etc.) so the feature stays off when the file is copied verbatim.
- [x] **Step 3: Commit.**

---

### Task 2: `live_weather_for_match` unit tests (RED)

**Files:**
- Modify: `backend/tests/test_football_weather.py`

**Interfaces:**
- Consumes: (not yet) `live_weather_for_match`, `_clear_live_weather_cache`

- [x] **Step 1: Stub fixtures.** `_StubHome` / `_StubMatch` carrying `home.name` and `kickoff_utc`; a frozen `_NOW = 2025-09-16 12:00 UTC`.
- [x] **Step 2: Five failing cases**, each patching `httpx.get`, `_utcnow`, and `settings.FOOTBALL_LIVE_WEATHER_URL`:
  - configured + within horizon → normalized dict, `weathercode 61` → `rain`, `mock_get.call_count == 1`
  - URL `""` → `None` and `mock_get.assert_not_called()`
  - kickoff 14 days out → `None` and `mock_get.assert_not_called()`
  - `httpx.ConnectError` → `None`
  - `resp.json()` raises `ValueError` → `None`
- [x] **Step 3:** `setup_method` calls `_clear_live_weather_cache()` so no case inherits another's cache entry.
- [x] **Step 4: Commit RED.**

---

### Task 3: `live_weather_for_match` implementation (GREEN)

**Files:**
- Modify: `backend/app/sports/football/football_weather.py`

**Interfaces:**
- Produces: `live_weather_for_match(match) -> dict | None`

- [x] **Step 1: `_utcnow()`** as a thin wrapper over `datetime.now(timezone.utc)` — the seam the tests patch.
- [x] **Step 2: `_condition_from_code(code, temp_c)`** — WMO ranges → vocabulary, then the `<= 3.0 → cold` and `>= 26.0 & clear → hot` overrides. Unknown codes fall to `mild`.
- [x] **Step 3: `_LIVE_CACHE` + `_clear_live_weather_cache()`.**
- [x] **Step 4: The gate chain**, in order, each returning `None`: URL unset → kickoff missing → beyond horizon (naive kickoff coerced to UTC first) → `resolve_city` miss → cache probe → non-200 (cache the miss) → payload parse.
- [x] **Step 5: Normalize and cache** — clamp, `round(1)`, map the condition, store `(now_mono + ttl, result)`.
- [x] **Step 6: Wrap the whole body** in `except Exception:  # noqa: BLE001 — never raise into the adapter`, logging at debug with `exc_info=True`.
- [x] **Step 7:** Unit tests green; commit.

---

### Task 4: Adapter wiring + integration tests

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py`, `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Produces: `custom.weather_source == "live_forecast"` on a live hit

- [x] **Step 1: Integration tests first (RED)** — `TestLiveWeatherFill`: live used when env absent; explicit `0.0` env temp beats live; live failure → `static_climate`; beyond horizon skips the call; unconfigured → no HTTP + `static_climate`.
- [x] **Step 2: Insert the live branch** in `enrich_weather_features`, after the kickoff guard and before `climate_for_home`. Guard on `live is not None and live.get("weather_temp_c") is not None`; write environment **and** custom mirrors; tag `weather_source="live_forecast"`; return.
- [x] **Step 3: Import `live_weather_for_match` alongside `climate_for_home`** in the existing function-local import, keeping the module lazy.
- [x] **Step 4:** Full football suite green; commit.

---

### Task 5: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`, `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

- [x] **Step 1: CHANGELOG Unreleased**, under the P1-F7 notes: the provider shape, the horizon/TTL gates, and "returns None on any failure (never raises)"; call out that the five keys are off by default and that unset == today's behaviour.
- [x] **Step 2: Backlog P1-F7 row** — record `live_weather_for_match` → `weather_source=live_forecast` as delivered while keeping 真多源气象 API 仍待 open.
- [x] **Step 3: Commit.**
- [x] **Step 4 (retroactive, 2026-08-06):** write the missing spec + this plan.

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Five optional config keys, off by default | 1 |
| `live_weather_for_match` returns adapter field names or `None` | 2–3 |
| Four selection gates, in order | 3 |
| Never raises into the adapter | 3 |
| WMO → closed condition vocabulary + temp overrides | 3 |
| temp clamp [-15.0, 45.0] | 3 |
| TTL cache keyed (lat, lon, kickoff date); non-200 misses cached | 3 |
| Fill order env → live → static, zero-safe | 4 |
| `weather_source="live_forecast"` on live hit | 4 |
| Horizon / unconfigured provably skip HTTP | 2, 4 |
| MultiFactor unchanged | constraints |
| CHANGELOG + backlog | 5 |

## Placeholder / consistency scan

- No TBD placeholders.
- API names: `live_weather_for_match`, `_clear_live_weather_cache`, `_condition_from_code`, `_utcnow`, `enrich_weather_features`, `weather_source=live_forecast`.
- Test club: Arsenal (resolves through `team_geo`); frozen now = 2025-09-16 12:00 UTC; far kickoff = 2025-09-30 (14 days, past the 72 h horizon).
- Provider payload under test: `{"current_weather": {"temperature": 17.4, "weathercode": 61}}` → `17.4 / rain`.
