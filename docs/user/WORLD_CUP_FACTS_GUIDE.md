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
2. Import with `replace=true` when the file is the current full fact snapshot.
   Use `replace=false` for incremental upserts.
3. Call the facts list endpoint and inspect the normalized records.
4. Run `resolve?dry_run=true` and review every `would_resolve` row.
5. Run `resolve?dry_run=false` only when the dry-run output is correct.

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

Gold Boot / scorer-threshold events are not auto-resolved yet.

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
