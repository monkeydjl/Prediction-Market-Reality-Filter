# Phase 12: Futures/Championship Markets — Design Spec

**Date:** 2026-07-16
**Status:** Approved (autonomous design per standing authorization)
**Predecessor:** Phase 11 (Kalshi Sports Market Integration)

## 1. Goal

Add support for futures/championship markets (e.g., "Who will win the NBA Championship 2024-25?"). These are multi-leg markets with N outcomes (one per team), as opposed to the existing single-match binary markets. The system discovers futures markets from Kalshi and Polymarket, links them to competition+season+team, captures price snapshots, and exposes them via API and a frontend page.

## 2. Background

- The existing market bridge (Phase 7/11) handles single-match binary markets: one match → one market contract → YES/NO outcome.
- Futures markets have one event → N contracts (one per team), each with its own price.
- Kalshi futures tickers: `KXNBACHAMP-LAL`, `KXSOCCERWCS-BRA`, etc.
- Polymarket futures: "NBA Championship" markets with multiple team outcome tokens.
- The existing `KernelSportMarketLink` table is match-level (`match_id` column) — not suitable for season-level futures.
- No changes to predictions, EdgeDetector, or Settlement — this phase is market data only (no model comparison).

## 3. Non-Goals

- Futures/championship predictions (model probability for "who wins championship") — future phase.
- Edge detection or recommendations for futures — future phase.
- Settlement for futures markets — future phase.
- Modifying existing match-level market code — zero-invasion.
- Multi-sport futures (e.g., "most gold medals at Olympics") — only league championships.

## 4. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Scheduler (_job_discover_futures_markets)                │
│  ├─ Kalshi futures source (multi-leg events)             │
│  └─ Polymarket futures source (multi-outcome markets)    │
│                            │                              │
│  FuturesMarketService.link_futures_market                │
│  ├─ Parse event title → competition + season             │
│  ├─ Parse contract ticker → team name                    │
│  ├─ Resolve team via team_aliases                        │
│  └─ Store in kernel_futures_links                        │
│                            │                              │
│  kernel_futures_links (competition, season, team, ...)   │
│                            │                              │
│  _job_capture_futures_snapshots                          │
│  → Fetches prices for all verified futures links         │
│  → Stores in kernel_futures_snapshots                    │
│                            │                              │
│  API: GET /api/futures/{competition}/{season}            │
│  → Returns team → latest price mapping                   │
│                            │                              │
│  Frontend: /sports/futures page                          │
│  → Table: Team | Market Price | Source | Last Updated    │
└──────────────────────────────────────────────────────────┘
```

## 5. Components

### 5.1 Futures Market Source (`backend/app/services/futures_market_source.py`)

New file. Fetches multi-leg championship events from Kalshi and Polymarket.

```python
KALSHI_FUTURES_SERIES_PREFIXES = {
    "KXNBACHAMP": ("nba", "championship"),
    "KXMLBCHAMP": ("mlb", "world_series"),
    "KXNHLCHAMP": ("nhl", "stanley_cup"),
    "KXSOCCERWCS": ("wc", "world_cup"),
    "KXSOCCERUCL": ("ucl", "champions_league"),
}

async def fetch_kalshi_futures_markets(limit: int = 200) -> list[dict]:
    """Fetch multi-leg championship events from Kalshi.

    Returns list of dicts:
    {
        "event_ticker": str,
        "title": str,
        "competition": str,      # nba, mlb, nhl, wc, ucl
        "championship_type": str, # championship, world_series, etc.
        "contracts": [
            {
                "ticker": str,     # e.g., KXNBACHAMP-LAL
                "team": str,       # extracted from ticker
                "price": float,    # 0-1
                "liquidity": float,
                "volume": float,
            }
        ],
        "source": "kalshi",
    }
    """
```

**Design decisions:**
- Filters Kalshi events by series ticker prefixes (KXNBACHAMP, KXMLBCHAMP, etc.)
- Only includes multi-leg events (`len(markets) > 1`)
- Extracts team name from ticker suffix (e.g., `KXNBACHAMP-LAL` → "LAL")
- Parses price using same logic as `kalshi_sports_source.py`

### 5.2 FuturesMarketService (`backend/app/kernel/futures_market_service.py`)

```python
class FuturesMarketService:
    """Discovers, links, and captures futures/championship market data."""

    async def discover_and_link(self) -> dict:
        """Fetch futures candidates and link them to competition+season+team."""

    async def link_futures_market(self, candidate: dict) -> dict:
        """Link a futures event to competition+season+team entries."""

    async def capture_snapshots(self) -> dict:
        """Capture price snapshots for all verified futures links."""

    def _parse_season_from_title(self, title: str) -> str:
        """Extract season from event title (e.g., '2024-25')."""
```

### 5.3 Futures Link Store (`backend/app/kernel/futures_link_store.py`)

```python
class FuturesLinkStore:
    """CRUD for kernel_futures_links table."""

    def upsert_link(self, *, competition, season, team, contract_id, source,
                    market_question, implied_prob, verified) -> dict: ...

    def get_links(self, competition: str, season: str) -> list[dict]: ...

    def get_verified_links(self) -> list[dict]: ...

    def get_latest_snapshots(self, competition: str, season: str) -> list[dict]: ...
```

### 5.4 Scheduler Jobs (`backend/app/core/scheduler.py`)

Two new jobs (additive):
- `_job_discover_futures_markets` — runs every `FUTURES_DISCOVERY_INTERVAL_MIN` (default 60)
- `_job_capture_futures_snapshots` — runs every `FUTURES_SNAPSHOT_INTERVAL_MIN` (default 5)

Both gated by `PHASE12_FUTURES_MARKETS_ENABLED`.

## 6. Data Model

### New table `kernel_futures_links`
```sql
CREATE TABLE kernel_futures_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competition TEXT NOT NULL,          -- nba, mlb, nhl, wc, ucl
    season TEXT NOT NULL,               -- 2024-25
    team TEXT NOT NULL,                 -- LAL, BOS, BRA, etc.
    contract_id TEXT NOT NULL,          -- Kalshi ticker or Polymarket token
    source TEXT NOT NULL,               -- kalshi, polymarket
    market_question TEXT,
    implied_prob REAL,
    verified INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(competition, season, team, source)
);
```

### New table `kernel_futures_snapshots`
```sql
CREATE TABLE kernel_futures_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id INTEGER NOT NULL,
    implied_prob REAL NOT NULL,
    price REAL,
    liquidity REAL,
    volume REAL,
    captured_at DATETIME NOT NULL,
    FOREIGN KEY (link_id) REFERENCES kernel_futures_links(id),
    INDEX(link_id)
);
```

## 7. Config Changes

```python
# === Phase 12 — Futures/Championship Markets ===
PHASE12_FUTURES_MARKETS_ENABLED: bool = _env_bool("PHASE12_FUTURES_MARKETS_ENABLED", "false")
FUTURES_DISCOVERY_INTERVAL_MIN: int = int(os.getenv("FUTURES_DISCOVERY_INTERVAL_MIN", "60"))
FUTURES_SNAPSHOT_INTERVAL_MIN: int = int(os.getenv("FUTURES_SNAPSHOT_INTERVAL_MIN", "5"))
```

## 8. API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/futures/{competition}/{season}` | None | Get futures market data (all teams) |
| GET | `/api/futures/{competition}/{season}/latest` | None | Get latest snapshots |
| GET | `/api/futures` | None | List all available futures (competition+season pairs) |

All gated by `PHASE12_FUTURES_MARKETS_ENABLED` (503 when disabled).

## 9. Frontend

New page `/sports/futures`:
- Dropdown: select competition + season
- Table: Team | Market Price | Source | Last Updated
- No real-time updates (HTTP only for this phase)

## 10. Testing Strategy

1. `test_futures_market_source.py` (4 tests) — Kalshi futures fetching, team extraction, price parsing
2. `test_futures_link_store.py` (5 tests) — CRUD operations
3. `test_futures_market_service.py` (4 tests) — discovery, linking, snapshot capture
4. `test_futures_routes.py` (3 tests) — API endpoint gating, data retrieval
5. `test_futures_scheduler.py` (2 tests) — scheduler job registration and execution
6. Frontend tests (2 tests) — page rendering, empty state

**Total: 20 tests**

## 11. Phase Boundaries

### Zero-invasion (must NOT modify):
- `backend/app/kernel/edge_detector_service.py`
- `backend/app/kernel/sport_recommendation_service.py`
- `backend/app/kernel/market_settlement_service.py`
- `backend/app/kernel/sport_market_bridge_service.py` (existing match-level code)
- `backend/app/kernel/prediction_kernel.py`
- `backend/app/kernel/domain.py`
- `backend/app/kernel/learning_service.py`
- `backend/app/sports/*/engines/*.py`
- All existing frontend pages (only new page added)
- `backend/app/services/kalshi_sports_source.py` (Phase 11 — single-leg only)
- `backend/app/services/polymarket_sports_source.py`

### Allowed modifications:
- `backend/app/core/config.py` — add 3 new settings
- `backend/app/core/scheduler.py` — add 2 new jobs (additive)
- `backend/app/api/router.py` — register futures router
- `.env.example` — add 3 new env vars

### New files:
- `backend/app/services/futures_market_source.py`
- `backend/app/kernel/futures_market_service.py`
- `backend/app/kernel/futures_link_store.py`
- `backend/app/api/routes/futures.py`
- `backend/app/kernel/kernel_db.py` — add 2 new models (additive, no existing model changes)
- `frontend/src/lib/futures-api.ts`
- `frontend/src/components/sports/futures/FuturesDashboard.tsx`
- `frontend/src/app/sports/futures/page.tsx`
- 5 backend test files + 1 frontend test file

## 12. Success Criteria

1. ✅ When `PHASE12_FUTURES_MARKETS_ENABLED=true`, scheduler discovers futures markets from Kalshi.
2. ✅ Futures links are stored with competition+season+team mapping.
3. ✅ Price snapshots are captured periodically for verified futures links.
4. ✅ API returns futures market data per competition+season.
5. ✅ Frontend `/sports/futures` page displays futures data.
6. ✅ When disabled, all endpoints return 503, scheduler skips jobs.
7. ✅ All 20 tests pass.
8. ✅ Zero-invasion: no modifications to match-level market code, engines, kernel, domain, LearningService.

## 13. Estimate

- 5 tasks, 20 tests, ~10 new files + 4 modified files
- ~1,500 lines of new code
