# Sports Prediction OS — Phase 2 Design: UCL + EPL League Extension

**Date:** 2026-07-14
**Status:** Draft
**Depends on:** Phase 1 (`backend/app/kernel/`, `backend/app/sports/football/`)
**Predecessor:** `docs/superpowers/specs/2026-07-11-sports-prediction-os-phase1-design.md`

---

## 1. Goal

Extend the Prediction Kernel from World Cup-only to support **UEFA Champions League (UCL)** and **English Premier League (EPL)**, increasing coverage from 64 matches/year to ~500 matches/year (UCL ~125 + EPL ~380).

Phase 2 establishes a reusable Adapter pattern so that La Liga, Bundesliga, Serie A, and Ligue 1 can be added in follow-up work with minimal effort (a thin Adapter + competition config).

### Success Criteria

1. `/api/predictions/matches/ucl-{id}/predict` returns a valid `PredictionResult` for UCL matches
2. `/api/predictions/matches/epl-{id}/predict` returns a valid `PredictionResult` for EPL matches
3. Existing World Cup predictions (`/api/predictions/matches/wc-{id}/predict`) continue to work unchanged
4. Kernel code (`backend/app/kernel/`) has zero modifications
5. `WorldCupAdapter` has zero modifications
6. `FootballFeatureBuilder` has zero modifications
7. All new tests pass; all Phase 1 tests pass (no regression)

---

## 2. Scope

### In Scope

- Parameterized Football-Data.org client (`football_data_client.py`)
- ClubElo.com CSV scraping service (`club_elo_service.py`)
- Shared adapter utilities (`_shared.py`)
- `UCLAdapter` and `EPLAdapter` implementing `DataAdapter` Protocol
- `MultiAdapter` proxy for match_id-prefix-based dispatch
- `kernel_match_fixtures` and `kernel_match_results` database tables
- `KernelClubEloCache` database table for club Elo caching
- API route updates to register multi-league adapters
- Configuration items for Phase 2 feature flags and data sources

### Out of Scope

- La Liga, Bundesliga, Serie A, Ligue 1 adapters (follow-up, same pattern)
- Frontend changes (Phase 2 is backend-only)
- Standings/table inference for UCL/EPL (World Cup's `already_qualified` logic is tournament-specific)
- Calibration and weight updates (Phase 3)
- NBA / basketball (Phase 4)
- Modifying existing `football_data_source.py` or `world_cup_*` files

---

## 3. Architecture

### 3.1 Module Layout

```
backend/app/
├── services/
│   ├── football_data_client.py      # NEW: Parameterized Football-Data.org client
│   ├── club_elo_service.py          # NEW: ClubElo.com CSV fetcher + cache
│   ├── football_data_source.py      # UNCHANGED: World Cup-specific legacy
│   ├── elo_ratings_service.py       # UNCHANGED: National team Elo
│   └── odds_cache_service.py        # UNCHANGED: Already league-agnostic
├── sports/football/
│   ├── adapters/
│   │   ├── __init__.py              # UNCHANGED
│   │   ├── _shared.py               # NEW: Shared utility functions (composition)
│   │   ├── world_cup_adapter.py     # UNCHANGED: Phase 1 legacy
│   │   ├── ucl_adapter.py           # NEW: UCL DataAdapter implementation
│   │   ├── epl_adapter.py           # NEW: EPL DataAdapter implementation
│   │   └── multi_adapter.py         # NEW: Prefix-dispatch proxy
│   └── feature_builder.py           # UNCHANGED: Already generic
├── kernel/
│   ├── kernel_db.py                 # MODIFIED: Add 3 new tables
│   └── (all other files UNCHANGED)
├── api/routes/
│   └── predictions.py               # MODIFIED: Register MultiAdapter
└── core/
    └── config.py                    # MODIFIED: Add Phase 2 config items
```

### 3.2 Data Flow

```
API Route → MultiAdapter (dispatch by match_id prefix)
  ├─ "wc-"  → WorldCupAdapter (Phase 1, unchanged)
  ├─ "ucl-" → UCLAdapter
  │            ├─ get_match_identity() → _shared.query_fixture(KernelMatchFixture)
  │            ├─ fetch_all_data() → _shared.fetch_team_elo(scope="club")
  │            │                     + _shared.fetch_match_odds()
  │            └─ fetch_outcome() → _shared.query_result(KernelMatchResult)
  └─ "epl-" → EPLAdapter (same structure, different config)
                    ↓
         FootballFeatureBuilder.build(match, raw)  → FeatureSet
                    ↓
         EloOddsEngine.predict(features, match)    → PredictionResult
                    ↓
         KernelLearningService.record_prediction()  → kernel_predictions table
```

### 3.3 Design Principles

1. **Composition over inheritance**: `_shared.py` provides pure utility functions; adapters call them freely. No base class, no inheritance hierarchy.
2. **Zero disruption to Phase 1**: Kernel, WorldCupAdapter, FootballFeatureBuilder, and all `world_cup_*` services remain untouched.
3. **Protocol-transparent**: `MultiAdapter` implements `DataAdapter` Protocol; the Kernel sees a single adapter, unaware of multi-league dispatch.
4. **Graceful degradation**: Missing API keys, failed scrapes, and empty tables all degrade to `None`/empty — the engine's None-Elo and no-odds fallback paths handle the rest.

---

## 4. Data Source Services

### 4.1 `football_data_client.py`

A new generic client for the Football-Data.org v4 API, parameterized by competition code.

**Functions:**

```python
def fetch_competition_fixtures(
    competition: str,       # "WC" | "CL" | "PL"
    season: int = 2026,
) -> list[dict[str, Any]]:
    """Fetch fixtures for a competition from Football-Data.org.

    Calls: GET /competitions/{competition}/matches?season={season}
    Returns: list of raw match dicts from the API response.
    Raises: FootballDataClientError if API key missing or request fails.
    """

def parse_fixture(
    match_data: dict[str, Any],
    stage_mapping: dict[str, str] | None = None,
    match_id_prefix: str = "fd-",
) -> dict[str, Any] | None:
    """Parse a raw API match dict into internal fixture format.

    Extracts: match_id, home_team, away_team, kickoff_utc, stage, group,
    status, venue, home_score, away_score.

    stage_mapping translates API stage names (e.g., "GROUP_STAGE") to
    internal canonical names (e.g., "group_stage"). If a stage is not
    in the mapping, it passes through lowercased. If stage_mapping is
    None, all stages map to "regular_season".

    Returns None if match_data is malformed.
    """

def _football_data_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HTTP GET with X-Auth-Token header, retry on 429.

    Reuses the same authentication and error-handling pattern as the
    existing football_data_source.py but lives in a separate module.
    """
```

**Stage Mappings (defined per-adapter, not in the client):**

| Competition | API Code | Stage Mapping |
|-------------|----------|---------------|
| World Cup | `WC` | (legacy, not used by new client) |
| UCL | `CL` | `GROUP_STAGE→group_stage`, `ROUND_OF_16→round_of_16`, `QUARTER_FINALS→quarterfinal`, `SEMI_FINALS→semifinal`, `FINAL→final` |
| EPL | `PL` | (empty — all stages → `regular_season`) |

**Configuration:**
- `FOOTBALL_DATA_API_KEY` (existing): Required. If empty, `fetch_competition_fixtures` raises `FootballDataClientError`.
- `FOOTBALL_DATA_BASE_URL` (existing, default `https://api.football-data.org/v4`)

### 4.2 `club_elo_service.py`

A new service for fetching club Elo ratings from ClubElo.com's free CSV API.

**API endpoints used:**
- `http://api.clubelo.com/{YYYY-MM-DD}` — Full ranking snapshot for a given date
- `http://api.clubelo.com/{ClubName}` — Historical Elo time series for a single club (URL: club name with spaces removed, e.g., `ManCity`)

**CSV schema (7 columns):**

| Column | Type | Example |
|--------|------|---------|
| Rank | int/None | 1 |
| Club | str | Man City |
| Country | str (3-letter) | ENG |
| Level | int | 1 |
| Elo | float | 2063.76 |
| From | date (YYYY-MM-DD) | 2026-05-31 |
| To | date (YYYY-MM-DD) | 2026-08-21 |

**Functions:**

```python
def get_club_elo(team_name: str) -> dict[str, Any] | None:
    """Get current club Elo rating.

    1. Check KernelClubEloCache table (TTL: CLUB_ELO_CACHE_TTL_DAYS).
    2. On cache miss/expire, fetch from ClubElo.com.
    3. Parse CSV, find matching club (case-insensitive, space-normalized).
    4. Cache result in KernelClubEloCache.
    5. Return {"elo_rating": float, "source": "clubelo"} or None on failure.
    """

def fetch_club_elo_snapshot(date: str | None = None) -> list[dict[str, str]]:
    """Fetch full ranking CSV for a given date (default: today).

    Returns list of dicts with keys: Rank, Club, Country, Level, Elo, From, To.
    Uses csv.DictReader for parsing (no pandas dependency).
    """

def get_club_elo_by_country(country: str, level: int = 1) -> dict[str, float]:
    """Fetch snapshot and filter by country + level.

    Returns {team_name: elo_rating} for all clubs in the specified
    country's specified league level. E.g., country="ENG", level=1 →
    all Premier League clubs.
    """
```

**Design decisions:**
- No API key required (ClubElo.com is free and public).
- Request interval: `CLUB_ELO_REQUEST_INTERVAL` seconds (default 1.0) between requests to avoid rate limiting.
- Parsing uses `csv.DictReader` from stdlib — no pandas dependency.
- Team name matching: normalize by lowercasing and removing spaces. E.g., "Manchester City" matches "Man City" via alias lookup (initial aliases cover top 50 clubs; extensible).
- Graceful degradation: network failure or team not found → return `None`.

**Configuration:**
- `CLUB_ELO_CACHE_TTL_DAYS: int = 7`
- `CLUB_ELO_REQUEST_INTERVAL: float = 1.0`

### 4.3 Team Name Alias Strategy

Football-Data.org and ClubElo.com use different team name conventions:

| Football-Data.org | ClubElo.com |
|-------------------|-------------|
| Manchester City FC | Man City |
| Arsenal FC | Arsenal |
| Tottenham Hotspur FC | Tottenham |
| Real Madrid CF | Real Madrid |
| FC Bayern München | Bayern Munich |

Each Adapter defines a `_TEAM_ALIASES` dict mapping its data source's names to ClubElo.com names. The `_shared.fetch_team_elo()` function applies the alias before calling `club_elo_service.get_club_elo()`.

Initial aliases cover all UCL 2025-26 participants and EPL 2025-26 clubs. Unknown teams fall through with their original name.

---

## 5. Adapter Layer

### 5.1 `_shared.py` — Shared Utility Functions

Pure utility functions extracted to avoid code duplication across adapters. No class, no state.

```python
# backend/app/sports/football/adapters/_shared.py

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.kernel.domain import (
    CompetitionIdentity, SeasonIdentity, TeamIdentity,
    MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData

logger = logging.getLogger(__name__)


async def fetch_team_elo(
    team_name: str,
    scope: str = "national",
    alias: str | None = None,
) -> dict[str, Any] | None:
    """Fetch Elo rating for a team.

    scope="national": delegates to app.services.elo_ratings_service.get_elo_rating()
    scope="club": delegates to app.services.club_elo_service.get_club_elo()

    alias: if provided, used as the lookup name instead of team_name
    (for mapping Football-Data.org names to ClubElo.com names).

    Returns {"elo_rating": float, "source": str} or None on failure.
    """
    lookup_name = alias or team_name
    if scope == "club":
        from app.services.club_elo_service import get_club_elo
        return get_club_elo(lookup_name)  # sync function, OK in async context
    else:
        from app.services.elo_ratings_service import get_elo_rating
        return await get_elo_rating(lookup_name)  # async function, needs await


async def fetch_match_odds(home: str, away: str) -> dict[str, Any] | None:
    """Fetch cached odds for a match.

    Delegates to app.services.odds_cache_service.get_cached_odds().
    Returns the odds dict or None on failure.
    """
    from app.services.odds_cache_service import get_cached_odds
    return await get_cached_odds(home, away)


def fetch_elo_and_odds(
    match: MatchIdentity,
    elo_scope: str = "national",
    team_aliases: dict[str, str] | None = None,
) -> dict:
    """Fetch Elo ratings + odds for a match in a single asyncio.run() call.

    Consolidates three async calls (elo_home, elo_away, odds) into one
    event loop via asyncio.gather(return_exceptions=True). Mirrors the
    WorldCupAdapter.fetch_all_data() pattern.

    team_aliases: {team_name: clubelo_name} for name mapping.

    Returns dict with keys:
        team: {elo_home: float|None, elo_away: float|None}
        market: {odds_home, odds_draw, odds_away, odds_source, odds_fresh}
        player: {}
        environment: {}
        general: {}
    """
    aliases = team_aliases or {}
    home_alias = aliases.get(match.home.name)
    away_alias = aliases.get(match.away.name)

    raw: dict = {
        "team": {}, "market": {},
        "player": {}, "environment": {}, "general": {},
    }

    try:
        results = asyncio.run(asyncio.gather(
            fetch_team_elo(match.home.name, scope=elo_scope, alias=home_alias),
            fetch_team_elo(match.away.name, scope=elo_scope, alias=away_alias),
            fetch_match_odds(match.home.name, match.away.name),
            return_exceptions=True,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch raw match data: %s", exc)
        return raw

    elo_home_raw, elo_away_raw, odds = results

    if isinstance(elo_home_raw, dict):
        raw["team"]["elo_home"] = elo_home_raw.get("elo_rating")
    elif isinstance(elo_home_raw, BaseException):
        logger.warning("Elo fetch failed for %s: %s", match.home.name, elo_home_raw)

    if isinstance(elo_away_raw, dict):
        raw["team"]["elo_away"] = elo_away_raw.get("elo_rating")
    elif isinstance(elo_away_raw, BaseException):
        logger.warning("Elo fetch failed for %s: %s", match.away.name, elo_away_raw)

    if isinstance(odds, dict) and odds:
        raw["market"]["odds_home"] = odds.get("home")
        raw["market"]["odds_draw"] = odds.get("draw")
        raw["market"]["odds_away"] = odds.get("away")
        raw["market"]["odds_source"] = odds.get("source")
        raw["market"]["odds_fresh"] = not odds.get("stale", True)
    elif isinstance(odds, BaseException):
        logger.warning("Odds fetch failed: %s", odds)

    return raw


def query_fixture(match_id: str, model_cls) -> Any | None:
    """Query a fixture by match_id from the kernel DB.

    model_cls: KernelMatchFixture (for UCL/EPL) — extensible to other models.

    Returns the fixture object or None.
    """
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query fixture %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def query_result(match_id: str, model_cls) -> Any | None:
    """Query a match result by match_id from the kernel DB."""
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query result %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def build_match_identity(
    fixture: Any,
    competition: CompetitionIdentity,
    season_key: str,
    default_stage: str = "group_stage",
) -> MatchIdentity:
    """Build MatchIdentity from a KernelMatchFixture row.

    Handles None values, provides defaults for missing fields.
    """
    home = TeamIdentity(
        code=(fixture.home_team or "HOME")[:3].upper(),
        name=fixture.home_team or "Home",
        competition=competition,
    )
    away = TeamIdentity(
        code=(fixture.away_team or "AWAY")[:3].upper(),
        name=fixture.away_team or "Away",
        competition=competition,
    )
    return MatchIdentity(
        match_id=fixture.match_id,
        season=SeasonIdentity(competition=competition, season_key=season_key),
        stage=fixture.stage or default_stage,
        round=None,
        home=home,
        away=away,
        kickoff_utc=fixture.kickoff_utc or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def build_match_outcome(result: Any) -> MatchOutcome | None:
    """Build MatchOutcome from a KernelMatchResult row."""
    if result is None:
        return None
    return MatchOutcome(
        match_id=result.match_id,
        home_score=result.home_score,
        away_score=result.away_score,
        outcome=result.outcome,
        finished_at=result.finished_at or datetime.now(timezone.utc),
    )
```

### 5.2 `UCLAdapter`

```python
# backend/app/sports/football/adapters/ucl_adapter.py

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.sports.football.adapters._shared import (
    fetch_elo_and_odds, query_fixture, query_result,
    build_match_identity, build_match_outcome,
)
from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult

_FOOTBALL = SportIdentity(code="football", name="Football")
_COMPETITION = CompetitionIdentity(
    code="ucl", name="UEFA Champions League", sport=_FOOTBALL
)
_DEFAULT_SEASON = "2025-26"
_DEFAULT_STAGE = "group_stage"

_STAGE_MAP = {
    "GROUP_STAGE": "group_stage",
    "ROUND_OF_16": "round_of_16",
    "QUARTER_FINALS": "quarterfinal",
    "SEMI_FINALS": "semifinal",
    "FINAL": "final",
}

_MATCH_ID_PREFIX = "ucl-"
_FD_COMPETITION = "CL"

# Football-Data.org name → ClubElo.com name
_TEAM_ALIASES = {
    "Real Madrid CF": "RealMadrid",
    "Manchester City FC": "ManCity",
    "FC Bayern München": "BayernMunich",
    "Arsenal FC": "Arsenal",
    # ... (full list in implementation)
}


class UCLAdapter:
    """DataAdapter Protocol implementation for UEFA Champions League."""

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id, KernelMatchFixture)
        if fixture is None:
            return _stub_identity(match_id)
        return build_match_identity(
            fixture, _COMPETITION, _DEFAULT_SEASON, _DEFAULT_STAGE
        )

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        return fetch_elo_and_odds(
            match, elo_scope="club", team_aliases=_TEAM_ALIASES
        )

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        try:
            from app.services.football_data_client import (
                fetch_competition_fixtures, parse_fixture,
            )
            fixtures_raw = fetch_competition_fixtures(
                _FD_COMPETITION, season=2025
            )
            count = 0
            for raw in fixtures_raw:
                parsed = parse_fixture(
                    raw, stage_mapping=_STAGE_MAP,
                    match_id_prefix=_MATCH_ID_PREFIX,
                )
                if parsed:
                    _save_fixture(parsed, "ucl", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync UCL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        # Query kernel_match_fixtures filtered by competition="ucl"
        ...

    def fetch_team_data(self, team) -> dict: return {}
    def fetch_player_data(self, team) -> dict: return {}
    def fetch_market_data(self, match) -> dict: return {}
```

### 5.3 `EPLAdapter`

Structurally identical to `UCLAdapter` with different constants:

| Constant | UCL | EPL |
|----------|-----|-----|
| `_COMPETITION.code` | `"ucl"` | `"epl"` |
| `_COMPETITION.name` | `"UEFA Champions League"` | `"English Premier League"` |
| `_FD_COMPETITION` | `"CL"` | `"PL"` |
| `_MATCH_ID_PREFIX` | `"ucl-"` | `"epl-"` |
| `_STAGE_MAP` | 5 knockout stages | Empty (all → `regular_season`) |
| `_DEFAULT_STAGE` | `"group_stage"` | `"regular_season"` |
| `_TEAM_ALIASES` | UCL clubs | EPL clubs (20 teams) |

### 5.4 `MultiAdapter`

A proxy that dispatches to the correct adapter based on match_id prefix.

```python
# backend/app/sports/football/adapters/multi_adapter.py

class MultiAdapter:
    """DataAdapter Protocol proxy — dispatches by match_id prefix.

    Implements DataAdapter Protocol transparently. The PredictionKernel
    sees a single adapter; internally, calls are routed to the correct
    league adapter based on the match_id prefix.

    Prefix mapping:
        "wc-"  → WorldCupAdapter
        "ucl-" → UCLAdapter
        "epl-" → EPLAdapter

    Unknown prefixes fall back to WorldCupAdapter (backward compatibility).
    """

    def __init__(self, adapters: dict[str, DataAdapter]) -> None:
        self._adapters = adapters
        self._default = adapters.get("wc") or next(iter(adapters.values()))

    def _select(self, match_id: str) -> DataAdapter:
        for prefix, adapter in self._adapters.items():
            if match_id.startswith(prefix):
                return adapter
        return self._default

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        return self._select(match_id).get_match_identity(match_id)

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        return self._select(match.match_id).fetch_all_data(match)

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        return self._select(match_id).fetch_outcome(match_id)

    def sync_schedule(self) -> int:
        # Syncs all registered adapters
        return sum(adapter.sync_schedule() for adapter in self._adapters.values())

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        results = []
        for adapter in self._adapters.values():
            results.extend(adapter.fetch_schedule(filters))
        return results

    def fetch_team_data(self, team) -> dict:
        return self._default.fetch_team_data(team)

    def fetch_player_data(self, team) -> dict:
        return self._default.fetch_player_data(team)

    def fetch_market_data(self, match) -> dict:
        return self._select(match.match_id).fetch_market_data(match)
```

---

## 6. Database Schema

### 6.1 New Tables (in `kernel_db.py`)

#### `kernel_match_fixtures`

| Column | Type | Notes |
|--------|------|-------|
| `match_id` | String (PK) | e.g., `"ucl-537327"`, `"epl-123456"` |
| `competition` | String, not null | `"ucl"` or `"epl"` |
| `season` | String, not null | `"2025-26"` |
| `home_team` | String, not null | |
| `away_team` | String, not null | |
| `kickoff_utc` | DateTime | |
| `stage` | String | `"group_stage"`, `"round_of_16"`, `"regular_season"`, etc. |
| `status` | String | `"scheduled"`, `"in_play"`, `"finished"`, etc. |
| `home_score` | Integer | Nullable (only for finished matches) |
| `away_score` | Integer | Nullable |
| `venue` | String | |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

#### `kernel_match_results`

| Column | Type | Notes |
|--------|------|-------|
| `match_id` | String (PK) | FK to `kernel_match_fixtures.match_id` |
| `home_score` | Integer | |
| `away_score` | Integer | |
| `outcome` | String | `"home_win"`, `"draw"`, `"away_win"` |
| `finished_at` | DateTime | |
| `created_at` | DateTime | |

#### `kernel_club_elo_cache`

| Column | Type | Notes |
|--------|------|-------|
| `team_name` | String (PK) | Normalized (lowercase, no spaces) |
| `elo_rating` | Float | |
| `source` | String | `"clubelo"` |
| `fetched_at` | DateTime | For TTL check |
| `country` | String | 3-letter code (ENG, ESP, etc.) |
| `level` | Integer | League level (1 = top tier) |

### 6.2 Table Creation

Tables are created by `init_kernel_db()` (existing function, extended). Uses `KernelBase.metadata.create_all()` — idempotent, safe on existing databases.

### 6.3 Fixture/Result Persistence

`_save_fixture()` and `_save_result()` helper functions (in `_shared.py`) perform upserts into `kernel_match_fixtures` and `kernel_match_results`. Called by `sync_schedule()` during fixture import and by `fetch_outcome()` during result backfill.

---

## 7. API Route Updates

### 7.1 `predictions.py` — `_get_kernel()` modification

```python
def _get_kernel():
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, ...)

    if not hasattr(_get_kernel, "_instance"):
        init_kernel_db()
        reg = EngineRegistry()
        reg.register(EloOddsEngine())

        adapters: dict[str, DataAdapter] = {
            "wc": WorldCupAdapter(),
        }

        if config.settings.PHASE2_LEAGUES_ENABLED:
            from app.sports.football.adapters.ucl_adapter import UCLAdapter
            from app.sports.football.adapters.epl_adapter import EPLAdapter
            adapters["ucl"] = UCLAdapter()
            adapters["epl"] = EPLAdapter()

        multi = MultiAdapter(adapters)

        _get_kernel._instance = PredictionKernel(
            adapter=multi,
            feature_builder=FootballFeatureBuilder(),
            engine_registry=reg,
            factor_registry=FactorRegistry(),
            feature_registry=FeatureRegistry(),
            learning=KernelLearningService(),
        )
    return _get_kernel._instance
```

### 7.2 Feature Flag Gating

| Flag | Default | Effect when false |
|------|---------|-------------------|
| `KERNEL_PREDICTION_ENABLED` | `false` | All `/api/predictions/*` return 503 |
| `PHASE2_LEAGUES_ENABLED` | `false` | Only `wc-` prefix match_ids work; `ucl-`/`epl-` fall back to WorldCupAdapter (returns stub identity, prediction works but with no real data) |

When both flags are `true`, all three leagues are fully operational.

---

## 8. Configuration

### New settings in `config.py`

```python
# Phase 2 feature flag
PHASE2_LEAGUES_ENABLED: bool = _env_bool("PHASE2_LEAGUES_ENABLED", "false")

# ClubElo service
CLUB_ELO_CACHE_TTL_DAYS: int = int(os.getenv("CLUB_ELO_CACHE_TTL_DAYS", "7"))
CLUB_ELO_REQUEST_INTERVAL: float = float(os.getenv("CLUB_ELO_REQUEST_INTERVAL", "1.0"))
```

### `.env.example` additions

```env
# Phase 2 — Multi-league support
PHASE2_LEAGUES_ENABLED=false
CLUB_ELO_CACHE_TTL_DAYS=7
CLUB_ELO_REQUEST_INTERVAL=1.0
```

---

## 9. Team Name Alias System

Football-Data.org and ClubElo.com use different naming conventions. Each Adapter maintains a `_TEAM_ALIASES` dict that maps its data source's team names to ClubElo.com's URL-safe names (spaces removed).

**Matching algorithm in `club_elo_service.get_club_elo()`:**
1. Normalize input: lowercase, remove spaces, remove common suffixes (FC, CF, AC, AFC)
2. Check alias map (passed by adapter)
3. If no alias, try direct lookup with normalized name
4. If still no match, fetch today's snapshot CSV and search by normalized prefix match
5. Return `None` if no match found

**Initial alias coverage:**
- UCL 2025-26: 36 clubs (group stage participants + qualified teams)
- EPL 2025-26: 20 clubs
- Total: ~50 clubs (some overlap — UCL participants include EPL clubs)

Aliases are defined as constants in `ucl_adapter.py` and `epl_adapter.py`. Unknown teams fall through with their original name; the matching algorithm in `club_elo_service` handles normalization.

---

## 10. Testing Strategy

### 10.1 Test Files

| File | Tests | Description |
|------|-------|-------------|
| `test_football_data_client.py` | 8-10 | `fetch_competition_fixtures` with mocked HTTP; `parse_fixture` with various stage_mappings; API key missing error; 429 retry |
| `test_club_elo_service.py` | 8-10 | CSV parsing; team name normalization; cache hit/miss/TTL expiry; graceful degradation on network failure; snapshot filtering by country |
| `test_adapter_shared.py` | 6-8 | `fetch_team_elo` national vs club scope; `fetch_elo_and_odds` consolidation; `query_fixture`/`query_result`; `build_match_identity`/`build_match_outcome` |
| `test_ucl_adapter.py` | 8-10 | Protocol compliance (`isinstance(adapter, DataAdapter)`); `fetch_all_data` with mocked Elo+odds; `sync_schedule` with mocked client; stage mapping correctness; stub identity |
| `test_epl_adapter.py` | 6-8 | Protocol compliance; all stages → `regular_season`; `fetch_all_data`; team aliases |
| `test_multi_adapter.py` | 6-8 | Prefix dispatch (`wc-`/`ucl-`/`epl-`); unknown prefix fallback; `sync_schedule` aggregates; Protocol compliance |
| `test_kernel_db_fixtures.py` | 5-6 | `KernelMatchFixture` CRUD; `KernelMatchResult` CRUD; `KernelClubEloCache` TTL; competition filter |

### 10.2 Regression

Existing Phase 1 tests must continue to pass:
- `test_kernel_domain.py` (14 tests)
- `test_kernel_protocols.py` (7 tests)
- `test_kernel_btd_model.py` (11 tests)
- `test_kernel_elo_odds_engine.py` (15 tests)
- `test_kernel_engine_registry.py` (6 tests)
- `test_kernel_feature_registry.py` (5 tests)
- `test_kernel_factor_registry.py` (7 tests)
- `test_kernel_learning_service.py` (7 tests)
- `test_kernel_prediction_kernel.py` (5 tests)
- `test_kernel_world_cup_adapter.py` (11 tests)
- `test_predictions_route.py` (5 tests)

### 10.3 Equivalence Principle

All league adapters must produce `fetch_all_data()` output with identical dict structure:
```python
{
    "team": {"elo_home": float|None, "elo_away": float|None},
    "market": {"odds_home": float|None, ...},
    "player": {},
    "environment": {},
    "general": {},
}
```
This ensures `FootballFeatureBuilder` and `EloOddsEngine` remain league-agnostic.

---

## 11. Constraints

### Hard Constraints (inherited from project memory)

1. Prediction Kernel (`backend/app/kernel/`) must NOT import `world_cup_*`, `epl_*`, or `ucl_*` modules — all interaction via Protocol interfaces
2. `WorldCupAdapter` must NOT be modified during Phase 2
3. `FootballFeatureBuilder` must NOT be modified during Phase 2
4. Existing `football_data_source.py` must NOT be modified or deleted
5. `PHASE2_LEAGUES_ENABLED` feature flag must default to OFF
6. New database tables use `kernel_` prefix
7. Frontend pages must NOT be modified during Phase 2
8. External API tokens default empty; services degrade gracefully when unconfigured

### Phase 2-Specific Constraints

9. `football_data_client.py` must NOT import `world_cup_*` modules — it is a generic client
10. `club_elo_service.py` must NOT depend on `football_data_source.py` — independent data source
11. `_shared.py` functions must be stateless pure functions — no class, no module-level mutable state
12. `MultiAdapter` must implement `DataAdapter` Protocol — Kernel is unaware of multi-league dispatch
13. Team name aliases are defined per-adapter, not in `_shared.py` or `club_elo_service.py`

---

## 12. Future Extensions

### Phase 2 Follow-up (La Liga, Bundesliga, Serie A, Ligue 1)

Each new league requires:
1. New `{league}_adapter.py` with config constants (competition code, stage map, team aliases)
2. Register in `MultiAdapter` config in `predictions.py`
3. No new data source services needed (Football-Data.org and ClubElo.com already cover these leagues)

Estimated effort per league: 1 Adapter file + 1 test file + 1 config line = ~2 hours.

### Phase 3 Dependency

Phase 2 establishes the multi-league data pipeline. Phase 3 (unified learning loop) will:
- Use `competition` column in `kernel_predictions`/`kernel_match_outcomes` to compute per-league engine scores
- Apply `FactorRegistry` competition-specific weights (e.g., Elo weight 0.30 for World Cup, 0.40 for EPL)
- Run calibration across all competitions

### Phase 4 Dependency

Phase 4 (NBA) will follow the same pattern:
1. New `BasketballFeatureBuilder` (sport-specific, uses `FeatureSet.custom` for Pace, Gold Diff)
2. New `NBAAdapter` (calls basketball data sources)
3. Register in `MultiAdapter` with `"nba-"` prefix
4. No Kernel changes needed
