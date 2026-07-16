# Phase 12: Futures/Championship Markets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add support for futures/championship markets (multi-leg N-outcome events) alongside existing single-match binary markets, with discovery from Kalshi, link storage, periodic price snapshots, REST API, and a frontend dashboard.

**Architecture:** New `FuturesMarketService` orchestrates Kalshi futures fetching → `FuturesLinkStore` persistence (kernel_futures_links) → snapshot capture (kernel_futures_snapshots). Two scheduler jobs run discovery and snapshot capture on intervals. 3 REST endpoints under `/api/futures/` expose the data. Frontend `/sports/futures` page renders a team → price table.

**Tech Stack:** Python 3.12, SQLAlchemy (KernelBase ORM), APScheduler, FastAPI, httpx, Next.js, Vitest, Pytest

## Global Constraints

- `PHASE12_FUTURES_MARKETS_ENABLED` feature flag must default to OFF
- `PredictionKernel`, `domain.py`, `LearningService`, `edge_detector_service.py`, `sport_recommendation_service.py`, `market_settlement_service.py`, `sport_market_bridge_service.py`, `prediction_kernel.py`, all `engines/*.py` files must NOT be modified
- New database tables must use `kernel_` prefix (`kernel_futures_links`, `kernel_futures_snapshots`)
- Existing tables must NOT be structurally modified (only additive model classes appended to `kernel_db.py`)
- All existing frontend pages must NOT be modified (only new components + new route page)
- API endpoints must use Pydantic type annotations for request payloads
- All async functions must properly use `await` for asynchronous operations
- Stores follow keyword-only args, session-per-call, fail-closed reads pattern
- Feature flags must default to OFF to maintain backward compatibility
- `.env.example` variable names must match code configuration
- Do NOT push to origin (standing instruction)
- TDD strictly followed (RED → GREEN → COMMIT per task)
- Kalshi API is read-only, no auth needed (uses `settings.KALSHI_API_URL`)

---

## File Structure

### New files (backend)
1. `backend/app/services/futures_market_source.py` — `fetch_kalshi_futures_markets(limit)` async function
2. `backend/app/kernel/futures_link_store.py` — `FuturesLinkStore` CRUD class
3. `backend/app/kernel/futures_market_service.py` — `FuturesMarketService` orchestrator
4. `backend/app/api/routes/futures.py` — 3 GET endpoints
5. `backend/tests/test_futures_market_source.py` — 4 tests
6. `backend/tests/test_futures_link_store.py` — 5 tests
7. `backend/tests/test_futures_market_service.py` — 4 tests
8. `backend/tests/test_futures_routes.py` — 3 tests
9. `backend/tests/test_futures_scheduler.py` — 2 tests

### Modified files (backend)
1. `backend/app/core/config.py:1131-1136` — add 3 new settings before `settings = Settings()`
2. `backend/app/kernel/kernel_db.py:337-354` — add 2 new ORM models after `KernelOptimizedParams`
3. `backend/app/core/scheduler.py:25-30` — add import; append 2 new job functions; register both in `start_scheduler()`
4. `backend/app/api/router.py:3` — add `futures` import + include_router line
5. `backend/.env.example` — append Phase 12 block

### New files (frontend)
1. `frontend/src/lib/futures-api.ts` — API client
2. `frontend/src/components/sports/futures/FuturesDashboard.tsx` — dashboard component
3. `frontend/src/components/sports/futures/FuturesDashboard.test.tsx` — 2 tests
4. `frontend/src/app/sports/futures/page.tsx` — route page

---

## Task 1: Futures Market Source + Config + .env.example

**Files:**
- Modify: `backend/app/core/config.py` (append before `settings = Settings()` at line 1137)
- Modify: `backend/.env.example` (append at end of file)
- Create: `backend/app/services/futures_market_source.py`
- Test: `backend/tests/test_futures_market_source.py`

**Interfaces:**
- Consumes: `settings.KALSHI_API_URL` from `app.core.config`, `settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS` for polite rate limit
- Produces: `async def fetch_kalshi_futures_markets(limit: int = 200) -> list[dict]` returning dicts with keys: `event_ticker`, `title`, `competition`, `championship_type`, `contracts` (each: `ticker`, `team`, `price`, `liquidity`, `volume`), `source="kalshi"`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_futures_market_source.py
"""Tests for futures_market_source — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.futures_market_source import (
    fetch_kalshi_futures_markets,
    _KALSHI_FUTURES_SERIES_PREFIXES,
    _extract_team_from_ticker,
    _parse_kalshi_price,
)


@pytest.fixture
def sample_kalshi_response():
    """Kalshi event with multiple markets (multi-leg futures event)."""
    return {
        "events": [
            {
                "series_ticker": "KXNBACHAMP",
                "event_ticker": "KXNBACHAMP-25JAN",
                "title": "NBA Championship 2024-25",
                "markets": [
                    {
                        "ticker": "KXNBACHAMP-LAL",
                        "title": "Lakers win NBA Championship",
                        "last_price_dollars": 0.18,
                        "yes_bid_dollars": 0.17,
                        "yes_ask_dollars": 0.19,
                        "liquidity_dollars": 50000,
                        "volume_fp": 12000,
                    },
                    {
                        "ticker": "KXNBACHAMP-BOS",
                        "title": "Celtics win NBA Championship",
                        "last_price_dollars": 0.32,
                        "yes_bid_dollars": 0.30,
                        "yes_ask_dollars": 0.34,
                        "liquidity_dollars": 80000,
                        "volume_fp": 25000,
                    },
                ],
            },
            {
                # Single-leg event — must be filtered out (len(markets) == 1)
                "series_ticker": "KXNBAGAME",
                "event_ticker": "KXNBAGAME-25JAN01-LAL-BOS",
                "title": "Lakers vs Celtics Jan 1",
                "markets": [
                    {"ticker": "KXNBAGAME-LAL", "last_price_dollars": 0.55},
                ],
            },
            {
                # Non-sports series — must be filtered out
                "series_ticker": "KXPRES",
                "event_ticker": "KXPRES-2024",
                "title": "Presidential Election",
                "markets": [
                    {"ticker": "KXPRES-DEM", "last_price_dollars": 0.50},
                    {"ticker": "KXPRES-REP", "last_price_dollars": 0.50},
                ],
            },
        ]
    }


@pytest.mark.asyncio
async def test_fetch_kalshi_futures_markets_filters_to_multi_leg_sports(sample_kalshi_response):
    with patch("app.services.futures_market_source.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock()
        instance.get.return_value.raise_for_status = lambda: None
        instance.get.return_value.json = lambda: sample_kalshi_response
        result = await fetch_kalshi_futures_markets(limit=10)
    # Only the NBA Championship futures event qualifies
    assert len(result) == 1
    event = result[0]
    assert event["event_ticker"] == "KXNBACHAMP-25JAN"
    assert event["competition"] == "nba"
    assert event["championship_type"] == "championship"
    assert event["source"] == "kalshi"
    assert len(event["contracts"]) == 2


@pytest.mark.asyncio
async def test_fetch_kalshi_futures_markets_returns_empty_on_error():
    with patch("app.services.futures_market_source.httpx.AsyncClient") as MockClient:
        MockClient.side_effect = RuntimeError("network down")
        result = await fetch_kalshi_futures_markets(limit=10)
    assert result == []


def test_extract_team_from_ticker():
    assert _extract_team_from_ticker("KXNBACHAMP-LAL") == "LAL"
    assert _extract_team_from_ticker("KXMLBCHAMP-NYY") == "NYY"
    assert _extract_team_from_ticker("KXNHLCHAMP-EDM") == "EDM"
    # No dash — return empty string
    assert _extract_team_from_ticker("KXNBACHAMP") == ""
    # Multiple dashes — take last segment
    assert _extract_team_from_ticker("KXSOCCERWCS-BRA") == "BRA"


def test_parse_kalshi_price_prefers_last_price():
    # last_price > 0 wins
    assert _parse_kalshi_price(0.18, 0.17, 0.19) == 0.18
    # Fall back to midpoint when last_price is 0/None
    assert _parse_kalshi_price(0, 0.30, 0.34) == 0.32
    # Fall back to 0.5 when all missing
    assert _parse_kalshi_price(0, 0, 0) == 0.5


def test_kalshi_futures_series_prefixes_covers_sports():
    # All 5 sports championships are mapped
    assert "KXNBACHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXMLBCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXNHLCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXSOCCERWCS" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXSOCCERUCL" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXNBACHAMP"] == ("nba", "championship")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXMLBCHAMP"] == ("mlb", "world_series")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXNHLCHAMP"] == ("nhl", "stanley_cup")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXSOCCERWCS"] == ("wc", "world_cup")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXSOCCERUCL"] == ("ucl", "champions_league")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_futures_market_source.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.futures_market_source'`

- [ ] **Step 3: Add config settings**

In `backend/app/core/config.py`, append before `settings = Settings()` (after Phase 11 block at line 1134):

```python
    # === Phase 12 — Futures/Championship Markets ===
    PHASE12_FUTURES_MARKETS_ENABLED: bool = _env_bool("PHASE12_FUTURES_MARKETS_ENABLED", "false")
    FUTURES_DISCOVERY_INTERVAL_MIN: int = int(os.getenv("FUTURES_DISCOVERY_INTERVAL_MIN", "60"))
    FUTURES_SNAPSHOT_INTERVAL_MIN: int = int(os.getenv("FUTURES_SNAPSHOT_INTERVAL_MIN", "5"))
```

- [ ] **Step 4: Append .env.example block**

Append to `backend/.env.example`:

```
# === Phase 12 — Futures/Championship Markets ===
# Multi-leg championship market support (e.g., "Who will win the NBA Championship 2024-25?").
# When the master flag is false, /api/futures/* return 503 and scheduler jobs are
# not registered. Reuses KALSHI_API_URL from Phase 11 — no new external API.
# Zero-invasion: no modifications to match-level market code, EdgeDetector, or engines.
PHASE12_FUTURES_MARKETS_ENABLED=false  # 中文：是否启用期货/冠军市场接口（/api/futures/*）；关闭时返回 503。
FUTURES_DISCOVERY_INTERVAL_MIN=60  # 中文：期货市场发现调度间隔（分钟）。
FUTURES_SNAPSHOT_INTERVAL_MIN=5  # 中文：期货价格快照采集调度间隔（分钟）。
```

- [ ] **Step 5: Implement futures_market_source.py**

```python
# backend/app/services/futures_market_source.py
"""Futures/championship market source — fetches multi-leg events from Kalshi.

Parallel to kalshi_sports_source.py (single-leg). Filters Kalshi events to
multi-leg championship series (KXNBACHAMP, KXMLBCHAMP, etc.) and extracts
one contract per team.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Kalshi futures series ticker prefixes — multi-leg championship events.
# Maps series prefix -> (competition, championship_type).
_KALSHI_FUTURES_SERIES_PREFIXES: dict[str, tuple[str, str]] = {
    "KXNBACHAMP": ("nba", "championship"),
    "KXMLBCHAMP": ("mlb", "world_series"),
    "KXNHLCHAMP": ("nhl", "stanley_cup"),
    "KXSOCCERWCS": ("wc", "world_cup"),
    "KXSOCCERUCL": ("ucl", "champions_league"),
}


def _extract_team_from_ticker(ticker: str) -> str:
    """Extract team code from a futures contract ticker.

    `KXNBACHAMP-LAL` -> "LAL". Returns "" if no dash present. If multiple
    dashes, takes the last segment (e.g., `KXSOCCERWCS-BRA` -> "BRA").
    """
    if not ticker or "-" not in ticker:
        return ""
    return ticker.rsplit("-", 1)[-1].strip()


def _parse_kalshi_price(last_price: float, yes_bid: float, yes_ask: float) -> float:
    """Parse Kalshi market price: last_price > midpoint > 0.5 fallback."""
    if last_price and last_price > 0:
        return float(last_price)
    if yes_bid > 0 and yes_ask > 0:
        return float((yes_bid + yes_ask) / 2)
    return 0.5


async def fetch_kalshi_futures_markets(limit: int = 200) -> list[dict[str, Any]]:
    """Fetch multi-leg championship events from Kalshi.

    Returns list of dicts with keys: event_ticker, title, competition,
    championship_type, contracts (list of {ticker, team, price, liquidity,
    volume}), source. Fail-closed: returns empty list on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                settings.KALSHI_API_URL,
                params={
                    "status": "open",
                    "with_nested_markets": "true",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            data = response.json()

        events = data.get("events", [])
        candidates: list[dict[str, Any]] = []

        for event in events:
            markets = event.get("markets", [])
            # Filter to multi-leg only (>=2 contracts = futures championship)
            if len(markets) < 2:
                continue

            series = (event.get("series_ticker") or "").upper()
            mapping = _KALSHI_FUTURES_SERIES_PREFIXES.get(series)
            if mapping is None:
                continue

            competition, championship_type = mapping
            event_ticker = event.get("event_ticker", "")
            title = event.get("title", "") or event_ticker

            contracts: list[dict[str, Any]] = []
            for market in markets:
                ticker = market.get("ticker", "")
                if not ticker:
                    continue
                team = _extract_team_from_ticker(ticker)
                if not team:
                    continue
                price = _parse_kalshi_price(
                    market.get("last_price_dollars", 0) or 0,
                    market.get("yes_bid_dollars", 0) or 0,
                    market.get("yes_ask_dollars", 0) or 0,
                )
                contracts.append({
                    "ticker": ticker,
                    "team": team,
                    "price": price,
                    "liquidity": float(market.get("liquidity_dollars", 0) or 0),
                    "volume": float(market.get("volume_fp", 0) or 0),
                })

            if not contracts:
                continue

            candidates.append({
                "event_ticker": event_ticker,
                "title": title,
                "competition": competition,
                "championship_type": championship_type,
                "contracts": contracts,
                "source": "kalshi",
            })

            # Polite rate limit
            await asyncio.sleep(settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS)

        return candidates

    except Exception:
        logger.warning("Failed to fetch Kalshi futures markets", exc_info=True)
        return []
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_futures_market_source.py -v --tb=short`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/futures_market_source.py backend/app/core/config.py backend/.env.example backend/tests/test_futures_market_source.py
git commit -m "feat(phase12): add futures market source + config + .env.example"
```

---

## Task 2: DB Models + FuturesLinkStore

**Files:**
- Modify: `backend/app/kernel/kernel_db.py:355-356` (append 2 new ORM models after `KernelOptimizedParams`)
- Create: `backend/app/kernel/futures_link_store.py`
- Test: `backend/tests/test_futures_link_store.py`

**Interfaces:**
- Consumes: `KernelBase`, `get_kernel_session` from `kernel_db.py`; `_get_engine` for test isolation
- Produces:
  - ORM models: `KernelFuturesLink`, `KernelFuturesSnapshot`
  - `FuturesLinkStore` class with methods:
    - `upsert_link(*, competition, season, team, contract_id, source, market_question, implied_prob, verified) -> dict`
    - `get_links(competition: str, season: str) -> list[dict]`
    - `get_verified_links() -> list[dict]`
    - `append_snapshot(*, link_id, implied_prob, price, liquidity, volume, captured_at) -> dict`
    - `get_latest_snapshots(competition: str, season: str) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_futures_link_store.py
"""Tests for FuturesLinkStore — TDD RED phase."""
from datetime import datetime, timezone

import pytest

from app.kernel.futures_link_store import FuturesLinkStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Create a store with an isolated SQLite DB."""
    db_path = str(tmp_path / "test_futures.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db
    kernel_db.KernelBase.metadata.create_all(kernel_db._get_engine(db_path))

    # Patch get_kernel_session to use this isolated engine
    engine = kernel_db._get_engine(db_path)
    from sqlalchemy.orm import sessionmaker
    SessionFactory = sessionmaker(bind=engine)

    monkeypatch.setattr(
        "app.kernel.futures_link_store.get_kernel_session",
        lambda: SessionFactory(),
    )
    return FuturesLinkStore()


def test_upsert_link_inserts_and_returns_record(store):
    result = store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="Lakers win NBA Championship",
        implied_prob=0.18, verified=True,
    )
    assert result["id"] is not None
    assert result["competition"] == "nba"
    assert result["team"] == "LAL"
    assert result["verified"] is True


def test_upsert_link_updates_existing(store):
    store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="old question",
        implied_prob=0.18, verified=False,
    )
    updated = store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="new question",
        implied_prob=0.22, verified=True,
    )
    # Same row, updated values
    links = store.get_links("nba", "2024-25")
    assert len(links) == 1
    assert links[0]["implied_prob"] == 0.22
    assert links[0]["market_question"] == "new question"
    assert links[0]["verified"] is True


def test_get_links_filters_by_competition_and_season(store):
    store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="",
        implied_prob=0.18, verified=True,
    )
    store.upsert_link(
        competition="mlb", season="2024",
        team="NYY", contract_id="KXMLBCHAMP-NYY",
        source="kalshi", market_question="",
        implied_prob=0.10, verified=True,
    )
    nba_links = store.get_links("nba", "2024-25")
    assert len(nba_links) == 1
    assert nba_links[0]["team"] == "LAL"
    mlb_links = store.get_links("mlb", "2024")
    assert len(mlb_links) == 1
    assert mlb_links[0]["team"] == "NYY"


def test_get_verified_links_returns_only_verified(store):
    store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="",
        implied_prob=0.18, verified=True,
    )
    store.upsert_link(
        competition="nba", season="2024-25",
        team="BOS", contract_id="KXNBACHAMP-BOS",
        source="kalshi", market_question="",
        implied_prob=0.32, verified=False,
    )
    verified = store.get_verified_links()
    assert len(verified) == 1
    assert verified[0]["team"] == "LAL"


def test_append_snapshot_and_get_latest(store):
    link = store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="",
        implied_prob=0.18, verified=True,
    )
    t1 = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc)
    store.append_snapshot(
        link_id=link["id"], implied_prob=0.18, price=0.18,
        liquidity=50000.0, volume=12000.0, captured_at=t1,
    )
    store.append_snapshot(
        link_id=link["id"], implied_prob=0.22, price=0.22,
        liquidity=55000.0, volume=13000.0, captured_at=t2,
    )
    latest = store.get_latest_snapshots("nba", "2024-25")
    assert len(latest) == 1
    # Latest snapshot is the most recent one (t2)
    assert latest[0]["implied_prob"] == 0.22
    assert latest[0]["team"] == "LAL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_futures_link_store.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.futures_link_store'`

- [ ] **Step 3: Add ORM models to kernel_db.py**

In `backend/app/kernel/kernel_db.py`, append after the `KernelOptimizedParams` class (line 354) and before `def _get_engine`:

```python
class KernelFuturesLink(KernelBase):
    """Futures/championship market link (competition+season+team -> contract).

    Distinct from KernelSportMarketLink which is match-level (match_id).
    Futures markets are season-level: one event -> N contracts (one per team).
    """
    __tablename__ = "kernel_futures_links"
    __table_args__ = (
        UniqueConstraint(
            "competition", "season", "team", "source",
            name="uq_futures_links_comp_season_team_source"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    competition = Column(String, nullable=False, index=True)
    season = Column(String, nullable=False, index=True)
    team = Column(String, nullable=False)
    contract_id = Column(String, nullable=False)
    source = Column(String, nullable=False)
    market_question = Column(String, nullable=True)
    implied_prob = Column(Float, nullable=True)
    verified = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KernelFuturesSnapshot(KernelBase):
    """Price snapshot for a futures link (one row per capture)."""
    __tablename__ = "kernel_futures_snapshots"
    __table_args__ = (
        Index("ix_futures_snapshots_link_id", "link_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, nullable=False)
    implied_prob = Column(Float, nullable=False)
    price = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    captured_at = Column(DateTime, nullable=False)
```

- [ ] **Step 4: Implement FuturesLinkStore**

```python
# backend/app/kernel/futures_link_store.py
"""Persistence for futures/championship market links (Phase 12).

Mirrors SportMarketLinkStore pattern: keyword-only args, session-per-call,
fail-closed reads. Distinct table (kernel_futures_links) because futures
markets are season-level (competition+season+team), not match-level.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.kernel.kernel_db import (
    KernelFuturesLink,
    KernelFuturesSnapshot,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _link_row_to_dict(row: KernelFuturesLink) -> dict[str, Any]:
    return {
        "id": row.id,
        "competition": row.competition,
        "season": row.season,
        "team": row.team,
        "contract_id": row.contract_id,
        "source": row.source,
        "market_question": row.market_question,
        "implied_prob": row.implied_prob,
        "verified": bool(row.verified),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _snapshot_row_to_dict(row: KernelFuturesSnapshot, *, team: str | None = None) -> dict[str, Any]:
    d = {
        "id": row.id,
        "link_id": row.link_id,
        "implied_prob": row.implied_prob,
        "price": row.price,
        "liquidity": row.liquidity,
        "volume": row.volume,
        "captured_at": row.captured_at,
    }
    if team is not None:
        d["team"] = team
    return d


class FuturesLinkStore:
    """CRUD facade over KernelFuturesLink and KernelFuturesSnapshot.

    All methods open a short session and close it in finally, mirroring
    SportMarketLinkStore.
    """

    def upsert_link(
        self,
        *,
        competition: str,
        season: str,
        team: str,
        contract_id: str,
        source: str,
        market_question: str | None,
        implied_prob: float,
        verified: bool,
    ) -> dict[str, Any]:
        """Insert or update by (competition, season, team, source)."""
        now = _utcnow()
        session = get_kernel_session()
        try:
            existing = (
                session.query(KernelFuturesLink)
                .filter_by(
                    competition=competition,
                    season=season,
                    team=team,
                    source=source,
                )
                .one_or_none()
            )
            if existing is not None:
                existing.contract_id = contract_id
                existing.market_question = market_question
                existing.implied_prob = implied_prob
                existing.verified = 1 if verified else 0
                existing.updated_at = now
                session.commit()
                session.refresh(existing)
                return _link_row_to_dict(existing)
            row = KernelFuturesLink(
                competition=competition,
                season=season,
                team=team,
                contract_id=contract_id,
                source=source,
                market_question=market_question,
                implied_prob=implied_prob,
                verified=1 if verified else 0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _link_row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_links(self, competition: str, season: str) -> list[dict[str, Any]]:
        """Return all links for a competition+season pair. Fail-closed: [] on error."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelFuturesLink)
                .filter_by(competition=competition, season=season)
                .all()
            )
            return [_link_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_verified_links(self) -> list[dict[str, Any]]:
        """Return all verified futures links. Fail-closed: [] on error."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelFuturesLink)
                .filter_by(verified=1)
                .all()
            )
            return [_link_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def append_snapshot(
        self,
        *,
        link_id: int,
        implied_prob: float,
        price: float | None,
        liquidity: float | None,
        volume: float | None,
        captured_at: datetime,
    ) -> dict[str, Any]:
        """Append a new snapshot row for a link."""
        session = get_kernel_session()
        try:
            row = KernelFuturesSnapshot(
                link_id=link_id,
                implied_prob=implied_prob,
                price=price,
                liquidity=liquidity,
                volume=volume,
                captured_at=captured_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _snapshot_row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_latest_snapshots(self, competition: str, season: str) -> list[dict[str, Any]]:
        """Return the most recent snapshot per link for a competition+season.

        Uses a correlated subquery to pick the row with max captured_at per
        link_id. Fail-closed: [] on error.
        """
        session = get_kernel_session()
        try:
            # Get all links for this competition+season
            links = (
                session.query(KernelFuturesLink)
                .filter_by(competition=competition, season=season)
                .all()
            )
            if not links:
                return []
            link_ids = [l.id for l in links]
            team_by_id = {l.id: l.team for l in links}

            # For each link, get the latest snapshot
            result: list[dict[str, Any]] = []
            for link_id in link_ids:
                row = (
                    session.query(KernelFuturesSnapshot)
                    .filter_by(link_id=link_id)
                    .order_by(KernelFuturesSnapshot.captured_at.desc())
                    .first()
                )
                if row is not None:
                    result.append(_snapshot_row_to_dict(row, team=team_by_id.get(link_id)))
            return result
        except Exception:
            return []
        finally:
            session.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_futures_link_store.py -v --tb=short`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/kernel/kernel_db.py backend/app/kernel/futures_link_store.py backend/tests/test_futures_link_store.py
git commit -m "feat(phase12): add KernelFuturesLink/Snapshot models + FuturesLinkStore"
```

---

## Task 3: FuturesMarketService

**Files:**
- Create: `backend/app/kernel/futures_market_service.py`
- Test: `backend/tests/test_futures_market_service.py`

**Interfaces:**
- Consumes: `fetch_kalshi_futures_markets` from `app.services.futures_market_source`, `FuturesLinkStore` from `app.kernel.futures_link_store`, `settings.PHASE12_FUTURES_MARKETS_ENABLED`
- Produces: `FuturesMarketService` class with methods:
  - `async def discover_and_link(self) -> dict` — fetch candidates from Kalshi, link each, return `{"discovered": int, "linked": int, "errors": int}`
  - `async def link_futures_market(self, candidate: dict) -> dict` — parse candidate, upsert one link per contract, return `{"links": int, "errors": int}`
  - `async def capture_snapshots(self) -> dict` — fetch fresh prices for verified links, store snapshots, return `{"captured": int, "errors": int}`
  - `def _parse_season_from_title(self, title: str) -> str` — extract season like "2024-25" from title; returns "" if no match

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_futures_market_service.py
"""Tests for FuturesMarketService — TDD RED phase."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.kernel.futures_market_service import FuturesMarketService


@pytest.fixture
def service():
    return FuturesMarketService()


@pytest.fixture
def sample_candidate():
    return {
        "event_ticker": "KXNBACHAMP-25JAN",
        "title": "NBA Championship 2024-25",
        "competition": "nba",
        "championship_type": "championship",
        "contracts": [
            {"ticker": "KXNBACHAMP-LAL", "team": "LAL", "price": 0.18, "liquidity": 50000, "volume": 12000},
            {"ticker": "KXNBACHAMP-BOS", "team": "BOS", "price": 0.32, "liquidity": 80000, "volume": 25000},
        ],
        "source": "kalshi",
    }


def test_parse_season_from_title_extracts_year_range(service):
    assert service._parse_season_from_title("NBA Championship 2024-25") == "2024-25"
    assert service._parse_season_from_title("Stanley Cup 2023-24") == "2023-24"
    assert service._parse_season_from_title("World Series 2024") == "2024"
    assert service._parse_season_from_title("No season here") == ""
    assert service._parse_season_from_title("") == ""


@pytest.mark.asyncio
async def test_link_futures_market_upserts_one_link_per_contract(service, sample_candidate):
    mock_store = MagicMock()
    mock_store.upsert_link = MagicMock(side_effect=[
        {"id": 1, "team": "LAL"},
        {"id": 2, "team": "BOS"},
    ])
    service._store = mock_store

    result = await service.link_futures_market(sample_candidate)

    assert result["links"] == 2
    assert result["errors"] == 0
    assert mock_store.upsert_link.call_count == 2
    # First call: LAL contract
    first_call_kwargs = mock_store.upsert_link.call_args_list[0].kwargs
    assert first_call_kwargs["team"] == "LAL"
    assert first_call_kwargs["contract_id"] == "KXNBACHAMP-LAL"
    assert first_call_kwargs["competition"] == "nba"
    assert first_call_kwargs["season"] == "2024-25"
    assert first_call_kwargs["verified"] is True


@pytest.mark.asyncio
async def test_discover_and_link_returns_counts(service, sample_candidate):
    mock_store = MagicMock()
    mock_store.upsert_link = MagicMock(return_value={"id": 1})
    service._store = mock_store

    with patch(
        "app.kernel.futures_market_service.fetch_kalshi_futures_markets",
        AsyncMock(return_value=[sample_candidate]),
    ):
        result = await service.discover_and_link()

    assert result["discovered"] == 1
    assert result["linked"] == 2  # 2 contracts in the candidate
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_capture_snapshots_stores_one_per_verified_link(service):
    mock_store = MagicMock()
    mock_store.get_verified_links = MagicMock(return_value=[
        {"id": 1, "contract_id": "KXNBACHAMP-LAL", "competition": "nba", "season": "2024-25", "team": "LAL", "source": "kalshi"},
        {"id": 2, "contract_id": "KXNBACHAMP-BOS", "competition": "nba", "season": "2024-25", "team": "BOS", "source": "kalshi"},
    ])
    mock_store.append_snapshot = MagicMock(return_value={"id": 100})
    service._store = mock_store

    with patch(
        "app.kernel.futures_market_service.fetch_kalshi_futures_markets",
        AsyncMock(return_value=[
            {
                "event_ticker": "KXNBACHAMP-25JAN",
                "title": "NBA Championship 2024-25",
                "competition": "nba",
                "championship_type": "championship",
                "contracts": [
                    {"ticker": "KXNBACHAMP-LAL", "team": "LAL", "price": 0.20, "liquidity": 51000, "volume": 12100},
                    {"ticker": "KXNBACHAMP-BOS", "team": "BOS", "price": 0.30, "liquidity": 79000, "volume": 24900},
                ],
                "source": "kalshi",
            }
        ]),
    ):
        result = await service.capture_snapshots()

    assert result["captured"] == 2
    assert result["errors"] == 0
    assert mock_store.append_snapshot.call_count == 2
    # Verify LAL snapshot was stored with updated price
    lal_call = mock_store.append_snapshot.call_args_list[0].kwargs
    assert lal_call["link_id"] == 1
    assert lal_call["implied_prob"] == 0.20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_futures_market_service.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.futures_market_service'`

- [ ] **Step 3: Implement FuturesMarketService**

```python
# backend/app/kernel/futures_market_service.py
"""Futures/championship market service (Phase 12).

Orchestrates discovery (Kalshi), linking (competition+season+team -> contract),
and price snapshot capture. Distinct from SportMarketBridgeService which
handles single-match binary markets.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.kernel.futures_link_store import FuturesLinkStore
from app.services.futures_market_source import fetch_kalshi_futures_markets

logger = logging.getLogger(__name__)

# Matches "2024-25" or "2024" in event titles
_SEASON_PATTERN = re.compile(r"\b(\d{4}-\d{2,4}|\d{4})\b")


class FuturesMarketService:
    """Discovers, links, and captures futures/championship market data."""

    def __init__(self, store: FuturesLinkStore | None = None) -> None:
        self._store = store or FuturesLinkStore()

    def _parse_season_from_title(self, title: str) -> str:
        """Extract a season string from an event title.

        Looks for patterns like "2024-25" or "2024" in the title. Returns ""
        if no match. Used to namespace futures links by season.
        """
        if not title:
            return ""
        match = _SEASON_PATTERN.search(title)
        if match is None:
            return ""
        return match.group(1)

    async def link_futures_market(self, candidate: dict[str, Any]) -> dict[str, int]:
        """Link a single futures candidate (one event -> N contracts).

        Upserts one FuturesLink per contract. Returns counts dict.
        """
        competition = candidate.get("competition", "")
        championship_type = candidate.get("championship_type", "")
        source = candidate.get("source", "kalshi")
        title = candidate.get("title", "")
        season = self._parse_season_from_title(title)
        contracts = candidate.get("contracts", [])

        linked = 0
        errors = 0
        for contract in contracts:
            try:
                team = contract.get("team", "")
                ticker = contract.get("ticker", "")
                price = float(contract.get("price", 0) or 0)
                if not team or not ticker:
                    errors += 1
                    continue
                market_question = f"{championship_type} - {team}" if championship_type else team
                self._store.upsert_link(
                    competition=competition,
                    season=season,
                    team=team,
                    contract_id=ticker,
                    source=source,
                    market_question=market_question,
                    implied_prob=price,
                    verified=True,  # Auto-verified: ticker prefix already implies sport
                )
                linked += 1
            except Exception:
                logger.warning("Failed to link futures contract", exc_info=True)
                errors += 1
        return {"links": linked, "errors": errors}

    async def discover_and_link(self) -> dict[str, int]:
        """Fetch futures candidates from Kalshi and link each.

        Returns counts: {"discovered": int, "linked": int, "errors": int}.
        """
        try:
            candidates = await fetch_kalshi_futures_markets(limit=200)
        except Exception:
            logger.warning("Failed to fetch Kalshi futures markets", exc_info=True)
            return {"discovered": 0, "linked": 0, "errors": 0}

        discovered = len(candidates)
        total_linked = 0
        total_errors = 0
        for candidate in candidates:
            try:
                result = await self.link_futures_market(candidate)
                total_linked += result["links"]
                total_errors += result["errors"]
            except Exception:
                logger.warning("Failed to link futures candidate", exc_info=True)
                total_errors += 1
        return {
            "discovered": discovered,
            "linked": total_linked,
            "errors": total_errors,
        }

    async def capture_snapshots(self) -> dict[str, int]:
        """Capture price snapshots for all verified futures links.

        Re-fetches Kalshi futures events to get fresh prices, then matches
        each verified link's contract_id to a fresh price. Returns counts.
        """
        verified = self._store.get_verified_links()
        if not verified:
            return {"captured": 0, "errors": 0}

        # Build contract_id -> contract price lookup from a fresh fetch
        try:
            candidates = await fetch_kalshi_futures_markets(limit=200)
        except Exception:
            logger.warning("Failed to fetch Kalshi futures markets for snapshots", exc_info=True)
            return {"captured": 0, "errors": len(verified)}

        price_by_ticker: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            for contract in candidate.get("contracts", []):
                ticker = contract.get("ticker", "")
                if ticker:
                    price_by_ticker[ticker] = contract

        captured = 0
        errors = 0
        now = datetime.now(timezone.utc)
        for link in verified:
            try:
                contract_id = link.get("contract_id", "")
                contract = price_by_ticker.get(contract_id)
                if contract is None:
                    errors += 1
                    continue
                self._store.append_snapshot(
                    link_id=link["id"],
                    implied_prob=float(contract.get("price", 0) or 0),
                    price=float(contract.get("price", 0) or 0),
                    liquidity=float(contract.get("liquidity", 0) or 0),
                    volume=float(contract.get("volume", 0) or 0),
                    captured_at=now,
                )
                captured += 1
            except Exception:
                logger.warning("Failed to capture futures snapshot", exc_info=True)
                errors += 1
        return {"captured": captured, "errors": errors}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_futures_market_service.py -v --tb=short`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kernel/futures_market_service.py backend/tests/test_futures_market_service.py
git commit -m "feat(phase12): add FuturesMarketService with discover/link/capture"
```

---

## Task 4: API Routes + Scheduler Jobs

**Files:**
- Create: `backend/app/api/routes/futures.py`
- Modify: `backend/app/api/router.py:3` (add `futures` import + include_router)
- Modify: `backend/app/core/scheduler.py` (add module-level import; append 2 job functions; register both in `start_scheduler`)
- Test: `backend/tests/test_futures_routes.py`
- Test: `backend/tests/test_futures_scheduler.py`

**Interfaces:**
- Consumes: `FuturesMarketService` from `app.kernel.futures_market_service`, `FuturesLinkStore` from `app.kernel.futures_link_store`, `settings.PHASE12_FUTURES_MARKETS_ENABLED`, `settings.FUTURES_DISCOVERY_INTERVAL_MIN`, `settings.FUTURES_SNAPSHOT_INTERVAL_MIN`
- Produces:
  - `futures.router` APIRouter with prefix `/futures` and 3 GET endpoints: `/{competition}/{season}`, `/{competition}/{season}/latest`, `/`
  - `_job_discover_futures_markets` async scheduler job
  - `_job_capture_futures_snapshots` async scheduler job

- [ ] **Step 1: Write the failing tests for routes**

```python
# backend/tests/test_futures_routes.py
"""Tests for futures API routes — TDD RED phase."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_phase12(monkeypatch):
    """Default: Phase 12 disabled -> 503."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", False)


def test_endpoints_return_503_when_disabled(client):
    resp = client.get("/api/futures/nba/2024-25")
    assert resp.status_code == 503


def test_get_futures_returns_links_when_enabled(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)

    mock_store = MagicMock()
    mock_store.get_links = MagicMock(return_value=[
        {
            "id": 1, "competition": "nba", "season": "2024-25",
            "team": "LAL", "contract_id": "KXNBACHAMP-LAL",
            "source": "kalshi", "market_question": "championship - LAL",
            "implied_prob": 0.18, "verified": True,
        },
    ])
    with patch(
        "app.api.routes.futures.FuturesLinkStore",
        return_value=mock_store,
    ):
        resp = client.get("/api/futures/nba/2024-25")
    assert resp.status_code == 200
    data = resp.json()
    assert data["competition"] == "nba"
    assert data["season"] == "2024-25"
    assert len(data["links"]) == 1
    assert data["links"][0]["team"] == "LAL"


def test_get_latest_snapshots_returns_data_when_enabled(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)

    mock_store = MagicMock()
    mock_store.get_latest_snapshots = MagicMock(return_value=[
        {
            "id": 100, "link_id": 1, "team": "LAL",
            "implied_prob": 0.22, "price": 0.22,
            "liquidity": 51000.0, "volume": 12100.0,
            "captured_at": "2026-07-16T11:00:00Z",
        },
    ])
    with patch(
        "app.api.routes.futures.FuturesLinkStore",
        return_value=mock_store,
    ):
        resp = client.get("/api/futures/nba/2024-25/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["competition"] == "nba"
    assert data["season"] == "2024-25"
    assert len(data["snapshots"]) == 1
    assert data["snapshots"][0]["team"] == "LAL"
    assert data["snapshots"][0]["implied_prob"] == 0.22
```

- [ ] **Step 2: Write the failing tests for scheduler**

```python
# backend/tests/test_futures_scheduler.py
"""Tests for futures scheduler jobs — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.scheduler import (
    _job_discover_futures_markets,
    _job_capture_futures_snapshots,
)


@pytest.mark.asyncio
async def test_discover_futures_job_skips_when_disabled(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", False)

    with patch(
        "app.kernel.futures_market_service.FuturesMarketService"
    ) as MockSvc:
        instance = MockSvc.return_value
        instance.discover_and_link = AsyncMock()
        await _job_discover_futures_markets()
        # Service must NOT be called when disabled
        instance.discover_and_link.assert_not_called()


@pytest.mark.asyncio
async def test_capture_futures_snapshots_job_calls_service_when_enabled(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)

    with patch(
        "app.core.scheduler.FuturesMarketService"
    ) as MockSvc:
        instance = MockSvc.return_value
        instance.capture_snapshots = AsyncMock(return_value={"captured": 3, "errors": 0})
        await _job_capture_futures_snapshots()
        instance.capture_snapshots.assert_awaited_once()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_futures_routes.py tests/test_futures_scheduler.py -v --tb=short`
Expected: FAIL with `ImportError: cannot import name '_job_discover_futures_markets' from 'app.core.scheduler'` (scheduler tests fail first; routes test fails on the 503 expectation because the route doesn't exist yet — actual error will be 404).

- [ ] **Step 4: Implement API routes**

```python
# backend/app/api/routes/futures.py
"""Futures/championship market API routes (Phase 12).

All endpoints gated by PHASE12_FUTURES_MARKETS_ENABLED (503 when false).
Read-only — no writes via API. Writes happen only via scheduler jobs.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.kernel.futures_link_store import FuturesLinkStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/futures", tags=["Futures Markets"])


def _check_enabled() -> None:
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        raise HTTPException(status_code=503, detail="Phase 12 futures markets disabled")


@router.get("/{competition}/{season}")
async def get_futures(competition: str, season: str) -> dict[str, Any]:
    """Get all futures market links for a competition+season pair."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_links(competition, season)
    return {
        "competition": competition,
        "season": season,
        "links": links,
    }


@router.get("/{competition}/{season}/latest")
async def get_latest_snapshots(competition: str, season: str) -> dict[str, Any]:
    """Get the latest price snapshot per team for a competition+season."""
    _check_enabled()
    store = FuturesLinkStore()
    snapshots = store.get_latest_snapshots(competition, season)
    return {
        "competition": competition,
        "season": season,
        "snapshots": snapshots,
    }


@router.get("")
async def list_available_futures() -> dict[str, Any]:
    """List all available (competition, season) pairs that have verified links."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_verified_links()
    # Deduplicate (competition, season) pairs
    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, str]] = []
    for link in links:
        key = (link["competition"], link["season"])
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"competition": link["competition"], "season": link["season"]})
    return {"pairs": pairs}
```

- [ ] **Step 5: Register the router**

In `backend/app/api/router.py`, modify line 3 to add `futures` to the import, and add an `include_router` line at the end:

```python
# app/api/router.py — v0.3.0
from fastapi import APIRouter
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges, sport_recommendations, sport_settlements, sport_odds, sport_optimization, realtime, futures

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(llm.router, prefix="/llm", tags=["LLM"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
api_router.include_router(predictions.router, tags=["Predictions"])
api_router.include_router(sport_markets.router, tags=["Sport Markets"])
api_router.include_router(sport_edges.router, tags=["Sport Edges"])
api_router.include_router(sport_recommendations.router, tags=["Sport Recommendations"])
api_router.include_router(sport_settlements.router, tags=["Sport Settlements"])
api_router.include_router(sport_odds.router, tags=["Sport Odds"])
api_router.include_router(sport_optimization.router, tags=["Sport Optimization"])
api_router.include_router(realtime.router, tags=["Realtime"])
api_router.include_router(futures.router, tags=["Futures Markets"])
```

- [ ] **Step 6: Add scheduler job functions**

In `backend/app/core/scheduler.py`:

Add to the imports section (around line 25, after the existing `from app.services.kalshi_sports_source import fetch_kalshi_sport_markets` line):

```python
from app.kernel.futures_market_service import FuturesMarketService
```

Append two new job functions after `_job_reoptimize_monthly` (around line 944, before `_summarize_prediction_update`):

```python
async def _job_discover_futures_markets():
    """Every FUTURES_DISCOVERY_INTERVAL_MIN: discover and link Kalshi futures markets."""
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        return
    logger.info("[Scheduler] Futures market discovery starting...")
    run_id = _start_run("futures_market_discover")
    try:
        from app.kernel.kernel_db import init_kernel_db
        init_kernel_db()
        service = FuturesMarketService()
        result = await service.discover_and_link()
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Futures market discovery: discovered=%d linked=%d errors=%d",
            result.get("discovered", 0),
            result.get("linked", 0),
            result.get("errors", 0),
        )
    except Exception as exc:
        logger.exception("[Scheduler] Futures market discovery failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_capture_futures_snapshots():
    """Every FUTURES_SNAPSHOT_INTERVAL_MIN: capture price snapshots for verified futures links."""
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        return
    logger.info("[Scheduler] Futures snapshot capture starting...")
    run_id = _start_run("futures_snapshots_capture")
    try:
        from app.kernel.kernel_db import init_kernel_db
        init_kernel_db()
        service = FuturesMarketService()
        result = await service.capture_snapshots()
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Futures snapshot capture: captured=%d errors=%d",
            result.get("captured", 0),
            result.get("errors", 0),
        )
    except Exception as exc:
        logger.exception("[Scheduler] Futures snapshot capture failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
```

In `start_scheduler()`, append before `scheduler.start()` (around line 1204):

```python
        if settings.PHASE12_FUTURES_MARKETS_ENABLED:
            scheduler.add_job(
                _job_discover_futures_markets,
                IntervalTrigger(minutes=settings.FUTURES_DISCOVERY_INTERVAL_MIN),
                id="futures_market_discover",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _job_capture_futures_snapshots,
                IntervalTrigger(minutes=settings.FUTURES_SNAPSHOT_INTERVAL_MIN),
                id="futures_snapshots_capture",
                replace_existing=True,
                max_instances=1,
            )
            logger.info(
                "[Scheduler] Registered futures jobs (discover@%dmin, snapshots@%dmin)",
                settings.FUTURES_DISCOVERY_INTERVAL_MIN,
                settings.FUTURES_SNAPSHOT_INTERVAL_MIN,
            )
```

- [ ] **Step 7: Run route tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_futures_routes.py -v --tb=short`
Expected: PASS (3 tests)

- [ ] **Step 8: Run scheduler tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_futures_scheduler.py -v --tb=short`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/routes/futures.py backend/app/api/router.py backend/app/core/scheduler.py backend/tests/test_futures_routes.py backend/tests/test_futures_scheduler.py
git commit -m "feat(phase12): add 3 API endpoints + 2 scheduler jobs for futures markets"
```

---

## Task 5: Frontend Dashboard + Page

**Files:**
- Create: `frontend/src/lib/futures-api.ts`
- Create: `frontend/src/components/sports/futures/FuturesDashboard.tsx`
- Create: `frontend/src/components/sports/futures/FuturesDashboard.test.tsx`
- Create: `frontend/src/app/sports/futures/page.tsx`

**Interfaces:**
- Consumes: `NEXT_PUBLIC_API_BASE_URL` env var; backend endpoints `GET /api/futures`, `GET /api/futures/{competition}/{season}`, `GET /api/futures/{competition}/{season}/latest`
- Produces: `FuturesDashboard` React component + `/sports/futures` route page

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/src/components/sports/futures/FuturesDashboard.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/futures-api", () => ({
  fetchAvailableFutures: vi.fn(),
  fetchLatestSnapshots: vi.fn(),
}));

import { FuturesDashboard } from "./FuturesDashboard";
import { fetchAvailableFutures, fetchLatestSnapshots } from "@/lib/futures-api";

describe("FuturesDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows empty state when no futures pairs available", async () => {
    vi.mocked(fetchAvailableFutures).mockResolvedValue({ pairs: [] });
    render(<FuturesDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders snapshots table when a pair is selected and data is available", async () => {
    vi.mocked(fetchAvailableFutures).mockResolvedValue({
      pairs: [{ competition: "nba", season: "2024-25" }],
    });
    vi.mocked(fetchLatestSnapshots).mockResolvedValue({
      competition: "nba",
      season: "2024-25",
      snapshots: [
        {
          id: 100,
          link_id: 1,
          team: "LAL",
          implied_prob: 0.22,
          price: 0.22,
          liquidity: 51000,
          volume: 12100,
          captured_at: "2026-07-16T11:00:00Z",
        },
      ],
    });
    render(<FuturesDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("snapshots-table")).toBeTruthy();
    });
    expect(screen.getByText("LAL")).toBeTruthy();
    // 0.22 renders via toFixed(4) as "0.2200"
    expect(screen.getByText("0.2200")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/futures/FuturesDashboard.test.tsx`
Expected: FAIL with `Cannot find module './FuturesDashboard'` or `@/lib/futures-api`

- [ ] **Step 3: Implement futures-api.ts**

```typescript
// frontend/src/lib/futures-api.ts
"use client";

export interface FuturesPair {
  competition: string;
  season: string;
}

export interface FuturesLink {
  id: number;
  competition: string;
  season: string;
  team: string;
  contract_id: string;
  source: string;
  market_question: string | null;
  implied_prob: number | null;
  verified: boolean;
}

export interface FuturesSnapshot {
  id: number;
  link_id: number;
  team?: string;
  implied_prob: number;
  price: number | null;
  liquidity: number | null;
  volume: number | null;
  captured_at: string;
}

export interface AvailableFuturesResponse {
  pairs: FuturesPair[];
}

export interface FuturesLinksResponse {
  competition: string;
  season: string;
  links: FuturesLink[];
}

export interface FuturesSnapshotsResponse {
  competition: string;
  season: string;
  snapshots: FuturesSnapshot[];
}

function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "";
  return base.replace(/\/api$/, "");
}

export async function fetchAvailableFutures(): Promise<AvailableFuturesResponse> {
  const url = `${getApiBase()}/api/futures`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export async function fetchFuturesLinks(
  competition: string,
  season: string
): Promise<FuturesLinksResponse> {
  const url = `${getApiBase()}/api/futures/${competition}/${season}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export async function fetchLatestSnapshots(
  competition: string,
  season: string
): Promise<FuturesSnapshotsResponse> {
  const url = `${getApiBase()}/api/futures/${competition}/${season}/latest`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}
```

- [ ] **Step 4: Implement FuturesDashboard.tsx**

```tsx
// frontend/src/components/sports/futures/FuturesDashboard.tsx
"use client";
import { useEffect, useState } from "react";
import {
  fetchAvailableFutures,
  fetchLatestSnapshots,
  type FuturesPair,
  type FuturesSnapshot,
} from "@/lib/futures-api";

export function FuturesDashboard() {
  const [pairs, setPairs] = useState<FuturesPair[] | null>(null);
  const [selected, setSelected] = useState<FuturesPair | null>(null);
  const [snapshots, setSnapshots] = useState<FuturesSnapshot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load available (competition, season) pairs on mount
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchAvailableFutures()
      .then((data) => {
        setPairs(data.pairs);
        setLoading(false);
        // Auto-select first pair if available
        if (data.pairs.length > 0) {
          setSelected(data.pairs[0]);
        }
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  // Load snapshots when a pair is selected
  useEffect(() => {
    if (!selected) return;
    setSnapshots(null);
    setError(null);
    fetchLatestSnapshots(selected.competition, selected.season)
      .then((data) => {
        setSnapshots(data.snapshots);
      })
      .catch((err) => {
        setError(err.message);
      });
  }, [selected]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!pairs || pairs.length === 0)
    return <div data-testid="empty">暂无期货市场数据</div>;

  return (
    <div className="space-y-6">
      <div className="flex gap-2">
        {pairs.map((p) => (
          <button
            key={`${p.competition}-${p.season}`}
            onClick={() => setSelected(p)}
            className={`px-3 py-1 rounded border ${
              selected?.competition === p.competition && selected?.season === p.season
                ? "bg-blue-600 text-white"
                : "bg-white text-black"
            }`}
          >
            {p.competition} {p.season}
          </button>
        ))}
      </div>

      {snapshots === null ? (
        <div>加载快照中...</div>
      ) : snapshots.length === 0 ? (
        <div>该赛事暂无快照数据</div>
      ) : (
        <div data-testid="snapshots-table" className="space-y-4">
          <h2 className="text-xl font-bold">
            {selected?.competition} {selected?.season} 最新价格
          </h2>
          <table className="w-full border-collapse border">
            <thead>
              <tr className="bg-gray-100">
                <th className="border p-2 text-left">Team</th>
                <th className="border p-2 text-left">Implied Prob</th>
                <th className="border p-2 text-left">Price</th>
                <th className="border p-2 text-left">Liquidity</th>
                <th className="border p-2 text-left">Volume</th>
                <th className="border p-2 text-left">Captured At</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((s) => (
                <tr key={s.id}>
                  <td className="border p-2">{s.team ?? "-"}</td>
                  <td className="border p-2">{s.implied_prob.toFixed(4)}</td>
                  <td className="border p-2">{s.price !== null ? s.price.toFixed(4) : "-"}</td>
                  <td className="border p-2">{s.liquidity ?? "-"}</td>
                  <td className="border p-2">{s.volume ?? "-"}</td>
                  <td className="border p-2">{s.captured_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Implement the route page**

```tsx
// frontend/src/app/sports/futures/page.tsx
"use client";
import { FuturesDashboard } from "@/components/sports/futures/FuturesDashboard";

export default function FuturesPage() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">期货/冠军市场</h1>
      <FuturesDashboard />
    </main>
  );
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/futures/FuturesDashboard.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/futures-api.ts frontend/src/components/sports/futures/ frontend/src/app/sports/futures/
git commit -m "feat(phase12): add futures dashboard frontend page + API client"
```

---

## Self-Review Checklist

### Spec coverage
- Section 5.1 (FuturesMarketSource) — Task 1 ✅
- Section 5.2 (FuturesMarketService) — Task 3 ✅
- Section 5.3 (FuturesLinkStore) — Task 2 ✅
- Section 5.4 (Scheduler jobs) — Task 4 ✅
- Section 6 (Data model — 2 tables) — Task 2 ✅
- Section 7 (Config — 3 settings) — Task 1 ✅
- Section 8 (API endpoints — 3 GET) — Task 4 ✅
- Section 9 (Frontend /sports/futures) — Task 5 ✅
- Section 10 (20 tests: 4+5+4+3+2 backend + 2 frontend) — Tasks 1-5 ✅
- Section 11 (Zero-invasion verified — no modifications to engines, kernel, domain, LearningService, EdgeDetector, Settlement, Recommendation, sport_market_bridge_service) — All tasks respect this ✅
- Section 12 (Success criteria — covered by tests + feature flag gating) ✅

### Placeholder scan
- No TBD/TODO/implement-later patterns found
- All test code is complete
- All implementation code blocks show full content

### Type consistency
- `FuturesLinkStore.upsert_link(*, competition, season, team, contract_id, source, market_question, implied_prob, verified)` — consistent across Task 2 (definition) and Task 3 (usage in `link_futures_market`)
- `FuturesMarketService.__init__(self, store: FuturesLinkStore | None = None)` — Task 3 definition; Task 4 scheduler instantiates via `FuturesMarketService()` (default None)
- `fetch_kalshi_futures_markets(limit: int = 200) -> list[dict]` — Task 1 definition; Task 3 imports and calls it
- API route paths: `/{competition}/{season}`, `/{competition}/{season}/latest`, `` (empty prefix → `/futures`) — Task 4 definition; Task 5 frontend calls `/api/futures`, `/api/futures/{competition}/{season}/latest` — consistent
- Snapshot dict shape: `{id, link_id, implied_prob, price, liquidity, volume, captured_at, team?}` — Task 2 store returns this; Task 5 frontend type matches

All checks pass. Ready for execution.
