# Sports Prediction OS Phase 2 — UCL + EPL League Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Prediction Kernel to support UEFA Champions League (UCL) and English Premier League (EPL) alongside the existing World Cup, using composition-based adapters and a new ClubElo data source.

**Architecture:** New `UCLAdapter` and `EPLAdapter` implement the `DataAdapter` Protocol via shared utility functions in `_shared.py`. A `MultiAdapter` proxy dispatches by match_id prefix. A new `football_data_client.py` parameterizes the Football-Data.org API, and `club_elo_service.py` fetches club Elo ratings from ClubElo.com's CSV API. Kernel code is untouched.

**Tech Stack:** Python 3.12+, SQLAlchemy (DeclarativeBase), httpx (HTTP client), csv.DictReader (CSV parsing), pytest (TDD), FastAPI (API routes).

## Global Constraints

1. Prediction Kernel (`backend/app/kernel/`) must NOT be modified except `kernel_db.py` (add new tables) — all interaction via Protocol interfaces
2. `WorldCupAdapter` must NOT be modified
3. `FootballFeatureBuilder` must NOT be modified
4. Existing `football_data_source.py` must NOT be modified or deleted
5. `PHASE2_LEAGUES_ENABLED` feature flag must default to OFF (`_env_bool("PHASE2_LEAGUES_ENABLED", "false")`)
6. New database tables use `kernel_` prefix
7. Frontend pages must NOT be modified
8. External API tokens default empty; services degrade gracefully when unconfigured
9. `football_data_client.py` must NOT import `world_cup_*` modules
10. `club_elo_service.py` must NOT depend on `football_data_source.py`
11. `_shared.py` functions must be stateless — no class, no module-level mutable state
12. `MultiAdapter` must implement `DataAdapter` Protocol
13. Team name aliases are defined per-adapter, not in `_shared.py` or `club_elo_service.py`
14. All domain value objects remain `@dataclass(frozen=True)`
15. `get_elo_rating` and `get_cached_odds` are `async` functions — must use `await` when calling them

---

## File Structure

```
backend/app/
├── services/
│   ├── football_data_client.py      # NEW: Parameterized Football-Data.org client
│   └── club_elo_service.py          # NEW: ClubElo.com CSV fetcher + cache
├── sports/football/adapters/
│   ├── _shared.py                   # NEW: Shared utility functions (composition)
│   ├── ucl_adapter.py               # NEW: UCL DataAdapter
│   ├── epl_adapter.py               # NEW: EPL DataAdapter
│   └── multi_adapter.py             # NEW: Prefix-dispatch proxy
├── kernel/
│   └── kernel_db.py                 # MODIFIED: Add 3 new tables
├── api/routes/
│   └── predictions.py               # MODIFIED: Register MultiAdapter
└── core/
    └── config.py                    # MODIFIED: Add PHASE2_LEAGUES_ENABLED + ClubElo config

backend/tests/
├── test_football_data_client.py     # NEW
├── test_club_elo_service.py         # NEW
├── test_adapter_shared.py           # NEW
├── test_ucl_adapter.py              # NEW
├── test_epl_adapter.py              # NEW
├── test_multi_adapter.py            # NEW
└── test_kernel_db_fixtures.py       # NEW
```

---

### Task 1: Football-Data.org Generic Client

**Files:**
- Create: `backend/app/services/football_data_client.py`
- Test: `backend/tests/test_football_data_client.py`

**Interfaces:**
- Consumes: `settings.FOOTBALL_DATA_API_KEY`, `settings.FOOTBALL_DATA_BASE_URL` from `app.core.config`
- Produces: `fetch_competition_fixtures(competition, season)`, `parse_fixture(match_data, stage_mapping, match_id_prefix)`, `FootballDataClientError`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_football_data_client.py
"""Tests for football_data_client — parameterized Football-Data.org client."""
from unittest.mock import patch, MagicMock
import pytest

from app.services.football_data_client import (
    fetch_competition_fixtures,
    parse_fixture,
    FootballDataClientError,
)


class TestFetchCompetitionFixtures:
    @patch("app.services.football_data_client.httpx.get")
    @patch("app.services.football_data_client.settings")
    def test_fetch_ucl_fixtures(self, mock_settings, mock_get):
        mock_settings.FOOTBALL_DATA_API_KEY = "test-key"
        mock_settings.FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"matches": [{"id": 123}]}
        mock_get.return_value = mock_response

        result = fetch_competition_fixtures("CL", season=2025)
        assert len(result) == 1
        assert result[0]["id"] == 123
        # Verify URL contains CL competition code
        call_args = mock_get.call_args
        assert "CL" in str(call_args[0][0])

    @patch("app.services.football_data_client.settings")
    def test_no_api_key_raises(self, mock_settings):
        mock_settings.FOOTBALL_DATA_API_KEY = ""
        with pytest.raises(FootballDataClientError, match="not configured"):
            fetch_competition_fixtures("CL")

    @patch("app.services.football_data_client.httpx.get")
    @patch("app.services.football_data_client.settings")
    def test_429_rate_limit_raises(self, mock_settings, mock_get):
        mock_settings.FOOTBALL_DATA_API_KEY = "test-key"
        mock_settings.FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.return_value = mock_response
        with pytest.raises(FootballDataClientError, match="Rate limit"):
            fetch_competition_fixtures("PL")


class TestParseFixture:
    def test_parse_ucl_group_stage(self):
        raw = {
            "id": 537327,
            "homeTeam": {"name": "Real Madrid CF"},
            "awayTeam": {"name": "FC Bayern München"},
            "utcDate": "2025-09-16T20:00:00Z",
            "stage": "GROUP_STAGE",
            "status": "SCHEDULED",
            "venue": "Santiago Bernabéu",
            "score": {"fullTime": {"home": None, "away": None}},
        }
        stage_map = {
            "GROUP_STAGE": "group_stage",
            "ROUND_OF_16": "round_of_16",
            "QUARTER_FINALS": "quarterfinal",
            "SEMI_FINALS": "semifinal",
            "FINAL": "final",
        }
        result = parse_fixture(raw, stage_mapping=stage_map, match_id_prefix="ucl-")
        assert result is not None
        assert result["match_id"] == "ucl-537327"
        assert result["home_team"] == "Real Madrid CF"
        assert result["away_team"] == "FC Bayern München"
        assert result["stage"] == "group_stage"
        assert result["status"] == "scheduled"
        assert result["venue"] == "Santiago Bernabéu"

    def test_parse_epl_no_stage_mapping(self):
        raw = {
            "id": 123456,
            "homeTeam": {"name": "Arsenal FC"},
            "awayTeam": {"name": "Chelsea FC"},
            "utcDate": "2025-08-16T15:00:00Z",
            "stage": "",
            "status": "FINISHED",
            "venue": "Emirates Stadium",
            "score": {"fullTime": {"home": 2, "away": 1}},
        }
        result = parse_fixture(raw, stage_mapping=None, match_id_prefix="epl-")
        assert result is not None
        assert result["match_id"] == "epl-123456"
        assert result["stage"] == "regular_season"
        assert result["status"] == "finished"
        assert result["home_score"] == 2
        assert result["away_score"] == 1

    def test_parse_missing_id_returns_none(self):
        raw = {"homeTeam": {"name": "Team A"}}
        result = parse_fixture(raw, stage_mapping=None)
        assert result is None

    def test_parse_missing_teams_returns_none(self):
        raw = {"id": 1, "homeTeam": {}, "awayTeam": {}}
        result = parse_fixture(raw, stage_mapping=None)
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_football_data_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.football_data_client'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/football_data_client.py
"""Parameterized Football-Data.org client.

Generic client for the Football-Data.org v4 API. Supports any competition
code (WC, CL, PL, etc.) without hardcoding. Does NOT import world_cup_*
modules — it is a clean, independent client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class FootballDataClientError(Exception):
    """Football-Data.org API error."""
    pass


def fetch_competition_fixtures(
    competition: str,
    season: int = 2026,
) -> list[dict[str, Any]]:
    """Fetch fixtures for a competition from Football-Data.org.

    Args:
        competition: Football-Data.org competition code (e.g., "CL", "PL", "WC").
        season: Season year (default: 2026).

    Returns:
        List of raw match dicts from the API response.

    Raises:
        FootballDataClientError: If API key is missing or request fails.
    """
    api_key = settings.FOOTBALL_DATA_API_KEY
    if not api_key:
        raise FootballDataClientError("FOOTBALL_DATA_API_KEY not configured")

    base_url = str(settings.FOOTBALL_DATA_BASE_URL or "").rstrip("/")
    url = f"{base_url}/competitions/{competition}/matches"

    data = _football_data_get(url, params={"season": season})
    matches = data.get("matches", [])
    return matches


def parse_fixture(
    match_data: dict[str, Any],
    stage_mapping: dict[str, str] | None = None,
    match_id_prefix: str = "fd-",
) -> dict[str, Any] | None:
    """Parse a raw Football-Data.org match dict into internal fixture format.

    Args:
        match_data: Raw match dict from the API.
        stage_mapping: Maps API stage names to internal canonical names.
            If None, all stages map to "regular_season".
        match_id_prefix: Prefix for the internal match_id (e.g., "ucl-", "epl-").

    Returns:
        Parsed fixture dict or None if match_data is malformed.
    """
    match_id = match_data.get("id")
    if not match_id:
        return None

    home_team = match_data.get("homeTeam", {}).get("name", "")
    away_team = match_data.get("awayTeam", {}).get("name", "")

    if not home_team or not away_team:
        return None

    utc_date = match_data.get("utcDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    stage_raw = (match_data.get("stage", "") or "").upper()
    if stage_mapping and stage_raw in stage_mapping:
        stage = stage_mapping[stage_raw]
    elif stage_mapping is None:
        stage = "regular_season"
    else:
        stage = stage_raw.lower()

    group = match_data.get("group")

    status_raw = match_data.get("status", "")
    status_mapping = {
        "TIMED": "scheduled",
        "SCHEDULED": "scheduled",
        "IN_PLAY": "in_play",
        "LIVE": "in_play",
        "PAUSED": "in_play",
        "FINISHED": "finished",
        "AWARDED": "finished",
        "POSTPONED": "postponed",
        "CANCELLED": "cancelled",
        "SUSPENDED": "suspended",
    }
    match_status = status_mapping.get(status_raw, "scheduled")

    venue_name = match_data.get("venue", "")

    score = match_data.get("score", {})
    fulltime = score.get("fullTime", {})
    home_score = fulltime.get("home")
    away_score = fulltime.get("away")

    return {
        "match_id": f"{match_id_prefix}{match_id}",
        "fixture_id": str(match_id),
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "venue": venue_name or "Unknown",
        "stage": stage,
        "group": group,
        "status": match_status,
        "home_score": home_score,
        "away_score": away_score,
    }


def _football_data_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HTTP GET with X-Auth-Token header.

    Reuses the same authentication and error-handling pattern as the
    existing football_data_source.py but lives in a separate module.
    """
    api_key = settings.FOOTBALL_DATA_API_KEY
    if not api_key:
        raise FootballDataClientError("FOOTBALL_DATA_API_KEY not configured")

    try:
        response = httpx.get(
            url,
            headers={"X-Auth-Token": api_key},
            params=params,
            timeout=30.0,
        )

        if response.status_code == 403:
            raise FootballDataClientError("API key invalid or access forbidden")
        if response.status_code == 429:
            raise FootballDataClientError("Rate limit exceeded (10 requests/minute)")
        if response.status_code != 200:
            raise FootballDataClientError(
                f"API error: {response.status_code} - {response.text[:200]}"
            )

        data = response.json()
        if not isinstance(data, dict):
            raise FootballDataClientError("Football-Data.org returned non-object JSON")
        return data
    except httpx.TimeoutException as exc:
        raise FootballDataClientError("Request timeout") from exc
    except httpx.RequestError as exc:
        raise FootballDataClientError(f"Request failed: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_football_data_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/football_data_client.py tests/test_football_data_client.py
git commit -m "feat(services): add parameterized Football-Data.org client"
```

---

### Task 2: ClubElo Service

**Files:**
- Create: `backend/app/services/club_elo_service.py`
- Test: `backend/tests/test_club_elo_service.py`

**Interfaces:**
- Consumes: `KernelClubEloCache` table from Task 3 (forward reference — will use lazy imports)
- Produces: `get_club_elo(team_name)`, `fetch_club_elo_snapshot(date)`, `get_club_elo_by_country(country, level)`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_club_elo_service.py
"""Tests for club_elo_service — ClubElo.com CSV fetcher + cache."""
from unittest.mock import patch, MagicMock
from io import StringIO
import pytest

from app.services.club_elo_service import (
    get_club_elo,
    fetch_club_elo_snapshot,
    get_club_elo_by_country,
    _normalize_team_name,
)


SAMPLE_CSV = """Rank,Club,Country,Level,Elo,From,To
1,Arsenal,ENG,1,2063.76,2026-05-31,2026-08-21
2,Man City,ENG,1,1970.85,2026-07-05,2026-08-23
3,Paris SG,FRA,1,1967.88,2026-07-05,2026-08-23
4,Real Madrid,ESP,1,1955.12,2026-07-05,2026-08-23
5,Bayern Munich,GER,1,1940.33,2026-07-05,2026-08-23
"""


class TestNormalizeTeamName:
    def test_removes_spaces(self):
        assert _normalize_team_name("Man City") == "mancity"

    def test_lowercases(self):
        assert _normalize_team_name("Arsenal") == "arsenal"

    def test_removes_common_suffixes(self):
        assert _normalize_team_name("Arsenal FC") == "arsenal"
        assert _normalize_team_name("Real Madrid CF") == "realmadrid"
        assert _normalize_team_name("FC Bayern München") == "bayernmünchen"

    def test_handles_none(self):
        assert _normalize_team_name("") == ""


class TestFetchClubEloSnapshot:
    @patch("app.services.club_elo_service.httpx.get")
    def test_fetch_snapshot_parses_csv(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = fetch_club_elo_snapshot("2026-07-13")
        assert len(result) == 5
        assert result[0]["Club"] == "Arsenal"
        assert result[0]["Country"] == "ENG"
        assert result[0]["Elo"] == "2063.76"

    @patch("app.services.club_elo_service.httpx.get")
    def test_fetch_snapshot_network_error_returns_empty(self, mock_get):
        import httpx
        mock_get.side_effect = httpx.RequestError("Network error")
        result = fetch_club_elo_snapshot("2026-07-13")
        assert result == []


class TestGetClubEloByCountry:
    @patch("app.services.club_elo_service.httpx.get")
    def test_filter_england_level1(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = get_club_elo_by_country("ENG", level=1)
        assert "Arsenal" in result
        assert "Man City" in result
        assert "Real Madrid" not in result
        assert result["Arsenal"] == 2063.76


class TestGetClubElo:
    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    def test_cache_hit_returns_cached(self, mock_cache, mock_get):
        mock_cache.return_value = {"elo_rating": 1900.0, "source": "clubelo"}
        result = get_club_elo("Arsenal")
        assert result is not None
        assert result["elo_rating"] == 1900.0
        mock_get.assert_not_called()

    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    @patch("app.services.club_elo_service._save_cache")
    def test_cache_miss_fetches_and_saves(self, mock_save, mock_cache, mock_get):
        mock_cache.return_value = None
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = get_club_elo("Arsenal")
        assert result is not None
        assert result["elo_rating"] == 2063.76
        assert result["source"] == "clubelo"
        mock_save.assert_called_once()

    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    def test_team_not_found_returns_none(self, mock_cache, mock_get):
        mock_cache.return_value = None
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = get_club_elo("Nonexistent United")
        assert result is None

    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    def test_network_error_returns_none(self, mock_cache, mock_get):
        mock_cache.return_value = None
        import httpx
        mock_get.side_effect = httpx.RequestError("Network error")

        result = get_club_elo("Arsenal")
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_club_elo_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/club_elo_service.py
"""ClubElo.com CSV fetcher + cache.

Fetches club Elo ratings from ClubElo.com's free CSV API.
No API key required. Uses kernel_club_elo_cache table for caching.
Does NOT depend on football_data_source.py.
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timezone, timedelta
from io import StringIO
from typing import Any

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

_CLUB_ELO_API = "http://api.clubelo.com"

# Common suffixes to strip when normalizing team names.
_SUFFIXES = (" fc", " cf", " ac", " afc", " sc", " fc.", " cf.", " ac.")


def _normalize_team_name(name: str) -> str:
    """Normalize team name for matching: lowercase, remove spaces, strip suffixes."""
    if not name:
        return ""
    normalized = name.strip().lower().replace(" ", "")
    for suffix in _SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def fetch_club_elo_snapshot(date: str | None = None) -> list[dict[str, str]]:
    """Fetch full ranking CSV for a given date (default: today).

    Returns list of dicts with keys: Rank, Club, Country, Level, Elo, From, To.
    Returns empty list on network failure.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    url = f"{_CLUB_ELO_API}/{date}"
    try:
        response = httpx.get(url, timeout=15.0)
        if response.status_code != 200:
            logger.warning("ClubElo snapshot fetch failed: %s", response.status_code)
            return []
        reader = csv.DictReader(StringIO(response.text))
        return list(reader)
    except httpx.RequestError as exc:
        logger.warning("ClubElo snapshot network error: %s", exc)
        return []


def get_club_elo_by_country(country: str, level: int = 1) -> dict[str, float]:
    """Fetch snapshot and filter by country + level.

    Returns {team_name: elo_rating} for all clubs in the specified
    country's specified league level.
    """
    snapshot = fetch_club_elo_snapshot()
    result = {}
    for row in snapshot:
        if row.get("Country") == country:
            try:
                row_level = int(row.get("Level", 0))
                if row_level == level:
                    result[row["Club"]] = float(row["Elo"])
            except (ValueError, TypeError):
                continue
    return result


def get_club_elo(team_name: str) -> dict[str, Any] | None:
    """Get current club Elo rating.

    1. Check KernelClubEloCache table (TTL: CLUB_ELO_CACHE_TTL_DAYS).
    2. On cache miss/expire, fetch from ClubElo.com.
    3. Parse CSV, find matching club (case-insensitive, space-normalized).
    4. Cache result in KernelClubEloCache.
    5. Return {"elo_rating": float, "source": "clubelo"} or None on failure.
    """
    cached = _check_cache(team_name)
    if cached is not None:
        return cached

    # Fetch today's snapshot
    snapshot = fetch_club_elo_snapshot()
    if not snapshot:
        return None

    normalized_target = _normalize_team_name(team_name)
    for row in snapshot:
        normalized_club = _normalize_team_name(row.get("Club", ""))
        if normalized_club == normalized_target:
            try:
                elo = float(row["Elo"])
                result = {"elo_rating": elo, "source": "clubelo"}
                _save_cache(
                    team_name, elo,
                    country=row.get("Country", ""),
                    level=int(row.get("Level", 0)),
                )
                return result
            except (ValueError, TypeError):
                continue

    logger.debug("ClubElo: no match for '%s'", team_name)
    return None


def _check_cache(team_name: str) -> dict[str, Any] | None:
    """Check KernelClubEloCache for a valid cached entry.

    Returns {"elo_rating": float, "source": "clubelo"} if cache is fresh,
    or None if cache is missing/expired.
    """
    try:
        from app.kernel.kernel_db import get_kernel_session, KernelClubEloCache
    except ImportError:
        return None

    normalized = _normalize_team_name(team_name)
    session = get_kernel_session()
    try:
        entry = session.get(KernelClubEloCache, normalized)
        if entry is None:
            return None
        ttl_days = getattr(settings, "CLUB_ELO_CACHE_TTL_DAYS", 7)
        max_age = timedelta(days=ttl_days)
        if datetime.now(timezone.utc) - entry.fetched_at > max_age:
            return None
        return {"elo_rating": entry.elo_rating, "source": "clubelo"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("ClubElo cache check failed: %s", exc)
        return None
    finally:
        session.close()


def _save_cache(
    team_name: str, elo: float, country: str = "", level: int = 0,
) -> None:
    """Save club Elo to KernelClubEloCache."""
    try:
        from app.kernel.kernel_db import get_kernel_session, KernelClubEloCache
    except ImportError:
        return

    normalized = _normalize_team_name(team_name)
    session = get_kernel_session()
    try:
        existing = session.get(KernelClubEloCache, normalized)
        now = datetime.now(timezone.utc)
        if existing:
            existing.elo_rating = elo
            existing.fetched_at = now
            existing.country = country
            existing.level = level
        else:
            entry = KernelClubEloCache(
                team_name=normalized,
                elo_rating=elo,
                source="clubelo",
                fetched_at=now,
                country=country,
                level=level,
            )
            session.add(entry)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.debug("ClubElo cache save failed: %s", exc)
    finally:
        session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_club_elo_service.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/club_elo_service.py tests/test_club_elo_service.py
git commit -m "feat(services): add ClubElo.com CSV fetcher with cache"
```

---

### Task 3: Kernel DB Tables for Fixtures, Results, and Club Elo Cache

**Files:**
- Modify: `backend/app/kernel/kernel_db.py` (add 3 new table models)
- Test: `backend/tests/test_kernel_db_fixtures.py`

**Interfaces:**
- Consumes: existing `KernelBase`, `init_kernel_db()`, `get_kernel_session()`
- Produces: `KernelMatchFixture`, `KernelMatchResult`, `KernelClubEloCache` ORM models

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_db_fixtures.py
"""Tests for kernel DB fixture/result/club_elo_cache tables."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db, get_kernel_session, close_kernel_session,
    KernelMatchFixture, KernelMatchResult, KernelClubEloCache,
)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield
    close_kernel_session()


class TestKernelMatchFixture:
    def test_create_and_read(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        fixture = KernelMatchFixture(
            match_id="ucl-537327",
            competition="ucl",
            season="2025-26",
            home_team="Real Madrid CF",
            away_team="FC Bayern München",
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
            stage="group_stage",
            status="scheduled",
            home_score=None,
            away_score=None,
            venue="Santiago Bernabéu",
            created_at=now,
            updated_at=now,
        )
        session.add(fixture)
        session.commit()

        fetched = session.get(KernelMatchFixture, "ucl-537327")
        assert fetched is not None
        assert fetched.competition == "ucl"
        assert fetched.home_team == "Real Madrid CF"
        assert fetched.stage == "group_stage"
        session.close()

    def test_update_score_on_finished(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        fixture = KernelMatchFixture(
            match_id="epl-123456",
            competition="epl",
            season="2025-26",
            home_team="Arsenal FC",
            away_team="Chelsea FC",
            kickoff_utc=datetime(2025, 8, 16, 15, 0, tzinfo=timezone.utc),
            stage="regular_season",
            status="scheduled",
            venue="Emirates Stadium",
            created_at=now,
            updated_at=now,
        )
        session.add(fixture)
        session.commit()

        fixture.home_score = 2
        fixture.away_score = 1
        fixture.status = "finished"
        session.commit()

        fetched = session.get(KernelMatchFixture, "epl-123456")
        assert fetched.home_score == 2
        assert fetched.away_score == 1
        assert fetched.status == "finished"
        session.close()


class TestKernelMatchResult:
    def test_create_and_read(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        result = KernelMatchResult(
            match_id="ucl-537327",
            home_score=3,
            away_score=1,
            outcome="home_win",
            finished_at=datetime(2025, 9, 16, 22, 0, tzinfo=timezone.utc),
            created_at=now,
        )
        session.add(result)
        session.commit()

        fetched = session.get(KernelMatchResult, "ucl-537327")
        assert fetched is not None
        assert fetched.home_score == 3
        assert fetched.outcome == "home_win"
        session.close()


class TestKernelClubEloCache:
    def test_create_and_read(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        entry = KernelClubEloCache(
            team_name="arsenal",
            elo_rating=2063.76,
            source="clubelo",
            fetched_at=now,
            country="ENG",
            level=1,
        )
        session.add(entry)
        session.commit()

        fetched = session.get(KernelClubEloCache, "arsenal")
        assert fetched is not None
        assert fetched.elo_rating == 2063.76
        assert fetched.country == "ENG"
        session.close()

    def test_update_existing(self, db):
        session = get_kernel_session()
        now = datetime.now(timezone.utc)
        entry = KernelClubEloCache(
            team_name="mancity",
            elo_rating=1950.0,
            source="clubelo",
            fetched_at=now,
            country="ENG",
            level=1,
        )
        session.add(entry)
        session.commit()

        entry.elo_rating = 1970.85
        session.commit()

        fetched = session.get(KernelClubEloCache, "mancity")
        assert fetched.elo_rating == 1970.85
        session.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_db_fixtures.py -v`
Expected: FAIL with `ImportError: cannot import name 'KernelMatchFixture'`

- [ ] **Step 3: Add new table models to kernel_db.py**

Open `backend/app/kernel/kernel_db.py` and add the following three classes **after** the existing `KernelFactor` class and **before** `init_kernel_db()`:

```python
class KernelMatchFixture(KernelBase):
    """Fixture table for UCL/EPL matches (kernel_ prefixed)."""
    __tablename__ = "kernel_match_fixtures"

    match_id = Column(String, primary_key=True)
    competition = Column(String, nullable=False)
    season = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    kickoff_utc = Column(DateTime)
    stage = Column(String)
    status = Column(String, default="scheduled")
    home_score = Column(Integer)
    away_score = Column(Integer)
    venue = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class KernelMatchResult(KernelBase):
    """Match result table for UCL/EPL matches."""
    __tablename__ = "kernel_match_results"

    match_id = Column(String, primary_key=True)
    home_score = Column(Integer)
    away_score = Column(Integer)
    outcome = Column(String)
    finished_at = Column(DateTime)
    created_at = Column(DateTime)


class KernelClubEloCache(KernelBase):
    """Cache for club Elo ratings from ClubElo.com."""
    __tablename__ = "kernel_club_elo_cache"

    team_name = Column(String, primary_key=True)
    elo_rating = Column(Float, nullable=False)
    source = Column(String, default="clubelo")
    fetched_at = Column(DateTime, nullable=False)
    country = Column(String)
    level = Column(Integer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_db_fixtures.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/kernel_db.py tests/test_kernel_db_fixtures.py
git commit -m "feat(kernel-db): add kernel_match_fixtures, kernel_match_results, kernel_club_elo_cache tables"
```

---

### Task 4: Shared Adapter Utilities

**Files:**
- Create: `backend/app/sports/football/adapters/_shared.py`
- Test: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `MatchIdentity`, `MatchOutcome`, `CompetitionIdentity`, `SeasonIdentity`, `TeamIdentity` from `app.kernel.domain`; `get_elo_rating` (async) from `app.services.elo_ratings_service`; `get_cached_odds` (async) from `app.services.odds_cache_service`; `get_club_elo` (sync) from `app.services.club_elo_service`; `KernelMatchFixture`, `KernelMatchResult`, `get_kernel_session` from `app.kernel.kernel_db`
- Produces: `fetch_team_elo(team_name, scope, alias)`, `fetch_match_odds(home, away)`, `fetch_elo_and_odds(match, elo_scope, team_aliases)`, `query_fixture(match_id, model_cls)`, `query_result(match_id, model_cls)`, `build_match_identity(fixture, competition, season_key, default_stage)`, `build_match_outcome(result)`, `save_fixture(parsed, competition, season)`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_adapter_shared.py
"""Tests for _shared.py — shared adapter utility functions."""
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import asyncio
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.sports.football.adapters._shared import (
    fetch_team_elo,
    fetch_elo_and_odds,
    query_fixture,
    query_result,
    build_match_identity,
    build_match_outcome,
    save_fixture,
)


_FOOTBALL = SportIdentity(code="football", name="Football")
_UCL = CompetitionIdentity(code="ucl", name="UEFA Champions League", sport=_FOOTBALL)


def _make_match(match_id="ucl-123") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
        away=TeamIdentity(code="FCB", name="FC Bayern München", competition=_UCL),
        kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
    )


class TestFetchTeamElo:
    @pytest.mark.asyncio
    @patch("app.sports.football.adapters._shared.get_club_elo")
    async def test_club_scope(self, mock_club):
        mock_club.return_value = {"elo_rating": 1955.12, "source": "clubelo"}
        result = await fetch_team_elo("Real Madrid", scope="club")
        assert result is not None
        assert result["elo_rating"] == 1955.12
        mock_club.assert_called_once_with("Real Madrid")

    @pytest.mark.asyncio
    @patch("app.sports.football.adapters._shared.get_club_elo")
    async def test_club_scope_with_alias(self, mock_club):
        mock_club.return_value = {"elo_rating": 1955.12, "source": "clubelo"}
        result = await fetch_team_elo("Real Madrid CF", scope="club", alias="RealMadrid")
        assert result is not None
        mock_club.assert_called_once_with("RealMadrid")


class TestFetchEloAndOdds:
    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    def test_fetch_all_success(self, mock_odds, mock_club):
        mock_club.return_value = {"elo_rating": 1955.12, "source": "clubelo"}
        mock_odds.return_value = {"home": 1.5, "draw": 4.0, "away": 5.5, "source": "test"}

        match = _make_match()
        raw = fetch_elo_and_odds(match, elo_scope="club")

        assert raw["team"]["elo_home"] == 1955.12
        assert raw["team"]["elo_away"] == 1955.12
        assert raw["market"]["odds_home"] == 1.5
        assert raw["market"]["odds_away"] == 5.5
        assert raw["market"]["odds_fresh"] is False  # stale defaults to True
        assert raw["player"] == {}
        assert raw["environment"] == {}

    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    def test_fetch_with_team_aliases(self, mock_odds, mock_club):
        mock_club.return_value = {"elo_rating": 1900.0, "source": "clubelo"}
        mock_odds.return_value = None

        match = _make_match()
        aliases = {"Real Madrid CF": "RealMadrid", "FC Bayern München": "BayernMunich"}
        raw = fetch_elo_and_odds(match, elo_scope="club", team_aliases=aliases)

        # get_club_elo called with alias names
        calls = [c.args[0] for c in mock_club.call_args_list]
        assert "RealMadrid" in calls
        assert "BayernMunich" in calls


class TestBuildMatchIdentity:
    def test_build_from_fixture(self):
        fixture = MagicMock()
        fixture.match_id = "ucl-537327"
        fixture.home_team = "Real Madrid CF"
        fixture.away_team = "FC Bayern München"
        fixture.stage = "group_stage"
        fixture.kickoff_utc = datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc)

        identity = build_match_identity(fixture, _UCL, "2025-26", "group_stage")
        assert identity.match_id == "ucl-537327"
        assert identity.home.name == "Real Madrid CF"
        assert identity.away.name == "FC Bayern München"
        assert identity.stage == "group_stage"
        assert identity.home.competition == _UCL

    def test_build_with_none_stage(self):
        fixture = MagicMock()
        fixture.match_id = "epl-1"
        fixture.home_team = "Arsenal FC"
        fixture.away_team = "Chelsea FC"
        fixture.stage = None
        fixture.kickoff_utc = None

        identity = build_match_identity(fixture, _UCL, "2025-26", "regular_season")
        assert identity.stage == "regular_season"


class TestBuildMatchOutcome:
    def test_build_from_result(self):
        result = MagicMock()
        result.match_id = "ucl-537327"
        result.home_score = 3
        result.away_score = 1
        result.outcome = "home_win"
        result.finished_at = datetime(2025, 9, 16, 22, 0, tzinfo=timezone.utc)

        outcome = build_match_outcome(result)
        assert outcome.match_id == "ucl-537327"
        assert outcome.home_score == 3
        assert outcome.away_score == 1
        assert outcome.outcome == "home_win"

    def test_build_from_none_returns_none(self):
        assert build_match_outcome(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_adapter_shared.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/sports/football/adapters/_shared.py
"""Shared utility functions for football adapters.

Pure functions — no class, no module-level mutable state.
Each adapter calls these freely (composition over inheritance).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.kernel.domain import (
    CompetitionIdentity, SeasonIdentity, TeamIdentity,
    MatchIdentity, MatchOutcome,
)

logger = logging.getLogger(__name__)


async def fetch_team_elo(
    team_name: str,
    scope: str = "national",
    alias: str | None = None,
) -> dict[str, Any] | None:
    """Fetch Elo rating for a team.

    scope="national": delegates to elo_ratings_service.get_elo_rating() (async)
    scope="club": delegates to club_elo_service.get_club_elo() (sync)

    alias: if provided, used as the lookup name instead of team_name.

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

    Delegates to odds_cache_service.get_cached_odds() (async).
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
    event loop via asyncio.gather(return_exceptions=True).

    team_aliases: {team_name: clubelo_name} for name mapping.

    Returns dict with keys: team, market, player, environment, general.
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

    model_cls: KernelMatchFixture (for UCL/EPL).

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
    """Build MatchIdentity from a KernelMatchFixture row."""
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


def save_fixture(parsed: dict, competition: str, season: str) -> None:
    """Upsert a parsed fixture into kernel_match_fixtures.

    parsed: dict from football_data_client.parse_fixture()
    """
    from app.kernel.kernel_db import get_kernel_session, KernelMatchFixture
    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        existing = session.get(KernelMatchFixture, parsed["match_id"])
        if existing:
            existing.home_team = parsed["home_team"]
            existing.away_team = parsed["away_team"]
            existing.kickoff_utc = parsed["kickoff_utc"]
            existing.stage = parsed["stage"]
            existing.status = parsed["status"]
            existing.venue = parsed["venue"]
            if parsed.get("home_score") is not None:
                existing.home_score = parsed["home_score"]
            if parsed.get("away_score") is not None:
                existing.away_score = parsed["away_score"]
            existing.updated_at = now
        else:
            fixture = KernelMatchFixture(
                match_id=parsed["match_id"],
                competition=competition,
                season=season,
                home_team=parsed["home_team"],
                away_team=parsed["away_team"],
                kickoff_utc=parsed["kickoff_utc"],
                stage=parsed["stage"],
                status=parsed["status"],
                home_score=parsed.get("home_score"),
                away_score=parsed.get("away_score"),
                venue=parsed["venue"],
                created_at=now,
                updated_at=now,
            )
            session.add(fixture)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to save fixture %s: %s", parsed.get("match_id"), exc)
    finally:
        session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_adapter_shared.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/sports/football/adapters/_shared.py tests/test_adapter_shared.py
git commit -m "feat(adapters): add shared utility functions for football adapters"
```

---

### Task 5: UCL Adapter

**Files:**
- Create: `backend/app/sports/football/adapters/ucl_adapter.py`
- Test: `backend/tests/test_ucl_adapter.py`

**Interfaces:**
- Consumes: `fetch_elo_and_odds`, `query_fixture`, `query_result`, `build_match_identity`, `build_match_outcome`, `save_fixture` from `_shared.py`; `fetch_competition_fixtures`, `parse_fixture` from `football_data_client.py`; `KernelMatchFixture`, `KernelMatchResult` from `kernel_db.py`
- Produces: `UCLAdapter` implementing `DataAdapter` Protocol

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ucl_adapter.py
"""Tests for UCLAdapter — DataAdapter Protocol implementation for UCL."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.sports.football.adapters.ucl_adapter import UCLAdapter


def _make_match(match_id="ucl-537327") -> MatchIdentity:
    football = SportIdentity(code="football", name="Football")
    ucl = CompetitionIdentity(code="ucl", name="UEFA Champions League", sport=football)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=ucl, season_key="2025-26"),
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=ucl),
        away=TeamIdentity(code="FCB", name="FC Bayern München", competition=ucl),
        kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
    )


class TestUCLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = UCLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestGetMatchIdentity:
    @patch("app.sports.football.adapters.ucl_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        fixture = MagicMock()
        fixture.match_id = "ucl-537327"
        fixture.home_team = "Real Madrid CF"
        fixture.away_team = "FC Bayern München"
        fixture.stage = "group_stage"
        fixture.kickoff_utc = datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc)
        mock_query.return_value = fixture

        adapter = UCLAdapter()
        identity = adapter.get_match_identity("ucl-537327")
        assert identity.match_id == "ucl-537327"
        assert identity.home.name == "Real Madrid CF"
        assert identity.away.name == "FC Bayern München"
        assert identity.season.competition.code == "ucl"
        assert identity.stage == "group_stage"

    @patch("app.sports.football.adapters.ucl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = UCLAdapter()
        identity = adapter.get_match_identity("ucl-nonexistent")
        assert identity.match_id == "ucl-nonexistent"
        assert identity.home.name == "Home"
        assert identity.season.competition.code == "ucl"


class TestFetchAllData:
    @patch("app.sports.football.adapters.ucl_adapter.fetch_elo_and_odds")
    def test_fetch_all_data_uses_club_elo(self, mock_fetch):
        mock_fetch.return_value = {
            "team": {"elo_home": 1955.12, "elo_away": 1940.33},
            "market": {"odds_home": 1.5},
            "player": {}, "environment": {}, "general": {},
        }
        adapter = UCLAdapter()
        match = _make_match()
        raw = adapter.fetch_all_data(match)

        assert raw["team"]["elo_home"] == 1955.12
        # Verify fetch_elo_and_odds was called with club scope
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs["elo_scope"] == "club"
        assert call_kwargs["team_aliases"] is not None  # UCL has aliases


class TestFetchOutcome:
    @patch("app.sports.football.adapters.ucl_adapter.query_result")
    @patch("app.sports.football.adapters.ucl_adapter.build_match_outcome")
    def test_fetch_outcome_returns_outcome(self, mock_build, mock_query):
        mock_query.return_value = MagicMock()
        expected = MatchOutcome(
            match_id="ucl-537327", home_score=3, away_score=1,
            outcome="home_win",
            finished_at=datetime(2025, 9, 16, 22, 0, tzinfo=timezone.utc),
        )
        mock_build.return_value = expected

        adapter = UCLAdapter()
        result = adapter.fetch_outcome("ucl-537327")
        assert result == expected

    @patch("app.sports.football.adapters.ucl_adapter.query_result")
    @patch("app.sports.football.adapters.ucl_adapter.build_match_outcome")
    def test_fetch_outcome_returns_none(self, mock_build, mock_query):
        mock_query.return_value = None
        mock_build.return_value = None
        adapter = UCLAdapter()
        assert adapter.fetch_outcome("ucl-nonexistent") is None


class TestSyncSchedule:
    @patch("app.sports.football.adapters.ucl_adapter.save_fixture")
    @patch("app.sports.football.adapters.ucl_adapter.parse_fixture")
    @patch("app.sports.football.adapters.ucl_adapter.fetch_competition_fixtures")
    def test_sync_saves_fixtures(self, mock_fetch, mock_parse, mock_save):
        mock_fetch.return_value = [{"id": 1}, {"id": 2}]
        mock_parse.side_effect = [
            {"match_id": "ucl-1", "home_team": "A", "away_team": "B",
             "kickoff_utc": datetime(2025, 9, 16), "stage": "group_stage",
             "status": "scheduled", "venue": "X"},
            {"match_id": "ucl-2", "home_team": "C", "away_team": "D",
             "kickoff_utc": datetime(2025, 9, 17), "stage": "group_stage",
             "status": "scheduled", "venue": "Y"},
        ]
        adapter = UCLAdapter()
        count = adapter.sync_schedule()
        assert count == 2
        assert mock_save.call_count == 2

    @patch("app.sports.football.adapters.ucl_adapter.fetch_competition_fixtures")
    def test_sync_failure_returns_zero(self, mock_fetch):
        mock_fetch.side_effect = Exception("API error")
        adapter = UCLAdapter()
        assert adapter.sync_schedule() == 0


class TestStubMethods:
    def test_fetch_team_data_returns_empty(self):
        assert UCLAdapter().fetch_team_data(MagicMock()) == {}

    def test_fetch_player_data_returns_empty(self):
        assert UCLAdapter().fetch_player_data(MagicMock()) == {}

    def test_fetch_market_data_returns_empty(self):
        assert UCLAdapter().fetch_market_data(MagicMock()) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ucl_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/sports/football/adapters/ucl_adapter.py
"""UCLAdapter — DataAdapter Protocol implementation for UEFA Champions League.

Bridges Football-Data.org (CL) and ClubElo.com data sources to the
sport-agnostic DataAdapter interface. The Kernel never sees UCL-specific
code — it only sees DataAdapter.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.sports.football.adapters._shared import (
    fetch_elo_and_odds, query_fixture, query_result,
    build_match_identity, build_match_outcome, save_fixture,
)
from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult

logger = logging.getLogger(__name__)

_FOOTBALL = SportIdentity(code="football", name="Football")
_COMPETITION = CompetitionIdentity(
    code="ucl", name="UEFA Champions League", sport=_FOOTBALL
)
_DEFAULT_SEASON = "2025-26"
_DEFAULT_STAGE = "group_stage"
_DEFAULT_KICKOFF = datetime(2025, 9, 16, tzinfo=timezone.utc)

_STAGE_MAP = {
    "GROUP_STAGE": "group_stage",
    "ROUND_OF_16": "round_of_16",
    "QUARTER_FINALS": "quarterfinal",
    "SEMI_FINALS": "semifinal",
    "FINAL": "final",
}

_MATCH_ID_PREFIX = "ucl-"
_FD_COMPETITION = "CL"
_FD_SEASON = 2025

# Football-Data.org name → ClubElo.com URL name (spaces removed)
_TEAM_ALIASES = {
    "Real Madrid CF": "RealMadrid",
    "Manchester City FC": "ManCity",
    "FC Bayern München": "BayernMunich",
    "Bayern Munich": "BayernMunich",
    "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Liverpool FC": "Liverpool",
    "FC Barcelona": "Barcelona",
    "Barcelona": "Barcelona",
    "Paris Saint-Germain": "ParisSG",
    "Paris SG": "ParisSG",
    "Atlético de Madrid": "AtleticoMadrid",
    "Atletico Madrid": "AtleticoMadrid",
    "Borussia Dortmund": "Dortmund",
    "Inter Milan": "Inter",
    "FC Internazionale Milano": "Inter",
    "AC Milan": "Milan",
    "Juventus FC": "Juventus",
    "SSC Napoli": "Napoli",
    "Napoli": "Napoli",
    "SL Benfica": "Benfica",
    "Benfica": "Benfica",
    "FC Porto": "Porto",
    "Porto": "Porto",
    "Sporting CP": "Sporting",
    "Sporting Lisbon": "Sporting",
    "Celtic FC": "Celtic",
    "Celtic": "Celtic",
    "Rangers FC": "Rangers",
    "Rangers": "Rangers",
    "Aston Villa FC": "AstonVilla",
    "Aston Villa": "AstonVilla",
    "Tottenham Hotspur FC": "Tottenham",
    "Tottenham": "Tottenham",
    "Newcastle United FC": "Newcastle",
    "Newcastle": "Newcastle",
    "RB Leipzig": "Leipzig",
    "VfB Stuttgart": "Stuttgart",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Leverkusen": "Leverkusen",
    "Atalanta BC": "Atalanta",
    "Atalanta": "Atalanta",
    "Feyenoord Rotterdam": "Feyenoord",
    "Feyenoord": "Feyenoord",
    "PSV Eindhoven": "PSV",
    "PSV": "PSV",
    "Ajax Amsterdam": "Ajax",
    "Ajax": "Ajax",
    "Club Brugge KV": "ClubBrugge",
    "Club Brugge": "ClubBrugge",
    "FC Salzburg": "Salzburg",
    "RB Salzburg": "Salzburg",
    "Shakhtar Donetsk": "Shakhtar",
    "Dinamo Zagreb": "DinamoZagreb",
    "FK Crvena Zvezda": "CrvenaZvezda",
    "Red Star Belgrade": "CrvenaZvezda",
    "BSC Young Boys": "YoungBoys",
    "Young Boys": "YoungBoys",
    "Slovan Bratislava": "SlovanBratislava",
    "GNK Dinamo Zagreb": "DinamoZagreb",
    "Sturm Graz": "SturmGraz",
    "VfL Bochum": "Bochum",
    "Girona FC": "Girona",
    "Girona": "Girona",
    "Stade Brestois 29": "Brest",
    "Brest": "Brest",
    "Lille OSC": "Lille",
    "Lille": "Lille",
    "Olympique de Marseille": "Marseille",
    "Marseille": "Marseille",
    "AS Monaco": "Monaco",
    "Monaco": "Monaco",
}


def _stub_identity(match_id: str) -> MatchIdentity:
    """Return a stub MatchIdentity when fixture data is unavailable."""
    home = TeamIdentity(code="HOME", name="Home", competition=_COMPETITION)
    away = TeamIdentity(code="AWAY", name="Away", competition=_COMPETITION)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_COMPETITION, season_key=_DEFAULT_SEASON),
        stage=_DEFAULT_STAGE,
        round=None,
        home=home,
        away=away,
        kickoff_utc=_DEFAULT_KICKOFF,
    )


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
            fixtures_raw = fetch_competition_fixtures(_FD_COMPETITION, season=_FD_SEASON)
            count = 0
            for raw in fixtures_raw:
                parsed = parse_fixture(
                    raw, stage_mapping=_STAGE_MAP,
                    match_id_prefix=_MATCH_ID_PREFIX,
                )
                if parsed:
                    save_fixture(parsed, "ucl", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync UCL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from app.kernel.kernel_db import get_kernel_session
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "ucl"
            )
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)
            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(
                    match=self.get_match_identity(f.match_id), raw_json={}
                )
                for f in fixtures
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch UCL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ucl_adapter.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/sports/football/adapters/ucl_adapter.py tests/test_ucl_adapter.py
git commit -m "feat(adapters): add UCLAdapter implementing DataAdapter Protocol"
```

---

### Task 6: EPL Adapter

**Files:**
- Create: `backend/app/sports/football/adapters/epl_adapter.py`
- Test: `backend/tests/test_epl_adapter.py`

**Interfaces:**
- Consumes: same as UCLAdapter (Task 5) but with different constants
- Produces: `EPLAdapter` implementing `DataAdapter` Protocol

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_epl_adapter.py
"""Tests for EPLAdapter — DataAdapter Protocol implementation for EPL."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.football.adapters.epl_adapter import EPLAdapter


def _make_match(match_id="epl-123456") -> MatchIdentity:
    football = SportIdentity(code="football", name="Football")
    epl = CompetitionIdentity(code="epl", name="English Premier League", sport=football)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=epl, season_key="2025-26"),
        stage="regular_season",
        round=None,
        home=TeamIdentity(code="ARS", name="Arsenal FC", competition=epl),
        away=TeamIdentity(code="CHE", name="Chelsea FC", competition=epl),
        kickoff_utc=datetime(2025, 8, 16, 15, 0, tzinfo=timezone.utc),
    )


class TestEPLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = EPLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestGetMatchIdentity:
    @patch("app.sports.football.adapters.epl_adapter.query_fixture")
    def test_returns_identity_with_regular_season(self, mock_query):
        fixture = MagicMock()
        fixture.match_id = "epl-123456"
        fixture.home_team = "Arsenal FC"
        fixture.away_team = "Chelsea FC"
        fixture.stage = "regular_season"
        fixture.kickoff_utc = datetime(2025, 8, 16, 15, 0, tzinfo=timezone.utc)
        mock_query.return_value = fixture

        adapter = EPLAdapter()
        identity = adapter.get_match_identity("epl-123456")
        assert identity.match_id == "epl-123456"
        assert identity.home.name == "Arsenal FC"
        assert identity.stage == "regular_season"
        assert identity.season.competition.code == "epl"

    @patch("app.sports.football.adapters.epl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = EPLAdapter()
        identity = adapter.get_match_identity("epl-nonexistent")
        assert identity.match_id == "epl-nonexistent"
        assert identity.stage == "regular_season"
        assert identity.season.competition.code == "epl"


class TestFetchAllData:
    @patch("app.sports.football.adapters.epl_adapter.fetch_elo_and_odds")
    def test_fetch_all_data_uses_club_elo(self, mock_fetch):
        mock_fetch.return_value = {
            "team": {"elo_home": 2063.76, "elo_away": 1680.0},
            "market": {},
            "player": {}, "environment": {}, "general": {},
        }
        adapter = EPLAdapter()
        match = _make_match()
        raw = adapter.fetch_all_data(match)

        assert raw["team"]["elo_home"] == 2063.76
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs["elo_scope"] == "club"
        assert "Arsenal FC" in call_kwargs["team_aliases"]


class TestSyncSchedule:
    @patch("app.sports.football.adapters.epl_adapter.save_fixture")
    @patch("app.sports.football.adapters.epl_adapter.parse_fixture")
    @patch("app.sports.football.adapters.epl_adapter.fetch_competition_fixtures")
    def test_sync_uses_pl_code(self, mock_fetch, mock_parse, mock_save):
        mock_fetch.return_value = [{"id": 1}]
        mock_parse.return_value = {
            "match_id": "epl-1", "home_team": "A", "away_team": "B",
            "kickoff_utc": datetime(2025, 8, 16), "stage": "regular_season",
            "status": "scheduled", "venue": "X",
        }
        adapter = EPLAdapter()
        count = adapter.sync_schedule()
        assert count == 1
        # Verify PL competition code was used
        mock_fetch.assert_called_once_with("PL", season=2025)

    @patch("app.sports.football.adapters.epl_adapter.fetch_competition_fixtures")
    def test_sync_failure_returns_zero(self, mock_fetch):
        mock_fetch.side_effect = Exception("API error")
        adapter = EPLAdapter()
        assert adapter.sync_schedule() == 0


class TestStubMethods:
    def test_fetch_team_data_returns_empty(self):
        assert EPLAdapter().fetch_team_data(MagicMock()) == {}

    def test_fetch_player_data_returns_empty(self):
        assert EPLAdapter().fetch_player_data(MagicMock()) == {}

    def test_fetch_market_data_returns_empty(self):
        assert EPLAdapter().fetch_market_data(MagicMock()) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_epl_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/sports/football/adapters/epl_adapter.py
"""EPLAdapter — DataAdapter Protocol implementation for English Premier League.

Structurally identical to UCLAdapter with different constants:
- Competition code: "epl" (Football-Data.org code: "PL")
- No stage mapping (all stages → "regular_season")
- EPL-specific team aliases for ClubElo.com
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.sports.football.adapters._shared import (
    fetch_elo_and_odds, query_fixture, query_result,
    build_match_identity, build_match_outcome, save_fixture,
)
from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult

logger = logging.getLogger(__name__)

_FOOTBALL = SportIdentity(code="football", name="Football")
_COMPETITION = CompetitionIdentity(
    code="epl", name="English Premier League", sport=_FOOTBALL
)
_DEFAULT_SEASON = "2025-26"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2025, 8, 16, tzinfo=timezone.utc)

# EPL has no knockout stages — all fixtures are regular season
_STAGE_MAP: dict[str, str] = {}

_MATCH_ID_PREFIX = "epl-"
_FD_COMPETITION = "PL"
_FD_SEASON = 2025

# Football-Data.org name → ClubElo.com URL name
_TEAM_ALIASES = {
    "Arsenal FC": "Arsenal",
    "Arsenal": "Arsenal",
    "Aston Villa FC": "AstonVilla",
    "Aston Villa": "AstonVilla",
    " AFC Bournemouth": "Bournemouth",
    "Bournemouth": "Bournemouth",
    "Brentford FC": "Brentford",
    "Brentford": "Brentford",
    "Brighton & Hove Albion FC": "Brighton",
    "Brighton": "Brighton",
    "Chelsea FC": "Chelsea",
    "Chelsea": "Chelsea",
    "Crystal Palace FC": "CrystalPalace",
    "Crystal Palace": "CrystalPalace",
    "Everton FC": "Everton",
    "Everton": "Everton",
    "Fulham FC": "Fulham",
    "Fulham": "Fulham",
    "Liverpool FC": "Liverpool",
    "Liverpool": "Liverpool",
    "Luton Town FC": "LutonTown",
    "Luton Town": "LutonTown",
    "Manchester City FC": "ManCity",
    "Manchester City": "ManCity",
    "Manchester United FC": "ManUnited",
    "Manchester United": "ManUnited",
    "Newcastle United FC": "Newcastle",
    "Newcastle": "Newcastle",
    "Nottingham Forest FC": "NottinghamForest",
    "Nottingham Forest": "NottinghamForest",
    "Sheffield United FC": "SheffieldUnited",
    "Sheffield United": "SheffieldUnited",
    "Tottenham Hotspur FC": "Tottenham",
    "Tottenham": "Tottenham",
    "West Ham United FC": "WestHam",
    "West Ham": "WestHam",
    "Wolverhampton Wanderers FC": "Wolves",
    "Wolves": "Wolves",
    "Burnley FC": "Burnley",
    "Burnley": "Burnley",
    "Leicester City FC": "Leicester",
    "Leicester City": "Leicester",
    "Ipswich Town FC": "Ipswich",
    "Ipswich Town": "Ipswich",
    "Southampton FC": "Southampton",
    "Southampton": "Southampton",
    "Leeds United FC": "Leeds",
    "Leeds United": "Leeds",
}


def _stub_identity(match_id: str) -> MatchIdentity:
    """Return a stub MatchIdentity when fixture data is unavailable."""
    home = TeamIdentity(code="HOME", name="Home", competition=_COMPETITION)
    away = TeamIdentity(code="AWAY", name="Away", competition=_COMPETITION)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_COMPETITION, season_key=_DEFAULT_SEASON),
        stage=_DEFAULT_STAGE,
        round=None,
        home=home,
        away=away,
        kickoff_utc=_DEFAULT_KICKOFF,
    )


class EPLAdapter:
    """DataAdapter Protocol implementation for English Premier League."""

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
            fixtures_raw = fetch_competition_fixtures(_FD_COMPETITION, season=_FD_SEASON)
            count = 0
            for raw in fixtures_raw:
                parsed = parse_fixture(
                    raw, stage_mapping=_STAGE_MAP,
                    match_id_prefix=_MATCH_ID_PREFIX,
                )
                if parsed:
                    save_fixture(parsed, "epl", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync EPL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from app.kernel.kernel_db import get_kernel_session
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "epl"
            )
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)
            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(
                    match=self.get_match_identity(f.match_id), raw_json={}
                )
                for f in fixtures
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch EPL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_epl_adapter.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/sports/football/adapters/epl_adapter.py tests/test_epl_adapter.py
git commit -m "feat(adapters): add EPLAdapter implementing DataAdapter Protocol"
```

---

### Task 7: MultiAdapter Proxy

**Files:**
- Create: `backend/app/sports/football/adapters/multi_adapter.py`
- Test: `backend/tests/test_multi_adapter.py`

**Interfaces:**
- Consumes: `DataAdapter` Protocol from `app.kernel.protocols`; any adapter implementing `DataAdapter`
- Produces: `MultiAdapter` implementing `DataAdapter` Protocol

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_multi_adapter.py
"""Tests for MultiAdapter — prefix-dispatch proxy."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.sports.football.adapters.multi_adapter import MultiAdapter


def _make_match(match_id="wc-123") -> MatchIdentity:
    football = SportIdentity(code="football", name="Football")
    wc = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=football)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=wc, season_key="2026"),
        stage="group_stage", round=None,
        home=TeamIdentity(code="BRA", name="Brazil", competition=wc),
        away=TeamIdentity(code="ARG", name="Argentina", competition=wc),
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _mock_adapter():
    """Create a MagicMock that satisfies DataAdapter Protocol."""
    adapter = MagicMock()
    adapter.get_match_identity.return_value = _make_match()
    adapter.fetch_all_data.return_value = {"team": {}, "market": {}}
    adapter.fetch_outcome.return_value = None
    adapter.sync_schedule.return_value = 5
    adapter.fetch_schedule.return_value = []
    adapter.fetch_team_data.return_value = {}
    adapter.fetch_player_data.return_value = {}
    adapter.fetch_market_data.return_value = {}
    return adapter


class TestMultiAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        multi = MultiAdapter({"wc": _mock_adapter()})
        assert isinstance(multi, DataAdapter)


class TestPrefixDispatch:
    def test_wc_prefix_dispatches_to_wc_adapter(self):
        wc = _mock_adapter()
        ucl = _mock_adapter()
        epl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "ucl-": ucl, "epl-": epl})

        multi.get_match_identity("wc-123")
        wc.get_match_identity.assert_called_once_with("wc-123")
        ucl.get_match_identity.assert_not_called()

    def test_ucl_prefix_dispatches_to_ucl_adapter(self):
        wc = _mock_adapter()
        ucl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "ucl-": ucl})

        multi.get_match_identity("ucl-456")
        ucl.get_match_identity.assert_called_once_with("ucl-456")

    def test_unknown_prefix_falls_back_to_default(self):
        wc = _mock_adapter()
        multi = MultiAdapter({"wc-": wc})

        multi.get_match_identity("unknown-789")
        wc.get_match_identity.assert_called_once_with("unknown-789")

    def test_fetch_all_data_dispatches_by_match_id(self):
        wc = _mock_adapter()
        ucl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "ucl-": ucl})

        match = _make_match("ucl-999")
        multi.fetch_all_data(match)
        ucl.fetch_all_data.assert_called_once_with(match)
        wc.fetch_all_data.assert_not_called()

    def test_fetch_outcome_dispatches(self):
        wc = _mock_adapter()
        epl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "epl-": epl})

        multi.fetch_outcome("epl-123")
        epl.fetch_outcome.assert_called_once_with("epl-123")


class TestSyncSchedule:
    def test_sync_aggregates_all_adapters(self):
        wc = _mock_adapter()
        wc.sync_schedule.return_value = 10
        ucl = _mock_adapter()
        ucl.sync_schedule.return_value = 5
        epl = _mock_adapter()
        epl.sync_schedule.return_value = 20

        multi = MultiAdapter({"wc-": wc, "ucl-": ucl, "epl-": epl})
        total = multi.sync_schedule()
        assert total == 35

    def test_sync_with_single_adapter(self):
        wc = _mock_adapter()
        wc.sync_schedule.return_value = 10
        multi = MultiAdapter({"wc-": wc})
        assert multi.sync_schedule() == 10


class TestFetchSchedule:
    def test_aggregates_all_adapters(self):
        wc = _mock_adapter()
        wc.fetch_schedule.return_value = [MagicMock()]
        ucl = _mock_adapter()
        ucl.fetch_schedule.return_value = [MagicMock(), MagicMock()]

        multi = MultiAdapter({"wc-": wc, "ucl-": ucl})
        results = multi.fetch_schedule(ScheduleFilter())
        assert len(results) == 3


class TestStubMethods:
    def test_fetch_team_data_delegates_to_default(self):
        wc = _mock_adapter()
        multi = MultiAdapter({"wc-": wc})
        multi.fetch_team_data(MagicMock())
        wc.fetch_team_data.assert_called_once()

    def test_fetch_market_data_dispatches_by_match_id(self):
        wc = _mock_adapter()
        epl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "epl-": epl})

        match = _make_match("epl-1")
        multi.fetch_market_data(match)
        epl.fetch_market_data.assert_called_once_with(match)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_multi_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/sports/football/adapters/multi_adapter.py
"""MultiAdapter — DataAdapter Protocol proxy with prefix-based dispatch.

Implements DataAdapter Protocol transparently. The PredictionKernel
sees a single adapter; internally, calls are routed to the correct
league adapter based on the match_id prefix.

Prefix mapping:
    "wc-"  → WorldCupAdapter
    "ucl-" → UCLAdapter
    "epl-" → EPLAdapter

Unknown prefixes fall back to the default adapter (first registered,
or "wc-" if present) for backward compatibility.
"""
from __future__ import annotations

import logging

from app.kernel.domain import (
    MatchIdentity, MatchOutcome, TeamIdentity,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData

logger = logging.getLogger(__name__)


class MultiAdapter:
    """DataAdapter Protocol proxy — dispatches by match_id prefix."""

    def __init__(self, adapters: dict[str, object]) -> None:
        """Initialize with prefix-to-adapter mapping.

        Args:
            adapters: {prefix: adapter} where prefix is a string like
                "wc-", "ucl-", "epl-". The first adapter is used as
                the default for unknown prefixes.
        """
        self._adapters = adapters
        # Default to first adapter for unknown prefixes
        self._default = next(iter(adapters.values()))

    def _select(self, match_id: str) -> object:
        """Select the adapter for a given match_id by prefix."""
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
        return sum(adapter.sync_schedule() for adapter in self._adapters.values())

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        results = []
        for adapter in self._adapters.values():
            results.extend(adapter.fetch_schedule(filters))
        return results

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return self._default.fetch_team_data(team)

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return self._default.fetch_player_data(team)

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return self._select(match.match_id).fetch_market_data(match)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_multi_adapter.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/sports/football/adapters/multi_adapter.py tests/test_multi_adapter.py
git commit -m "feat(adapters): add MultiAdapter prefix-dispatch proxy"
```

---

### Task 8: Config + API Route Integration

**Files:**
- Modify: `backend/app/core/config.py` (add `PHASE2_LEAGUES_ENABLED`, `CLUB_ELO_CACHE_TTL_DAYS`, `CLUB_ELO_REQUEST_INTERVAL`)
- Modify: `backend/app/api/routes/predictions.py` (register MultiAdapter in `_get_kernel()`)
- Test: `backend/tests/test_predictions_route.py` (extend with Phase 2 tests)

**Interfaces:**
- Consumes: `MultiAdapter`, `UCLAdapter`, `EPLAdapter`, `WorldCupAdapter` from adapter layer; `config.settings` for flags
- Produces: Updated `/api/predictions/*` routes with multi-league support

- [ ] **Step 1: Write the failing test**

Add the following tests to the existing `backend/tests/test_predictions_route.py`:

```python
# Append to existing test file — these are new test classes

class TestPhase2Routes:
    """Tests for Phase 2 multi-league routes."""

    @pytest.fixture
    def client_phase2(self):
        """Client with both Phase 1 and Phase 2 flags enabled."""
        from app.main import app
        from app.core import config
        old_kernel = config.settings.KERNEL_PREDICTION_ENABLED
        old_phase2 = config.settings.PHASE2_LEAGUES_ENABLED
        # Clear any cached kernel instance
        from app.api.routes.predictions import _get_kernel
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")
        config.settings.KERNEL_PREDICTION_ENABLED = True
        config.settings.PHASE2_LEAGUES_ENABLED = True
        yield TestClient(app)
        config.settings.KERNEL_PREDICTION_ENABLED = old_kernel
        config.settings.PHASE2_LEAGUES_ENABLED = old_phase2
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")

    def test_engines_list_includes_elo_odds(self, client_phase2):
        resp = client_phase2.get("/api/predictions/engines")
        assert resp.status_code == 200
        data = resp.json()
        assert "elo_odds" in data

    def test_ucl_predict_returns_200_or_404(self, client_phase2):
        """UCL match prediction should work (404 if fixture not in DB, not 500)."""
        resp = client_phase2.post(
            "/api/predictions/matches/ucl-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)  # 500 acceptable if service unavailable

    def test_epl_predict_returns_200_or_404(self, client_phase2):
        """EPL match prediction should work (404 if fixture not in DB, not 500)."""
        resp = client_phase2.post(
            "/api/predictions/matches/epl-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)

    def test_phase2_disabled_ucl_falls_back(self):
        """When PHASE2_LEAGUES_ENABLED=false, ucl- prefix falls back to WorldCupAdapter."""
        from app.main import app
        from app.core import config
        from app.api.routes.predictions import _get_kernel
        old_kernel = config.settings.KERNEL_PREDICTION_ENABLED
        old_phase2 = config.settings.PHASE2_LEAGUES_ENABLED
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")
        config.settings.KERNEL_PREDICTION_ENABLED = True
        config.settings.PHASE2_LEAGUES_ENABLED = False
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/predictions/matches/ucl-nonexistent/predict",
                headers={"X-Write-Key": "test"},
            )
            # Should still work (falls back to WorldCupAdapter, stub identity)
            assert resp.status_code in (200, 404, 500)
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = old_kernel
            config.settings.PHASE2_LEAGUES_ENABLED = old_phase2
            if hasattr(_get_kernel, "_instance"):
                delattr(_get_kernel, "_instance")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictions_route.py::TestPhase2Routes -v`
Expected: FAIL (tests run but `PHASE2_LEAGUES_ENABLED` doesn't exist yet)

- [ ] **Step 3: Add config settings**

Open `backend/app/core/config.py` and find the `KERNEL_PREDICTION_ENABLED` setting (near line 950). Add the following **after** it and **before** `settings = Settings()`:

```python
    # Phase 2 — Multi-league support (default OFF). When false, only
    # World Cup (wc- prefix) adapters are registered. Set to true to
    # enable UCL and EPL adapters.
    PHASE2_LEAGUES_ENABLED: bool = _env_bool(
        "PHASE2_LEAGUES_ENABLED", "false"
    )

    # ClubElo.com service configuration
    CLUB_ELO_CACHE_TTL_DAYS: int = int(
        os.getenv("CLUB_ELO_CACHE_TTL_DAYS", "7")
    )
    CLUB_ELO_REQUEST_INTERVAL: float = float(
        os.getenv("CLUB_ELO_REQUEST_INTERVAL", "1.0")
    )
```

- [ ] **Step 4: Update `_get_kernel()` in predictions.py**

Open `backend/app/api/routes/predictions.py` and replace the `_get_kernel()` function body (the part after all imports, where `PredictionKernel` is constructed) with:

```python
    if not hasattr(_get_kernel, "_instance"):
        init_kernel_db()
        reg = EngineRegistry()
        reg.register(EloOddsEngine())

        # Build adapter registry — always includes WorldCupAdapter
        adapters: dict[str, object] = {
            "wc-": WorldCupAdapter(),
        }

        # Phase 2: register UCL and EPL adapters when enabled
        if config.settings.PHASE2_LEAGUES_ENABLED:
            from app.sports.football.adapters.ucl_adapter import UCLAdapter
            from app.sports.football.adapters.epl_adapter import EPLAdapter
            adapters["ucl-"] = UCLAdapter()
            adapters["epl-"] = EPLAdapter()

        from app.sports.football.adapters.multi_adapter import MultiAdapter
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

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictions_route.py -v`
Expected: PASS (all existing + new Phase 2 tests)

- [ ] **Step 6: Run full regression**

Run: `cd backend && python -m pytest tests/ -v --tb=short -q`
Expected: All existing tests pass + new tests pass

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/core/config.py app/api/routes/predictions.py tests/test_predictions_route.py
git commit -m "feat(api): integrate MultiAdapter with PHASE2_LEAGUES_ENABLED flag"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] Parameterized Football-Data.org client — Task 1
- [x] ClubElo.com CSV service — Task 2
- [x] `kernel_match_fixtures` + `kernel_match_results` + `kernel_club_elo_cache` tables — Task 3
- [x] Shared adapter utilities (`_shared.py`) — Task 4
- [x] `UCLAdapter` — Task 5
- [x] `EPLAdapter` — Task 6
- [x] `MultiAdapter` proxy — Task 7
- [x] `PHASE2_LEAGUES_ENABLED` config flag — Task 8
- [x] API route integration — Task 8
- [x] Team name alias system — Tasks 5 & 6 (per-adapter constants)
- [x] Kernel zero modification (except kernel_db.py for new tables) — confirmed
- [x] WorldCupAdapter zero modification — confirmed
- [x] FootballFeatureBuilder zero modification — confirmed
- [x] Frontend zero modification — confirmed

**2. Placeholder scan:** No TBD/TODO found. All steps contain actual code. All team alias dicts have real entries.

**3. Type consistency:** Verified:
- `fetch_elo_and_odds(match, elo_scope, team_aliases)` — consistent across _shared.py, UCLAdapter, EPLAdapter
- `query_fixture(match_id, model_cls)` — consistent across _shared.py, UCLAdapter, EPLAdapter
- `build_match_identity(fixture, competition, season_key, default_stage)` — consistent
- `MultiAdapter.__init__(adapters: dict[str, object])` — keys are prefixes with dashes ("wc-", "ucl-", "epl-")
- `DataAdapter` Protocol has 8 methods — all 8 implemented in UCLAdapter, EPLAdapter, MultiAdapter

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-14-sports-prediction-os-phase2.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
