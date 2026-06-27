# World Cup Data Enhancement Notes

This note records public GitHub sources and the current integration path for
improving World Cup prediction factors.

## Integrated

### openfootball/worldcup.json

- URL: https://github.com/openfootball/worldcup.json
- License: CC0-1.0
- Local files:
  - `backend/data/openfootball-2026/worldcup.json`
  - `backend/data/openfootball-2026/worldcup.groups.json`
  - `backend/data/openfootball-2026/worldcup.teams.json`
  - `backend/data/openfootball-2026/worldcup.squads.json`
  - `backend/data/openfootball-2026/worldcup.stadiums.json`
  - `backend/data/openfootball-2026/worldcup.quali_playoffs.json`
- Current usage:
  - Local team metadata, group, FIFA code, confederation.
  - Squad size, position counts and average age when kickoff date is known.
  - Stadium city/timezone/capacity lookup for match context.
  - Fixture reference without consuming `score`/goals fields.
- Implemented in:
  - `backend/app/services/world_cup_openfootball_data.py`
  - `backend/app/services/world_cup_prediction_pipeline.py`

### martj42/international_results

- URL: https://github.com/martj42/international_results
- License: CC0-1.0
- Local files:
  - `backend/data/international_results.csv`
  - `backend/data/international_results.LICENSE`
- Useful fields:
  - `date`, `home_team`, `away_team`, `home_score`, `away_score`
  - `tournament`, `neutral`
- Current usage:
  - Fallback recent team form/team stats when API-Football is unavailable.
  - Fallback H2H when API-Football H2H is unavailable.
- Implemented in:
  - `backend/app/services/world_cup_historical_results.py`
  - `backend/app/services/world_cup_prediction_pipeline.py`

## Evaluated Sources

### openfootball/worldcup

- URL: https://github.com/openfootball/worldcup
- License: CC0-1.0
- Best use:
  - Human-readable Football.TXT source for World Cup fixture/result snapshots.
  - Useful as a manual audit source, less direct than JSON for ingestion.

## Engine/Signal Improvements

Practical near-term signals:

- `historical_recent_form`: last 10 international results before kickoff.
- `historical_h2h`: last 20 direct meetings, expressed from scheduled home-team perspective.
- `stale_market_signal`: old but real cached odds when fresh odds are unavailable; included but down-weighted.
- `neutral_venue`: from martj42/openfootball fields when available.
- `tournament_context`: weight competitive matches higher than friendlies for future form calculations.
- `squad_availability`: openfootball squads can support squad-depth and missing-player factors if paired with injury/status facts.
- `venue_travel`: openfootball stadium/city plus team confederation can support coarse travel/rest factors.

Model direction:

- Keep the current three-engine UI contract.
- Strengthen factor inputs before adding another public engine.
- For a future offline engine, prefer Dixon-Coles/Poisson over a black-box model:
  - attack/defense strength from historical international results
  - Elo prior
  - market odds adjustment when fresh or stale odds exist
  - group-stage motivation and schedule density already present

## Current Caveats

- API-Football quota was exhausted during inspection, so team-id resolver could not populate a verified API cache in this run.
- Existing hardcoded API-Football team IDs are still only a last-resort fallback and are known to contain duplicates.
- Current predictions must be regenerated before already-persisted factors show the new historical stats/H2H sources.
