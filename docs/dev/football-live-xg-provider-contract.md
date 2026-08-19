# Football live true-xG provider contract

## Purpose

`backend/app/services/football_live_xg_service.py` can optionally read a licensed provider's **season-level true expected-goals** snapshot for club football. It is disabled by default and is a data-source improvement only: it does not enable learning, scheduling, market writes, or automatic actions.

## Configuration

Set all required values in `backend/.env`; a missing URL **or API key** causes zero outbound requests:

```dotenv
FOOTBALL_LIVE_XG_ENABLED=true
FOOTBALL_LIVE_XG_URL=https://provider.example/xg/teams
FOOTBALL_LIVE_XG_API_KEY=...
FOOTBALL_LIVE_XG_SEASON_PARAM=season
```

The service sends an HTTP GET to the configured URL with these query parameters:

- `competition`: internal competition code, such as `epl` or `ucl`;
- the configured season parameter (default `season`): the season start year, such as `2026` for `2026-27`.

Existing `competition` or season query parameters in the configured URL are replaced. An API key, when configured, is sent only as `Authorization: Bearer <key>`; neither keys nor response bodies are logged.

## Required response envelope

The endpoint must return UTF-8 JSON in exactly this shape:

```json
{
  "teams": [
    {"team": "Arsenal", "xg_per90": 1.72},
    {"team": "Chelsea FC", "xg_per90": 1.38}
  ]
}
```

Rules:

- `teams` must be a JSON array of objects.
- `team` must be a non-empty team name. Matching ignores accents, punctuation, case, and `FC`/`CF` suffixes.
- `xg_per90` must be a finite numeric value in the inclusive range `[0.1, 5.0]`.
- Names must be unique after normalization.
- Error envelopes, malformed JSON, goals/shots/ratings/Elo substitutes, missing values, duplicate teams, or values outside the permitted range invalidate the complete snapshot.

The provider must supply genuine expected-goals data. This integration does not reinterpret goals, shots, ratings, or synthetic metrics as xG.

## Fallback and provenance

For a match, both teams must resolve from the same valid live snapshot before enrichment sets:

```text
custom.xg_home
custom.xg_away
custom.xg_source = "live_provider"
```

If the live source is disabled, unavailable, malformed, or incomplete for either team, the adapter retains the existing fallback order:

1. both-team static `football_xg` lookup (`xg_source="static_table"`);
2. existing goals-per-game proxy;
3. no xG values when neither source is available.

World Cup enrichment does not call this configured club-provider path. Valid snapshots are retained only in process memory, keyed by competition and season, for the configured TTL.

## Operational readiness

The code is ready to connect to a provider, but production use still requires a licensed endpoint and key whose payload obeys this contract. Keep `FOOTBALL_LIVE_XG_ENABLED=false` until those are provisioned and validated in the target environment.
