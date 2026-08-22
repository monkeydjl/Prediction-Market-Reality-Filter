# Football live schedule-density provider contract

## Purpose

`backend/app/services/football_live_schedule_service.py` optionally supplies recent football fixtures for schedule-density enrichment. It is a read-only data source: fetched fixtures are held in process memory and are never written to the kernel database.

## Configuration

Configure all required values in `backend/.env`; missing URL or API key causes zero outbound requests:

```dotenv
FOOTBALL_LIVE_SCHEDULE_ENABLED=true
FOOTBALL_LIVE_SCHEDULE_URL=https://provider.example/fixtures
FOOTBALL_LIVE_SCHEDULE_API_KEY=...
FOOTBALL_LIVE_SCHEDULE_SEASON_PARAM=season
FOOTBALL_LIVE_SCHEDULE_HISTORY_DAYS=14
```

Requests include `competition` and the configured season-start-year parameter. Existing values for those parameters are replaced. Authentication uses `Authorization: Bearer <key>`. Keys and raw responses are never logged.

## Required response envelope

```json
{
  "fixtures": [
    {
      "match_id": "provider-1",
      "home_team": "Arsenal",
      "away_team": "Chelsea",
      "kickoff_utc": "2026-08-16T15:00:00Z",
      "status": "scheduled"
    }
  ]
}
```

Rules:

- `fixtures` is an array of objects.
- `match_id`, both team names, and a timezone-aware `kickoff_utc` are required.
- Home and away teams must differ.
- IDs must be unique.
- Status must be one of `scheduled`, `in_play`, `finished`, `postponed`, `cancelled`, or `suspended`.
- Malformed JSON, provider errors, duplicate IDs, invalid timestamps, invalid status, or oversized responses invalidate the complete snapshot.
- When a prediction cutoff is supplied, only fixtures in the configured historical window and strictly before that cutoff are returned.

## Fallback and density behavior

The adapter keeps the kernel fixture history as the authoritative first source. The configured provider is consulted only when the relevant kernel history is empty or unavailable. Cross-competition live fallback requests each registered football competition and preserves the competition on every row before applying the existing competition-scoped alias resolution. No database rows or predictions are created.

The existing 7-day, 3-day, current-match exclusion, congestion, and default-off engine behavior remain unchanged. If the provider is disabled or unavailable, the adapter returns to kernel-only behavior.

## Operational readiness

Production activation requires a licensed endpoint and key returning this contract. Keep `FOOTBALL_LIVE_SCHEDULE_ENABLED=false` until the provider is provisioned and validated in the target environment.
