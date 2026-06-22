# World Cup Facts Guide

This guide shows how to feed structured 2026 FIFA World Cup facts into PMRF so
the sports-event pipeline can produce better probability context and safely
auto-resolve deterministic events.

The sports layer is intentionally conservative:

- Facts come from operators, official data, or trusted evidence. AI output is
  not a fact source.
- Probability impact is still judged by the analysis model; facts only provide
  structured context and deterministic settlement rules.
- Auto-resolution is fail-closed. If the facts are not decisive, the event stays
  pending.

## Endpoints

All paths below assume the backend is serving the API under `/api`.

Read status:

```text
GET /api/events/sports/world-cup/status
```

Read configured data-source status:

```text
GET /api/events/sports/world-cup/data/sources/status
Header: X-API-Key: <API_WRITE_KEY>
```

List facts:

```text
GET /api/events/sports/world-cup/facts
GET /api/events/sports/world-cup/facts?kind=injury
GET /api/events/sports/world-cup/facts?team=Brazil
```

Import facts:

```text
POST /api/events/sports/world-cup/facts/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"facts": [...]}
```

Import trusted match-data snapshots:

```text
POST /api/events/sports/world-cup/data/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"matches": [...], "qualifications": [...], "player_awards": [...], "player_statuses": [...]}

POST /api/events/sports/world-cup/data/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"matches": [...], "qualifications": [...], "player_awards": [...], "player_statuses": [...]}
```

Import multiple data-source payloads as one bundle:

```text
POST /api/events/sports/world-cup/data/bundle/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"sources": [{"kind": "matches", "payload": {...}}, {"kind": "standings", "payload": {...}}]}

POST /api/events/sports/world-cup/data/bundle/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"sources": [{"kind": "matches", "payload": {...}}, {"kind": "player_status", "payload": {...}}]}
```

The bundle endpoint is an operator convenience: it runs the same conservative
adapters used by the individual endpoints and imports the combined facts in one
write. Supported `kind` values are `data`, `matches`, `standings`,
`player_awards`, and `player_status`.

Import the configured multi-source bundle file:

```text
POST /api/events/sports/world-cup/data/bundle/source/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/source/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import the configured remote multi-source bundle URL:

```text
POST /api/events/sports/world-cup/data/bundle/url/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/url/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import configured raw source feed URLs as one bundle:

```text
POST /api/events/sports/world-cup/data/bundle/feeds/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/feeds/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import raw fixture/result exports through the match-source adapter:

```text
POST /api/events/sports/world-cup/matches/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"fixture": {...}, "teams": {...}, "goals": {...}}]}

POST /api/events/sports/world-cup/matches/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"fixture": {...}, "teams": {...}, "goals": {...}}]}
```

Import raw standings/group-table exports through the standings adapter:

```text
POST /api/events/sports/world-cup/standings/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"league": {"standings": [[...] ]}}]}

POST /api/events/sports/world-cup/standings/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"league": {"standings": [[...] ]}}]}
```

The standings adapter is conservative: it maps explicit source statuses such as
`qualified`, `advanced`, `knockout`, or `eliminated` into qualification facts. It
does not infer qualification from points, rank, or goal difference by itself.

Import raw top-scorers/player-awards exports through the player-awards adapter:

```text
POST /api/events/sports/world-cup/player-awards/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "statistics": [{"goals": {...}}]}]}

POST /api/events/sports/world-cup/player-awards/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "statistics": [{"goals": {...}}]}]}
```

Import raw injury/availability/suspension/lineup exports through the
player-status adapter:

```text
POST /api/events/sports/world-cup/player-status/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "team": {...}, "status": "out"}]}

POST /api/events/sports/world-cup/player-status/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "team": {...}, "status": "out"}]}
```

The player-status adapter maps raw injury, availability, suspension, and lineup
exports into `injury`, `availability`, `suspension`, or `lineup` facts. It
requires player and team names, and preserves status, severity, fixture/match
id, stage, notes/reason, and applies-to hints when present.

Import the configured trusted data-source file:

```text
POST /api/events/sports/world-cup/data/source/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/source/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Configured source files and remote bundle responses must include `source` and a
timezone-aware `observed_at` timestamp. For `WORLD_CUP_SOURCE_BUNDLE_FILE` or
`WORLD_CUP_SOURCE_BUNDLE_URL`, each `sources[]` entry must provide that metadata
either at the entry level or inside its `payload`. PMRF rejects stale snapshots older than
`WORLD_CUP_DATA_MAX_AGE_HOURS` (default: 168). Set the value to `0` only if an
operator intentionally wants to disable the age check.

Preview deterministic resolution:

```text
POST /api/events/sports/world-cup/resolve?dry_run=true
Header: X-API-Key: <API_WRITE_KEY>
```

Apply deterministic resolution:

```text
POST /api/events/sports/world-cup/resolve?dry_run=false
Header: X-API-Key: <API_WRITE_KEY>
```

## Minimal Operator Flow

1. Put structured facts in a JSON file using the sample at
   `docs/examples/world-cup-facts.sample.json`.
2. Or put trusted feed-shaped match data in a JSON file using the sample at
   `docs/examples/world-cup-data.sample.json`; PMRF converts it into facts.
   If your source exports CSV, use the JSON-wrapped CSV sample at
   `docs/examples/world-cup-data-csv.sample.json`.
   If you want to preview or import several raw exports in one request, use
   `docs/examples/world-cup-source-bundle.sample.json` with the
   `data/bundle/*` endpoints.
   If your source exports raw fixture/result records, use
   `docs/examples/world-cup-match-source.sample.json` with the `matches/*`
   endpoints; PMRF normalizes it first, then converts it into facts.
   If your source exports raw standings/group tables, use
   `docs/examples/world-cup-standings-source.sample.json` with the
   `standings/*` endpoints.
   If your source exports raw top-scorers/player-awards records, use
   `docs/examples/world-cup-player-awards-source.sample.json` with the
   `player-awards/*` endpoints.
   If your source exports raw player injury, availability, suspension, or lineup
   records, use `docs/examples/world-cup-player-status-source.sample.json` with
   the `player-status/*` endpoints.
3. For trusted match data, call `data/preview` and inspect the generated facts.
   For multi-source bundles, call `data/bundle/preview`.
   For raw fixture/result exports, call `matches/preview`; for raw standings,
   call `standings/preview`; for raw top-scorers/player-awards exports, call
   `player-awards/preview`; for raw player status exports, call
   `player-status/preview`. If the data lives in `WORLD_CUP_DATA_FILE`, call
   `data/source/preview`. If a multi-source bundle lives in
   `WORLD_CUP_SOURCE_BUNDLE_FILE`, call `data/bundle/source/preview`.
   If it lives behind `WORLD_CUP_SOURCE_BUNDLE_URL`, call
   `data/bundle/url/preview`. If raw source feeds are configured with
   `WORLD_CUP_MATCH_SOURCE_URL`, `WORLD_CUP_STANDINGS_SOURCE_URL`,
   `WORLD_CUP_PLAYER_AWARDS_SOURCE_URL`, or
   `WORLD_CUP_PLAYER_STATUS_SOURCE_URL`, call `data/bundle/feeds/preview`.
4. Import with `replace=true` when the file is the current full fact snapshot.
   Use `replace=false` for incremental upserts.
5. Call the facts list endpoint and inspect the normalized records.
6. Run `resolve?dry_run=true` and review every `would_resolve` row.
7. Run `resolve?dry_run=false` only when the dry-run output is correct.

## PowerShell Example

```powershell
$key = "<API_WRITE_KEY>"
$body = Get-Content -Raw docs\examples\world-cup-facts.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/facts/import?replace=true" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/resolve?dry_run=true" `
  -Headers @{ "X-API-Key" = $key }
```

For a trusted match-data snapshot, switch the import URL and body file:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-data.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For CSV exports, use the same endpoint with the CSV sample:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-data-csv.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For a multi-source bundle, use the bundle adapter:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-source-bundle.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw fixture/result exports, use the match-source adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-match-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/matches/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/matches/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw standings/group-table exports, use the standings adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-standings-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/standings/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/standings/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw top-scorers/player-awards exports, use the player-awards adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-player-awards-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-awards/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-awards/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw injury/availability/suspension/lineup exports, use the player-status
adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-player-status-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-status/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-status/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For a configured source file, set `WORLD_CUP_DATA_FILE` and call the source
endpoints. The file must include fresh source metadata:

```json
{
  "source": "official_feed",
  "source_url": "https://example.com/world-cup-feed",
  "observed_at": "2026-07-20T00:00:00Z",
  "matches": []
}
```

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/source/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/source/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For a configured multi-source bundle file, set `WORLD_CUP_SOURCE_BUNDLE_FILE`
and call the bundle source endpoints. Each bundle source must include fresh
metadata:

```json
{
  "sources": [
    {
      "kind": "matches",
      "source": "api_football",
      "source_url": "https://example.com/fixtures",
      "observed_at": "2026-07-20T00:00:00Z",
      "payload": {
        "response": [
          {
            "fixture": {"id": 1001, "status": {"short": "FT"}},
            "teams": {
              "home": {"name": "Team A", "winner": true},
              "away": {"name": "Team B", "winner": false}
            },
            "goals": {"home": 2, "away": 0}
          }
        ]
      }
    }
  ]
}
```

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/source/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/source/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For a configured remote multi-source bundle, set `WORLD_CUP_SOURCE_BUNDLE_URL`
and call the bundle URL endpoints. The URL must return the same JSON shape as
`docs/examples/world-cup-source-bundle.sample.json`. If the upstream requires a
key, configure `WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER` and
`WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE`; avoid putting real keys in URL query
strings.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/url/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/url/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For configured raw source feeds, set one or more of
`WORLD_CUP_MATCH_SOURCE_URL`, `WORLD_CUP_STANDINGS_SOURCE_URL`,
`WORLD_CUP_PLAYER_AWARDS_SOURCE_URL`, and
`WORLD_CUP_PLAYER_STATUS_SOURCE_URL`. PMRF fetches each configured URL, strips
query strings from returned `source_url` metadata, adds a fetch timestamp when
the payload lacks `observed_at`, and then runs the existing bundle adapters.
Use the shared `WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER` /
`WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE` settings when every configured feed uses
the same upstream auth header.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/feeds/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/feeds/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

To import a configured bundle on a schedule, set
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED=true`. Use
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=url` for `WORLD_CUP_SOURCE_BUNDLE_URL`, or
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=file` for `WORLD_CUP_SOURCE_BUNDLE_FILE`,
or `WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=feeds` for the configured raw source
feed URLs.
The default run time is 05:20 UTC and can be changed with
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC` and
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC`. Keep
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE=false` for incremental upserts; set it
to `true` only when the configured bundle is the full current fact snapshot.

Do not put real keys in committed files or docs.

## Fact Shape

Common fields:

```json
{
  "fact_id": "optional stable id",
  "kind": "injury",
  "tournament": "2026 FIFA World Cup",
  "team": "Brazil",
  "player": "Player Name",
  "status": "out",
  "severity": "high",
  "source": "manual",
  "source_url": "https://...",
  "confidence": 1.0,
  "observed_at": "2026-06-22T00:00:00Z",
  "applies_to": ["world-cup-2026:brazil-semifinal"],
  "notes": "Short operator note."
}
```

Supported `kind` values:

```text
injury
availability
suspension
discipline
qualification
match_state
match_result
lineup
player_award
tournament_status
```

## Current Resolution Rules

Team progression:

- YES when a `qualification` fact for the team says it reached the required
  stage.
- NO when a `qualification` fact says the team is eliminated before the stage.

Red-card threshold:

- YES when summed `red_cards` reaches the threshold in the event title.
- NO only when the tournament is explicitly complete and the total is below the
  threshold.

Penalty shootout:

- YES when any `match_result` / `match_state` fact has
  `penalty_shootout=true`.
- NO only when the tournament is explicitly complete with no shootout fact.

Final extra time:

- YES when a final `match_result` has `extra_time=true`.
- NO when a final `match_result` is finished and has `extra_time=false`.

Golden Boot / scorer threshold:

- YES when a top-scorer / Golden Boot `player_award` fact has `goals` at or
  above the threshold in the event title.
- NO when final top-scorer facts or a complete tournament show the top scorer
  finished below the threshold.

## Field Notes

- `confidence` is clamped to 0..1 and copied to the outcome confidence when a
  decisive fact resolves an event.
- `fact_id` may be omitted; PMRF generates a stable id from fact content. For
  repeat imports, provide your own stable `fact_id` when possible.
- `applies_to` can include a specific event source id such as
  `world-cup-2026:brazil-semifinal`, but entity matching also works for team
  facts.
- Use `tournament_status` with `tournament_complete=true` only after the whole
  tournament is complete.

## Trusted Data Import Shape

`data/preview` and `data/import` accept a compact source-normalized snapshot and
convert it to the same facts used by analysis and auto-resolution. Preview
returns generated facts without writing them:

- `matches`: creates `match_result` facts with score, red/yellow cards,
  `extra_time`, and `penalty_shootout`.
- `qualifications`: creates `qualification` facts for team progression.
- `player_awards`: creates `player_award` facts for Golden Boot / top scorer
  events.
- `player_statuses`: creates `injury`, `availability`, `suspension`, or
  `lineup` facts for player status and availability context.
- `tournament_status`: creates one `tournament_status` fact.

CSV exports can be wrapped under `csv.matches`, `csv.qualifications`, and
`csv.player_awards`, and `csv.player_statuses`. The first row must be headers
whose names match the JSON fields, such as `match_id`, `stage`, `home_team`,
`away_team`, `status`, `penalty_shootout`, `team`, `already_qualified`,
`award`, `player`, `goals`, `severity`, and `reason`.
