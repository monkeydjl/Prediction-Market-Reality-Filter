# Sports Prediction OS — Phase 2b Design: La Liga + Bundesliga + Serie A + Ligue 1 League Extension

**Date:** 2026-07-14
**Status:** Draft
**Depends on:** Phase 2 (`backend/app/sports/football/adapters/`, `backend/app/services/football_data_client.py`, `backend/app/services/club_elo_service.py`)
**Predecessor:** `docs/superpowers/specs/2026-07-14-sports-prediction-os-phase2-design.md`

---

## 1. Goal

Extend the Prediction Kernel to support **La Liga**, **Bundesliga**, **Serie A**, and **Ligue 1**, completing the European Top 5 leagues coverage. This increases match coverage from ~500 matches/year (UCL + EPL) to ~1900 matches/year (+ La Liga ~380 + Bundesliga ~306 + Serie A ~380 + Ligue 1 ~306).

Phase 2b introduces a **config-driven `LeagueAdapter`** with a `LeagueConfig` dataclass and `LEAGUE_REGISTRY`, establishing a pattern where new leagues are added with a single config entry — no new adapter class needed.

### Success Criteria

1. `/api/predictions/matches/laliga-{id}/predict` returns a valid `PredictionResult` for La Liga matches
2. `/api/predictions/matches/bundesliga-{id}/predict` returns a valid `PredictionResult` for Bundesliga matches
3. `/api/predictions/matches/seriea-{id}/predict` returns a valid `PredictionResult` for Serie A matches
4. `/api/predictions/matches/ligue1-{id}/predict` returns a valid `PredictionResult` for Ligue 1 matches
5. Existing World Cup, UCL, and EPL predictions continue to work unchanged
6. `_shared.py`, `multi_adapter.py`, `world_cup_adapter.py`, `ucl_adapter.py`, `epl_adapter.py` have zero modifications
7. Kernel code has zero modifications
8. `football_data_source.py` has zero modifications
9. All new tests pass; all Phase 1 + Phase 2 tests pass (no regression)

---

## 2. Scope

### In Scope

- `LeagueConfig` frozen dataclass encapsulating league-specific constants
- `LeagueAdapter` class implementing `DataAdapter` Protocol, driven by `LeagueConfig`
- `LEAGUE_REGISTRY` dict mapping match_id prefixes to `LeagueConfig` instances
- Team name alias dictionaries for 4 leagues (~76 clubs total)
- API route update in `_get_kernel()` to loop-register new adapters from registry
- Test suite for all new code

### Out of Scope

- Refactoring UCLAdapter or EPLAdapter to use `LeagueAdapter` (they remain as-is for zero-risk)
- Frontend changes (Phase 2b is backend-only)
- Calibration and weight updates (Phase 3)
- NBA / basketball (Phase 4)
- Modifying existing `_shared.py`, `multi_adapter.py`, or any Phase 2 adapter

---

## 3. Architecture

### 3.1 Module Layout

```
backend/app/sports/football/adapters/
├── _shared.py               # UNCHANGED — stateless utility functions
├── world_cup_adapter.py      # UNCHANGED — Phase 1 legacy
├── ucl_adapter.py            # UNCHANGED — Phase 2
├── epl_adapter.py            # UNCHANGED — Phase 2
├── multi_adapter.py          # UNCHANGED — prefix-dispatch proxy
└── league_adapter.py         # NEW — LeagueConfig + LeagueAdapter + LEAGUE_REGISTRY

backend/app/api/routes/
└── predictions.py            # MODIFIED — loop-register adapters from LEAGUE_REGISTRY (~4 lines)

backend/tests/
└── test_league_adapter.py    # NEW — all tests for LeagueAdapter + LeagueConfig + registry
```

### 3.2 Data Flow

```
API Route → MultiAdapter (dispatch by match_id prefix)
  ├─ "wc-"      → WorldCupAdapter (Phase 1, unchanged)
  ├─ "ucl-"     → UCLAdapter (Phase 2, unchanged)
  ├─ "epl-"     → EPLAdapter (Phase 2, unchanged)
  ├─ "laliga-"  → LeagueAdapter(LEAGUE_REGISTRY["laliga-"])
  ├─ "bundesliga-" → LeagueAdapter(LEAGUE_REGISTRY["bundesliga-"])
  ├─ "seriea-"  → LeagueAdapter(LEAGUE_REGISTRY["seriea-"])
  └─ "ligue1-"  → LeagueAdapter(LEAGUE_REGISTRY["ligue1-"])
                    ↓
         _shared.py functions (unchanged)
                    ↓
         FootballFeatureBuilder.build(match, raw)  → FeatureSet
                    ↓
         EloOddsEngine.predict(features, match)    → PredictionResult
```

### 3.3 Design Principles

1. **Config-driven, not copy-paste**: A single `LeagueAdapter` class serves all 4 new leagues. Adding a 5th league (e.g., Eredivisie) requires only a new `LeagueConfig` entry in `LEAGUE_REGISTRY`.
2. **Zero disruption to Phase 1/2**: UCLAdapter and EPLAdapter are not refactored to use `LeagueAdapter`. They remain as-is, eliminating any regression risk.
3. **Composition, not inheritance**: `LeagueAdapter` delegates to `_shared.py` functions, exactly like the existing adapters. No base class hierarchy.
4. **Protocol-transparent**: `LeagueAdapter` implements `DataAdapter` Protocol; `MultiAdapter` and the Kernel see a single adapter interface.

---

## 4. LeagueConfig

### 4.1 Dataclass Definition

```python
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class LeagueConfig:
    """Configuration for a league-specific adapter.

    Encapsulates all league-specific constants so that a single
    LeagueAdapter class can serve any league.
    """
    code: str                    # Internal competition code (e.g., "laliga")
    name: str                    # Display name (e.g., "La Liga")
    match_id_prefix: str         # Prefix for match IDs (e.g., "laliga-")
    fd_competition: str          # Football-Data.org competition code (e.g., "PD")
    fd_season: int               # Football-Data.org season year (e.g., 2025)
    default_season: str          # Internal season key (e.g., "2025-26")
    default_stage: str           # Default stage (e.g., "regular_season")
    default_kickoff: datetime    # Default kickoff for stub identity
    stage_map: dict[str, str]    # Stage mapping (empty for league-format)
    team_aliases: dict[str, str] # FD team name → ClubElo lookup name
```

**Note on frozen + dict fields:** `dict` and `list` fields in a `frozen=True` dataclass are shallow-immutable — the reference cannot be reassigned, but the contents are technically mutable. This matches the existing pattern in Phase 1's `FeatureSet` (which has `dict`/`list` fields with `frozen=True`). The spec accepts this shallow immutability as consistent with the codebase convention.

### 4.2 League Configurations

#### La Liga (PD)

```python
LeagueConfig(
    code="laliga",
    name="La Liga",
    match_id_prefix="laliga-",
    fd_competition="PD",
    fd_season=2025,
    default_season="2025-26",
    default_stage="regular_season",
    default_kickoff=datetime(2025, 8, 16, 20, 0, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "Real Madrid CF": "RealMadrid",
        "FC Barcelona": "Barcelona",
        "Atlético de Madrid": "AtleticoMadrid",
        "Athletic Club": "AthleticBilbao",
        "Real Sociedad": "RealSociedad",
        "Villarreal CF": "Villarreal",
        "Real Betis": "RealBetis",
        "Valencia CF": "Valencia",
        "Sevilla FC": "Sevilla",
        "Girona FC": "Girona",
        "Rayo Vallecano": "RayoVallecano",
        "Getafe CF": "Getafe",
        "CA Osasuna": "Osasuna",
        "Celta de Vigo": "CeltaVigo",
        "RCD Mallorca": "Mallorca",
        "UD Las Palmas": "LasPalmas",
        "Deportivo Alavés": "Alaves",
        "RCD Espanyol": "Espanyol",
        "CD Leganés": "Leganes",
        "Valladolid CF": "Valladolid",
    },
)
```

#### Bundesliga (BL1)

```python
LeagueConfig(
    code="bundesliga",
    name="Bundesliga",
    match_id_prefix="bundesliga-",
    fd_competition="BL1",
    fd_season=2025,
    default_season="2025-26",
    default_stage="regular_season",
    default_kickoff=datetime(2025, 8, 23, 15, 30, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "FC Bayern München": "BayernMunich",
        "Borussia Dortmund": "Dortmund",
        "Bayer 04 Leverkusen": "Leverkusen",
        "RB Leipzig": "RBLeipzig",
        "Eintracht Frankfurt": "Frankfurt",
        "VfL Wolfsburg": "Wolfsburg",
        "SC Freiburg": "Freiburg",
        "1. FSV Mainz 05": "Mainz",
        "VfB Stuttgart": "Stuttgart",
        "FC Augsburg": "Augsburg",
        "Borussia Mönchengladbach": "Mönchengladbach",
        "SV Werder Bremen": "WerderBremen",
        "TSG 1899 Hoffenheim": "Hoffenheim",
        "1. FC Union Berlin": "UnionBerlin",
        "VfL Bochum": "Bochum",
        "FC St. Pauli": "StPauli",
        "Holstein Kiel": "HolsteinKiel",
        "1. FC Heidenheim 1846": "Heidenheim",
    },
)
```

#### Serie A (SA)

```python
LeagueConfig(
    code="seriea",
    name="Serie A",
    match_id_prefix="seriea-",
    fd_competition="SA",
    fd_season=2025,
    default_season="2025-26",
    default_stage="regular_season",
    default_kickoff=datetime(2025, 8, 17, 18, 0, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "FC Internazionale Milano": "Inter",
        "AC Milan": "Milan",
        "Juventus FC": "Juventus",
        "SSC Napoli": "Napoli",
        "AS Roma": "Roma",
        "Atalanta BC": "Atalanta",
        "ACF Fiorentina": "Fiorentina",
        "SS Lazio": "Lazio",
        "Bologna FC 1909": "Bologna",
        "Torino FC": "Torino",
        "Udinese Calcio": "Udinese",
        "US Sassuolo": "Sassuolo",
        "Genoa CFC": "Genoa",
        "US Lecce": "Lecce",
        "Cagliari Calcio": "Cagliari",
        "Hellas Verona": "Verona",
        "Empoli FC": "Empoli",
        "Parma Calcio 1913": "Parma",
        "Como 1907": "Como",
        "Venezia FC": "Venezia",
    },
)
```

#### Ligue 1 (FL1)

```python
LeagueConfig(
    code="ligue1",
    name="Ligue 1",
    match_id_prefix="ligue1-",
    fd_competition="FL1",
    fd_season=2025,
    default_season="2025-26",
    default_stage="regular_season",
    default_kickoff=datetime(2025, 8, 17, 19, 0, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "Paris Saint-Germain": "ParisSG",
        "AS Monaco": "Monaco",
        "Olympique de Marseille": "Marseille",
        "Olympique Lyonnais": "Lyon",
        "LOSC Lille": "Lille",
        "OGC Nice": "Nice",
        "Stade Rennais FC": "Rennes",
        "RC Lens": "Lens",
        "Stade Brestois 29": "Brest",
        "Toulouse FC": "Toulouse",
        "Montpellier HSC": "Montpellier",
        "FC Nantes": "Nantes",
        "Stade de Reims": "Reims",
        "RC Strasbourg Alsace": "Strasbourg",
        "Le Havre AC": "LeHavre",
        "AJ Auxerre": "Auxerre",
        "Angers SCO": "Angers",
        "AS Saint-Étienne": "Saint-Etienne",
    },
)
```

---

## 5. LeagueAdapter

### 5.1 Class Structure

`LeagueAdapter` accepts a `LeagueConfig` and implements all 8 `DataAdapter` Protocol methods. The logic is identical to `EPLAdapter` — the only difference is that constants are read from `self._config` instead of module-level variables.

```python
class LeagueAdapter:
    """DataAdapter implementation for league-format football competitions.

    Driven by LeagueConfig — a single class serves all league-format
    competitions (La Liga, Bundesliga, Serie A, Ligue 1, and any future
    league added to LEAGUE_REGISTRY).
    """

    def __init__(self, config: LeagueConfig) -> None:
        self._config = config
```

### 5.2 Method Implementations

All methods delegate to `_shared.py` functions, using `self._config` for constants:

| Method | Implementation |
|--------|---------------|
| `get_match_identity(match_id)` | `query_fixture(match_id, KernelMatchFixture)` → `build_match_identity(fixture, self._config.code, self._config.default_season, self._config.default_stage)` |
| `fetch_all_data(match)` | `fetch_elo_and_odds(match, elo_scope="club", team_aliases=self._config.team_aliases)` |
| `fetch_outcome(match_id)` | `query_result(match_id, KernelMatchResult)` → `build_match_outcome(result)` |
| `sync_schedule(filter)` | `fetch_competition_fixtures(self._config.fd_competition, self._config.fd_season)` → `parse_fixture(raw, self._config.stage_map, self._config.match_id_prefix)` → `save_fixture(parsed, self._config.code, self._config.default_season)` |
| `fetch_schedule(filter)` | Query `KernelMatchFixture` where `competition == self._config.code` |
| `fetch_team_data(team_name)` | Return `{}` (stub, same as EPL) |
| `fetch_player_data(match_id)` | Return `{}` (stub, same as EPL) |
| `fetch_market_data(match_id)` | Return `{}` (stub, same as EPL) |

### 5.3 Module-level Singleton

Following the pattern of existing adapters, `LEAGUE_REGISTRY` holds the 4 `LeagueConfig` instances. Adapters are constructed on-demand in `_get_kernel()`:

```python
LEAGUE_REGISTRY: dict[str, LeagueConfig] = {
    "laliga-":     _LALIGA_CONFIG,
    "bundesliga-": _BUNDESLIGA_CONFIG,
    "seriea-":     _SERIEA_CONFIG,
    "ligue1-":     _LIGUE1_CONFIG,
}
```

---

## 6. API Route Integration

### 6.1 `_get_kernel()` Modification

In `backend/app/api/routes/predictions.py`, inside the existing `if config.settings.PHASE2_LEAGUES_ENABLED:` block, append:

```python
from app.sports.football.adapters.league_adapter import LEAGUE_REGISTRY, LeagueAdapter
for prefix, cfg in LEAGUE_REGISTRY.items():
    adapters[prefix] = LeagueAdapter(cfg)
```

This adds 4 new adapters to the `MultiAdapter` dictionary. The existing `wc-`, `ucl-`, and `epl-` registrations remain unchanged.

### 6.2 No New Feature Flag

The existing `PHASE2_LEAGUES_ENABLED` flag controls all Phase 2 leagues (UCL, EPL, and now La Liga, Bundesliga, Serie A, Ligue 1). No new flag is introduced.

---

## 7. Team Name Alias Strategy

### 7.1 Alias Construction

Aliases map Football-Data.org team names to ClubElo.com URL-safe names (spaces removed, as established in Phase 2).

**Construction approach:**
1. Call `fetch_club_elo_snapshot()` to get the full ClubElo ranking CSV
2. Filter by Country: ESP (La Liga), GER (Bundesliga), ITA (Serie A), FRA (Ligue 1)
3. Filter by Level=1 (top-tier league)
4. For each club, normalize with `_normalize_team_name()` and match against Football-Data.org names
5. Manually verify and correct mismatches (e.g., "Athletic Club" → "AthleticBilbao" on ClubElo)

### 7.2 Alias Maintenance

Aliases are defined as `dict[str, str]` in each `LeagueConfig`. They are static — club names rarely change between seasons. When a team is promoted/relegated, the alias dict is updated in the next deployment.

**Note:** The aliases in Section 4.2 are best-effort initial values based on known ClubElo.com naming conventions (English names, spaces removed). During implementation, aliases should be verified against the actual ClubElo CSV by calling `fetch_club_elo_snapshot()` and checking that each alias, when passed through `_normalize_team_name()`, matches a CSV "Club" entry. Any mismatches are corrected before commit.

---

## 8. Testing Strategy

### 8.1 Test File

`backend/tests/test_league_adapter.py`

### 8.2 Test Classes

| Class | Tests | Description |
|-------|-------|-------------|
| `TestLeagueConfig` | 5 | Frozen immutability, 4 configs have correct codes/prefixes/FD codes, stage_map is empty |
| `TestLeagueAdapterProtocol` | 1 | `isinstance(LeagueAdapter(cfg), DataAdapter)` is `True` |
| `TestLeagueAdapterGetMatchIdentity` | 2 | Builds identity from fixture; returns stub when not found |
| `TestLeagueAdapterFetchAllData` | 2 | Calls `fetch_elo_and_odds` with correct scope and aliases |
| `TestLeagueAdapterFetchOutcome` | 2 | Builds outcome from result; returns None when not found |
| `TestLeagueAdapterSyncSchedule` | 2 | Calls `fetch_competition_fixtures` with correct FD code; returns 0 on failure |
| `TestLeagueAdapterStubMethods` | 3 | `fetch_team_data`/`fetch_player_data`/`fetch_market_data` return empty dicts |
| `TestLeagueRegistry` | 3 | 4 prefixes registered; each config has non-empty aliases; each FD code is unique |
| `TestRouteIntegration` | 2 | `_get_kernel()` with PHASE2 enabled registers 7 adapters total (wc + ucl + epl + 4 new) |

**Total: ~22 tests**

### 8.3 Mocking Strategy

- `fetch_competition_fixtures` and `parse_fixture` patched at module level (same pattern as Phase 2)
- `_shared.py` functions (`query_fixture`, `query_result`, `fetch_elo_and_odds`, `save_fixture`, `build_match_identity`, `build_match_outcome`) patched at module level
- `get_kernel_session` patched for DB-touching tests
- `TestClient(app)` used for route integration tests with `ALLOW_OPEN_WRITES=True` (same pattern as Phase 2 Task 8)

---

## 9. Constraints

1. `LeagueAdapter` must implement `DataAdapter` Protocol (verifiable via `isinstance`)
2. `_shared.py` must NOT be modified
3. `multi_adapter.py` must NOT be modified
4. `world_cup_adapter.py` must NOT be modified
5. `ucl_adapter.py` must NOT be modified
6. `epl_adapter.py` must NOT be modified
7. Kernel code must NOT be modified
8. `football_data_source.py` must NOT be modified
9. `LeagueConfig` must be `@dataclass(frozen=True)`
10. `PHASE2_LEAGUES_ENABLED` continues to gate all Phase 2 leagues — no new feature flag
11. All 4 new leagues use `stage_map={}` and `default_stage="regular_season"` (league format, no knockout)
12. Frontend pages must NOT be modified
13. New match_id prefixes: `laliga-`, `bundesliga-`, `seriea-`, `ligue1-`
14. No `world_cup_*` imports in any new file
15. `get_elo_rating` and `get_cached_odds` are async — must use `await` (inherited from `_shared.py`)

---

## 10. Future Extensions

### Adding a 6th League (e.g., Eredivisie)

1. Define a new `LeagueConfig` in `league_adapter.py`
2. Add one entry to `LEAGUE_REGISTRY`
3. No other changes needed — `_get_kernel()` loop picks it up automatically

### Migrating EPL to LeagueAdapter

In a future refactor, `EPLAdapter` could be replaced by `LeagueAdapter(EPL_CONFIG)` since EPL is also league-format. This is deferred to avoid any regression risk in Phase 2b.

### UCL Cannot Use LeagueAdapter

UCL has knockout stages (`_STAGE_MAP` with 5 entries) and a different default stage (`"group_stage"`). While `LeagueConfig` could technically support it (by populating `stage_map`), the current design scopes `LeagueAdapter` to league-format competitions only. UCL remains on its dedicated adapter.
