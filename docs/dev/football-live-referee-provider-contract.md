# Football live referee-stat provider contract

## Purpose

`backend/app/services/football_live_referee_service.py` optionally retrieves a licensed provider's **season-level referee home-win statistics** for club football. It is disabled by default and only enriches feature provenance; it does not enable learning, scheduling, market writes, or automatic actions.

## Configuration

Configure all required values in `backend/.env`; a missing URL or API key causes zero outbound requests:

```dotenv
FOOTBALL_LIVE_REFEREE_ENABLED=true
FOOTBALL_LIVE_REFEREE_URL=https://provider.example/referees
FOOTBALL_LIVE_REFEREE_API_KEY=...
FOOTBALL_LIVE_REFEREE_SEASON_PARAM=season
```

The service sends an HTTP GET with:

- `competition`: internal competition code, such as `epl` or `ucl`;
- the configured season parameter (default `season`): the season start year, such as `2026` for `2026-27`.

Existing competition and season query parameters are replaced. The key is sent only in `Authorization: Bearer <key>`; keys and raw response bodies are never logged.

## Required response envelope

The endpoint must return UTF-8 JSON in this shape:

```json
{
  "referees": [
    {
      "referee": "Michael Oliver",
      "home_win_rate": 0.54,
      "matches": 24
    }
  ]
}
```

Rules:

- `referees` is an array of objects.
- `referee` is non-empty; matching ignores accents, punctuation, case, and extra whitespace.
- `home_win_rate` is finite and in `[0, 1]`.
- `matches` is a whole number in `[1, 100]`.
- Names must be unique after normalization.
- Error envelopes, malformed JSON, incomplete rows, duplicate names, booleans, and invalid ranges invalidate the entire snapshot.

The provider must supply genuine season referee statistics. The integration does not infer referee behavior from team priors, league averages, ratings, or fabricated values.

## Fallback and provenance

For a named referee, enrichment uses this precedence:

1. explicit pre-existing `custom.referee_home_win_rate` or `custom.referee_home_bias`;
2. complete live referee row, writing `referee_home_win_rate`, derived bounded `referee_home_bias = 2 × rate − 1`, and `referee_source="live_provider"`;
3. existing static referee map, writing `referee_home_bias` and `referee_source="static_map"`;
4. no referee statistic when no source resolves.

World Cup enrichment does not call this configured club-provider path. Valid snapshots remain only in process memory, keyed by competition and season, until the configured TTL expires.

## Operational readiness

The code is ready for a provider, but production activation requires a licensed URL/key returning the documented contract. Keep `FOOTBALL_LIVE_REFEREE_ENABLED=false` until those credentials and the endpoint are provisioned and validated in the target environment.
