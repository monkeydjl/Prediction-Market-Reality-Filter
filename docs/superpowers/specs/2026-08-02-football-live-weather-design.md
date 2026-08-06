# Football Live Weather Forecast Fill (P1-F7 residual) — Design

**Date:** 2026-08-02
**Status:** Shipped (`bd89fd5`) — written retroactively; the implementation landed before this artifact
**Backlog:** P1-F7 (weather side; multi-source meteorological API still pending)

## Problem

`2026-07-26-football-static-weather-design.md` delivered the offline slice: `climate_for_home(team, month)` writes multi-year city×month priors into the adapter environment and tags them `weather_source="static_climate"`. That closed the "environment weather is empty" gap, but the value is a *climate prior*, not match-day weather:

1. A static September row for Arsenal cannot distinguish a 24 °C clear evening from a 12 °C downpour.
2. The static spec listed "Open-Meteo / any live forecast API for club fixtures this round" as an explicit non-goal, deferring it — this design picks that deferral up.
3. The legacy World Cup path already proved the Open-Meteo shape via `world_cup_weather_service`, but that service is coupled to the World Cup path and cannot be reused as-is by the club kernel adapter.
4. Any network call in `fetch_raw_match_data` is on the prediction hot path, so it has to be optional, bounded, cached, and silent on failure — otherwise it degrades the adapter it is meant to enrich.

## Goals

1. Add `live_weather_for_match(match) -> dict | None` to `backend/app/sports/football/football_weather.py`, returning the adapter's own field names (`weather_temp_c`, `weather_condition`) so it is drop-in against the existing enrich path.
2. **Off by default.** With no configuration the module makes zero HTTP calls and the adapter behaves exactly as it did before this change.
3. Insert live forecast into the weather fill order **between** explicit fixture data and static climate:
   `env explicit (zero-safe) → live_forecast → static_climate`.
4. Never raise into the adapter. Any miss — unconfigured, out of horizon, unresolved city, non-200, malformed payload, network error — returns `None` and the caller falls through to static climate.
5. Bound cost: configurable HTTP timeout, a kickoff horizon gate that skips the call for distant fixtures, and an in-memory TTL cache so repeated enrich calls for one fixture do not re-fetch.
6. Normalize the provider response into the **closed** condition vocabulary the static layer already uses (`clear | mild | rain | cold | hot`) so FeatureBuilder and the frontend see one shape regardless of source.
7. Leave `FootballMultiFactorEngine` weights and formulas unchanged (still no weather factor).

## Non-goals

- Multi-source / failover weather providers (backlog line stays open)
- Adding a MultiFactor `weather` factor or changing weights
- Wind, humidity, precipitation mm, pressure — temp + coarse condition only, matching the static layer
- Persistent (Redis / DB) forecast cache — in-memory TTL is sufficient for the enrich call pattern
- Historical / post-match actual weather backfill
- Retry or circuit-breaker logic beyond "fail silently, cache the failure"
- Away-city or travel-weather interaction
- Replacing `world_cup_weather_service` on the legacy path

## Approved approach

**Provider-agnostic URL template + normalization inside `football_weather.py`**

The provider is a configured URL, not a hardcoded vendor. The default template targets Open-Meteo (keyless), and the response is normalized to the adapter's field names, so swapping providers is a config change plus, at most, a change to the payload parse — never a change to `_shared.py`.

### Selection gates (evaluated in order; any miss → `None`)

| # | Gate | Rationale |
|---|------|-----------|
| 1 | `FOOTBALL_LIVE_WEATHER_URL` non-empty | Feature is opt-in; unset means no HTTP at all |
| 2 | Kickoff present, and `hours_to_kickoff <= FOOTBALL_LIVE_WEATHER_HORIZON_HOURS` | A *current* observation is meaningless for a fixture weeks out; static climate is the better prior there |
| 3 | `resolve_city(home_name, "football")` resolves to `(lat, lon, tz)` | Reuses the P1-F7 club geo table; no coordinates → nothing to query |
| 4 | HTTP 200 **and** parseable `current_weather.temperature` | Anything else is treated as a miss |

Gate 2 uses `>` on the upper bound only. A kickoff in the past is *not* gated out: in-play and just-finished fixtures still get a useful current observation.

### Rejected alternatives

| Option | Why not |
|--------|---------|
| Hardcode Open-Meteo client | Locks the vendor into the module; the URL template costs nothing and keeps the swap cheap |
| Reuse `world_cup_weather_service` | Coupled to the World Cup path and its own cache/settings; extracting it is a larger refactor than this slice |
| Live-first, ignore explicit fixture weather | Fixture-provided weather is authoritative; overwriting it with a third-party observation is a regression |
| Replace static climate entirely | Live only covers configured + near-kickoff + geo-resolvable fixtures; static remains the floor |
| Async / batched prefetch across fixtures | The enrich path is sync; a per-call TTL cache captures nearly the same saving |
| Persistent cache | Adds a Redis dependency to a soft-signal fill; TTL in memory is proportionate |
| Raise on provider error so callers can react | The adapter has a valid fallback; raising would turn a soft-signal miss into a prediction failure |

## Configuration

Five optional keys (`backend/app/core/config.py`, documented in `backend/.env.example`). Every one has a default that keeps the feature off or bounded:

| Key | Default | Meaning |
|-----|---------|---------|
| `FOOTBALL_LIVE_WEATHER_URL` | `""` | Provider endpoint. **Empty disables the whole feature.** |
| `FOOTBALL_LIVE_WEATHER_API_KEY` | `""` | Appended as the `apikey` query param when non-empty; omitted for keyless providers |
| `FOOTBALL_LIVE_WEATHER_TIMEOUT_S` | `5.0` | Per-request httpx timeout |
| `FOOTBALL_LIVE_WEATHER_HORIZON_HOURS` | `72.0` | Skip the call when kickoff is further out than this |
| `FOOTBALL_LIVE_WEATHER_CACHE_TTL_HOURS` | `6.0` | In-memory cache lifetime (see caching note below for what is cached) |

All five are read through `getattr(settings, ..., default)` rather than direct attribute access, so an older `settings` object without these fields degrades to the defaults instead of raising.

## Data model

### Public lookup

```python
def live_weather_for_match(match) -> dict[str, float | str | None] | None:
    """Live current-weather for a match's home city, or None on any failure.

    Selection gates (any miss -> None, so the adapter falls through to static
    climate): provider URL configured; kickoff within the configurable horizon;
    home city resolves to coordinates; HTTP 200 with a parseable payload.
    Never raises into the caller.
    """
```

Returns exactly:

```python
{
  "weather_temp_c": float,    # clamped to [-15.0, 45.0], round(1)
  "weather_condition": str,   # clear | mild | rain | cold | hot
}
```

The temperature clamp is the same band the static layer uses, so a bad provider reading cannot inject an out-of-range value that the static path would have rejected.

### Provider request

`httpx.get(url, params={"latitude": lat, "longitude": lon[, "apikey": key]}, timeout=...)`

Expected payload shape (Open-Meteo `current_weather`):

```json
{"current_weather": {"temperature": 17.4, "weathercode": 61}}
```

`temperature` is required — its absence is a miss. `weathercode` is optional and defaults to `0` when absent or non-integer.

### WMO weathercode → condition vocabulary

| Codes | Condition |
|-------|-----------|
| 0, 1 | `clear` |
| 2, 3, 45, 48 | `mild` (cloud / fog) |
| 51–67, 80–82 | `rain` (drizzle / rain / showers) |
| 71–77, 85, 86 | `cold` (snow / freezing) |
| anything else (incl. thunderstorm) | `mild` |

Two temperature overrides are then applied, in this order:

1. `temp_c <= 3.0` → force `cold` (a cold rainy match is better labelled cold than rain for a coarse 5-value vocab)
2. `temp_c >= 26.0` **and** condition is currently `clear` → `hot`

The override order matters: a 28 °C thunderstorm stays `mild`, not `hot`, because the `hot` promotion only fires from `clear`.

### Caching

In-memory dict, no persistence:

- **Key:** `(round(lat, 2), round(lon, 2), kickoff.date().isoformat())` — rounding coordinates collapses alias clubs sharing a city; the kickoff date keeps separate fixtures at one venue distinct.
- **Value:** `(expires_at_monotonic, result_or_None)`, using `time.monotonic()` so a wall-clock adjustment cannot extend or invalidate an entry.
- **What is cached:** successful results, and HTTP-non-200 misses (so a provider returning 429/500 is not hammered once per enrich call). Transport errors and malformed payloads fall through the `except` block and are *not* cached — they retry on the next call.
- `_clear_live_weather_cache()` is exported for test isolation only.

## Architecture / data flow

```text
enrich_weather_features(raw, match)
  |
  +-- env/custom already has weather_temp_c or weather_condition?
  |     -> normalize into environment (+ custom mirror); return
  |        (no weather_source override -- explicit data is authoritative)
  |
  +-- kickoff missing? -> return (no invent)
  |
  +-- live_weather_for_match(match)
  |     gate 1 URL configured -> gate 2 within horizon -> gate 3 city resolves
  |     -> cache hit? return cached
  |     -> httpx.get -> 200 + parseable -> clamp + normalize -> cache -> return
  |     any miss -> None
  |
  |     hit: environment.weather_temp_c / weather_condition
  |          custom mirrors
  |          custom.weather_source = "live_forecast"     <-- new
  |          return
  |
  +-- climate_for_home(home_name, kickoff.month)
        hit: environment + custom mirrors
             custom.weather_source = "static_climate"
        miss: leave empty

FootballFeatureBuilder -> environment.weather_temp_c / weather_condition (unchanged)
FootballMultiFactorEngine -> unchanged (still no weather factor)
```

### Zero-safety in the pass-through gate

The explicit-data check tests `is not None` for temperature (across `env.weather_temp_c`, `custom.weather_temp_c`, `env.temp_c`, `custom.temp_c`), **not** truthiness. A legitimate `0.0 °C` fixture reading must beat both live and static fill; a truthiness test would silently discard it. Condition, being a non-empty string when present, uses `or` chaining.

## Testing

**`backend/tests/test_football_weather.py::TestLiveWeatherForMatch`** (unit, all HTTP patched):

1. Configured + within horizon → normalized dict; `weathercode 61` → `rain`; exactly one HTTP call
2. `FOOTBALL_LIVE_WEATHER_URL=""` → `None`, `httpx.get` **not called**
3. Kickoff 14 days out (horizon 72 h) → `None`, `httpx.get` **not called**
4. `httpx.ConnectError` → `None` (no raise)
5. `resp.json()` raises → `None` (no raise)

`setup_method` calls `_clear_live_weather_cache()` so cached entries cannot leak between cases.

**`backend/tests/test_adapter_shared.py::TestLiveWeatherFill`** (integration through the enrich path):

1. Live forecast used when configured and environment weather absent → `weather_source="live_forecast"`
2. Explicit `0.0` env temperature beats live forecast (zero-safety regression guard)
3. Live failure falls back to static climate → `weather_source="static_climate"`
4. Beyond horizon skips the live call entirely
5. Unconfigured → no HTTP, static climate

Every test patches `settings.FOOTBALL_LIVE_WEATHER_URL` at the module path (`app.sports.football.football_weather.settings...`), never the real environment, so the suite makes no network calls under any configuration.

## Risks / mitigations

| Risk | Mitigation |
|------|------------|
| Network call on the prediction hot path | Off by default; horizon gate; bounded timeout; TTL cache; never raises |
| Provider outage degrades predictions | Failure returns `None` → static climate fallback; non-200 cached so the outage is not re-polled per call |
| `live_forecast` mistaken for a full weather model | Source tag distinguishes it; backlog line still marks the multi-source API as pending |
| Coarse 5-value vocabulary loses forecast detail | Deliberate — matches the static layer so downstream consumers see one shape |
| Cache key collision between clubs sharing a city | Intended: same city, same date, same weather |
| Silent `except Exception` hides a real bug | `logger.debug(..., exc_info=True)` preserves the traceback for anyone who raises the log level |

## Acceptance criteria

1. `live_weather_for_match` exists in `football_weather.py`, returns adapter field names or `None`, and never raises.
2. With `FOOTBALL_LIVE_WEATHER_URL` unset, behaviour is byte-identical to pre-change and no HTTP is attempted.
3. Adapter fill order is env explicit (zero-safe) → `live_forecast` → `static_climate`, with the matching `custom.weather_source` tag on each path.
4. All five `FOOTBALL_LIVE_WEATHER_*` keys are declared in `config.py` with defaults and documented in `.env.example`.
5. Temperature clamped to `[-15.0, 45.0]`; condition always within `{clear, mild, rain, cold, hot}`.
6. Horizon and TTL gates provably skip HTTP (asserted via `mock_get.assert_not_called()` / call count).
7. MultiFactor weights and formulas unchanged.
8. Unit + adapter tests green; `CHANGELOG.md` and the P1-F7 backlog row updated.
