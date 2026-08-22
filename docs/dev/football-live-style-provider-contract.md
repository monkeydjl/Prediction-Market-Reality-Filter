# Football live style-stat provider contract

## Purpose

`backend/app/services/football_live_style_service.py` optionally retrieves a licensed provider's **season-level possession, shots-per-90, and PPDA** snapshot for club football. It is disabled by default and only improves enrichment data; it does not enable learning, scheduling, market writes, or automatic actions.

## Configuration

Configure all required values in `backend/.env`; a missing URL or API key causes zero outbound requests:

```dotenv
FOOTBALL_LIVE_STYLE_ENABLED=true
FOOTBALL_LIVE_STYLE_URL=https://provider.example/style/teams
FOOTBALL_LIVE_STYLE_API_KEY=...
FOOTBALL_LIVE_STYLE_SEASON_PARAM=season
```

The service sends an HTTP GET to the configured URL with:

- `competition`: internal competition code, such as `epl` or `ucl`;
- the configured season parameter (default `season`): the season start year, such as `2026` for `2026-27`.

Configured `competition` or season query values are replaced. The key is sent only in `Authorization: Bearer <key>`; neither keys nor raw bodies are logged.

## Required response envelope

The endpoint must return UTF-8 JSON in precisely this shape:

```json
{
  "teams": [
    {
      "team": "Arsenal",
      "possession_pct": 57.2,
      "shots_per90": 15.1,
      "ppda": 9.3
    }
  ]
}
```

Rules:

- `teams` is an array of objects.
- `team` is non-empty. Matching ignores accents, punctuation, case, and `FC`/`CF` suffixes.
- Each row has finite numeric `possession_pct` in `[20, 80]`, `shots_per90` in `[1, 40]`, and `ppda` in `[1, 40]`.
- Team names must be unique after normalization.
- Error envelopes, malformed JSON, incomplete rows, duplicate teams, and invalid values invalidate the whole snapshot.

The provider must supply genuine possession, shots, and PPDA values. Goals, ratings, Elo, or fabricated substitutes are not accepted as style statistics.

## Fallback and provenance

Both match teams must resolve from one valid live snapshot before enrichment writes all style features and:

```text
custom.style_source = "live_provider"
```

Otherwise the existing sequence remains:

1. complete static `football_style` pair (`style_source="static_table"`);
2. existing form-share possession proxy;
3. absent style fields when neither source resolves.

World Cup enrichment does not call this configured club-provider path. Valid snapshots remain only in process memory, keyed by competition and season, until the configured TTL expires.

## Operational readiness

The integration is ready for a provider, but production activation requires a licensed URL/key returning the documented contract. Keep `FOOTBALL_LIVE_STYLE_ENABLED=false` until those credentials and the endpoint are provisioned and validated in the target environment.
