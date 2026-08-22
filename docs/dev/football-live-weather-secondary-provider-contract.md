# Football secondary weather provider contract

## Purpose

`backend/app/services/football_live_weather_service.py` optionally retrieves a **second, independent** weather reading for a football fixture's home city. It closes the P1-F7 multi-source gap: the primary source stays the keyless Open-Meteo-shaped path in `backend/app/sports/football/football_weather.py`, and this service adds a licensed provider alongside it.

It is disabled by default, only fills the existing `weather_temp_c` / `weather_condition` features plus provenance, and does not enable learning, scheduling, market writes, or automatic actions.

## Configuration

Configure all required values in `backend/.env`; a disabled flag, missing URL, or missing API key causes zero outbound requests and leaves the single-source behaviour unchanged:

```dotenv
FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED=true
FOOTBALL_LIVE_WEATHER_SECONDARY_URL=https://provider.example/point
FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY=...
```

Optional tuning: `FOOTBALL_LIVE_WEATHER_SECONDARY_TIMEOUT_S` (default `5.0`), `FOOTBALL_LIVE_WEATHER_SECONDARY_CACHE_TTL_HOURS` (default `1.0`), `FOOTBALL_LIVE_WEATHER_SECONDARY_MAX_BYTES` (default `262144`).

The service sends an HTTP GET with:

- `latitude` / `longitude`: the resolved home-city coordinates, rounded to two decimals;
- `date`: the fixture's UTC calendar date, such as `2026-09-16`.

Existing `latitude`, `longitude`, and `date` query parameters in the configured URL are replaced; other query parameters are preserved. The key is sent only in `Authorization: Bearer <key>`; keys and raw response bodies are never logged.

## Required response envelope

The endpoint must return UTF-8 JSON in this shape:

```json
{
  "weather": {
    "temp_c": 17.4,
    "condition": "rain"
  }
}
```

Rules:

- `weather` is an object.
- `temp_c` is finite and within `[-60, 60]` degrees Celsius; the consumer then clamps to the `[-15, 45]` feature band shared with the static climate table.
- `condition` is one of `clear`, `mild`, `rain`, `cold`, `hot`, compared case-insensitively after whitespace collapse.
- Error envelopes, malformed JSON, oversized bodies, non-numeric temperatures, booleans, out-of-range values, and unrecognized condition labels invalidate the whole reading.

The provider must supply a genuine weather observation or forecast for the requested point and date. The integration does not derive a second reading from the primary source, from climate priors, or from fabricated values.

## Consensus and provenance

Both live sources are gated by the shared checks first: at least one source configured, kickoff within `FOOTBALL_LIVE_WEATHER_HORIZON_HOURS`, and the home city resolving to coordinates. Each configured source is then read best-effort — one failing never suppresses the other — and merged deterministically:

| Situation | Temperature | Condition | `weather_agreement` |
| --- | --- | --- | --- |
| One source available | that source's value | that source's label | `single` |
| Both within 5 °C, same label | mean of the two | shared label | `agree` |
| Both within 5 °C, different labels | mean of the two | primary's label | `temp_only` |
| Both beyond 5 °C apart | primary's value | primary's label | `diverged` |

Because divergence beyond the tolerance falls back to the primary reading, a misconfigured or drifting second provider can never move the temperature feature by more than the tolerance.

Weather fill order in the adapter is unchanged: explicit environment/custom values (zero-safe) → live consensus (`weather_source="live_forecast"`) → static climate (`weather_source="static_climate"`). When the live path resolves, the adapter also writes `custom.weather_source_count` and `custom.weather_agreement` for diagnostics; the feature contract consumed downstream stays `weather_temp_c` / `weather_condition`.

Valid readings are cached in process memory only, keyed by rounded coordinates and fixture date, until the configured TTL expires. Failures are not cached, so a transient provider fault does not pin an unavailable answer for a whole TTL. The two sources keep independent caches and TTLs.

## Operational readiness

The code is ready for a provider, but production activation requires a licensed URL/key returning the documented contract. Keep `FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED=false` until those credentials and the endpoint are provisioned and validated in the target environment.
