# Phase 11: Kalshi Sports Market Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Integrate Kalshi as a new sports market data source alongside Polymarket, using the existing three-layer matching engine and source-agnostic EdgeDetector.

**Architecture:** New `kalshi_sports_source.py` fetches sports markets → `SportMarketBridgeService.link_kalshi_market` runs three-layer matching → links stored with `source="kalshi"` → existing snapshot capture + EdgeDetector pipeline handles the rest.

**Tech Stack:** Python 3.12, httpx, FastAPI, SQLAlchemy, Pytest

## Global Constraints

- `PHASE11_KALSHI_SPORTS_ENABLED` feature flag must default to OFF
- `EdgeDetectorService`, `SportRecommendationService`, `MarketSettlementService`, `PredictionKernel`, `domain.py`, `LearningService`, all `engines/*.py` must NOT be modified
- `kalshi_event_source.py` (existing generic Kalshi source) must NOT be modified
- `polymarket_sports_source.py`, `polymarket_service.py`, `odds_api_service.py`, `sport_market_detector.py` must NOT be modified
- All frontend files must NOT be modified
- Kalshi discovery in scheduler must be additive (try/except wrapped, cannot break Polymarket discovery)
- New code uses `source="kalshi"` in `KernelSportMarketLink.source` field
- No new database tables — reuse existing `kernel_sport_market_links`
- API endpoints must use Pydantic type annotations where applicable
- All async functions must properly use `await`
- `.env.example` variable names must match code configuration
- Do NOT push to origin (standing instruction)
- TDD strictly followed (RED → GREEN → COMMIT per task)
- Kalshi API is read-only, no auth needed (uses `KALSHI_API_URL` from config)

---

## File Structure

### New files
1. `backend/app/services/kalshi_sports_source.py` — Kalshi sports market fetcher
2. `backend/tests/test_kalshi_sports_source.py` — 5 tests
3. `backend/tests/test_kalshi_bridge_integration.py` — 4 tests
4. `backend/tests/test_kalshi_scheduler.py` — 3 tests

### Modified files
1. `backend/app/core/config.py` — add 3 new settings
2. `backend/app/kernel/sport_market_bridge_service.py` — add `link_kalshi_market` + `_fetch_kalshi_price` + dispatch in `capture_snapshots`
3. `backend/app/core/scheduler.py` — add Kalshi discovery in `_job_discover_sport_markets`
4. `.env.example` (or `backend/.env.example`) — add 3 new env vars

---

## Task 1: Kalshi Sports Source + Config + .env.example

**Files:**
- Create: `backend/app/services/kalshi_sports_source.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_kalshi_sports_source.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`, `app.core.config.settings`, `app.services.sport_market_detector.detect_sport_market`
- Produces: `fetch_kalshi_sport_markets(limit: int = 100) -> list[dict]` with output structure compatible with `SportMarketBridgeService.link_kalshi_market`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_kalshi_sports_source.py
"""Tests for Kalshi sports market source — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.kalshi_sports_source import fetch_kalshi_sport_markets


def _make_kalshi_event(ticker="KXNBAGAME-25JAN01-LAL-BOS", title="Lakers vs Celtics Jan 1",
                       last_price=0.65, yes_bid=0.63, yes_ask=0.67,
                       liquidity=5000.0, volume=12000.0, status="open"):
    """Build a Kalshi event dict matching the API response shape."""
    return {
        "event_ticker": ticker,
        "series_ticker": ticker.split("-")[0],
        "title": title,
        "markets": [{
            "ticker": ticker,
            "title": title,
            "last_price_dollars": last_price,
            "yes_bid_dollars": yes_bid,
            "yes_ask_dollars": yes_ask,
            "liquidity_dollars": liquidity,
            "volume_fp": volume,
            "status": status,
            "close_time": "2025-01-01T23:59:59Z",
        }],
    }


@pytest.mark.asyncio
async def test_returns_empty_list_on_api_failure():
    """Fail-closed: API errors return empty list."""
    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=RuntimeError("network error"))
        mock_client_cls.return_value = mock_client

        result = await fetch_kalshi_sport_markets(limit=10)
        assert result == []


@pytest.mark.asyncio
async def test_filters_to_single_leg_events():
    """Multi-leg events (championships) are excluded."""
    multi_leg_event = _make_kalshi_event()
    multi_leg_event["markets"] = [
        {"ticker": "KXNBAGAME-25JAN01-LAL-BOS", "last_price_dollars": 0.65, "yes_bid_dollars": 0.63, "yes_ask_dollars": 0.67, "liquidity_dollars": 5000, "volume_fp": 12000, "status": "open"},
        {"ticker": "KXNBAGAME-25JAN01-LAL-BOS-NO", "last_price_dollars": 0.35, "yes_bid_dollars": 0.33, "yes_ask_dollars": 0.37, "liquidity_dollars": 5000, "volume_fp": 12000, "status": "open"},
    ]
    single_leg_event = _make_kalshi_event()

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [multi_leg_event, single_leg_event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1  # Only single-leg event


@pytest.mark.asyncio
async def test_parses_last_price_as_implied_prob():
    """last_price_dollars is used as the YES implied_prob."""
    event = _make_kalshi_event(last_price=0.72)

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1
    assert result[0]["price"] == pytest.approx(0.72)
    assert result[0]["no_price"] == pytest.approx(0.28)


@pytest.mark.asyncio
async def test_falls_back_to_bid_ask_midpoint():
    """When last_price is 0 or missing, use (yes_bid + yes_ask) / 2."""
    event = _make_kalshi_event(last_price=0.0, yes_bid=0.60, yes_ask=0.64)

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1
    assert result[0]["price"] == pytest.approx(0.62)  # (0.60 + 0.64) / 2


@pytest.mark.asyncio
async def test_output_includes_source_kalshi():
    """Output dicts include source='kalshi'."""
    event = _make_kalshi_event()

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1
    assert result[0]["source"] == "kalshi"
    assert result[0]["contract_id"] == "KXNBAGAME-25JAN01-LAL-BOS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kalshi_sports_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.kalshi_sports_source'`

- [ ] **Step 3: Write minimal implementation**

READ these files first:
- `backend/app/services/kalshi_event_source.py` — understand the existing Kalshi API client pattern
- `backend/app/services/polymarket_sports_source.py` — understand the output dict structure
- `backend/app/services/sport_market_detector.py` — understand `detect_sport_market` signature
- `backend/app/core/config.py` — confirm `KALSHI_API_URL` exists

```python
# backend/app/services/kalshi_sports_source.py
"""Kalshi sports market source — fetches sports markets from Kalshi API.

Parallel to polymarket_sports_source.py. Uses Kalshi's public read-only API
(no auth needed). Filters to single-leg binary events only.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import settings
from app.services.sport_market_detector import detect_sport_market

logger = logging.getLogger(__name__)

# Kalshi sports series ticker prefixes
_KALSHI_SPORTS_SERIES_PREFIXES = (
    "KXNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXSOCCEREPL", "KXSOCCERUCL", "KXSOCCERWCS",
    "KXNFL", "KXNBAGAME",
)


async def fetch_kalshi_sport_markets(limit: int = 100) -> list[dict]:
    """Fetch sports markets from Kalshi, filtered to single-leg binary events.

    Returns list of dicts compatible with SportMarketBridgeService.link_kalshi_market.
    Fail-closed: returns empty list on any error.
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
        candidates: list[dict] = []

        for event in events:
            markets = event.get("markets", [])
            # Filter to single-leg binary events only
            if len(markets) != 1:
                continue

            market = markets[0]
            ticker = market.get("ticker", "")
            series = event.get("series_ticker", "")

            # Filter to sports series
            if not series.upper().startswith(_KALSHI_SPORTS_SERIES_PREFIXES):
                continue

            # Parse price
            last_price = market.get("last_price_dollars", 0) or 0
            yes_bid = market.get("yes_bid_dollars", 0) or 0
            yes_ask = market.get("yes_ask_dollars", 0) or 0

            if last_price > 0:
                price = last_price
            elif yes_bid > 0 and yes_ask > 0:
                price = (yes_bid + yes_ask) / 2
            else:
                price = 0.5

            no_price = 1.0 - price

            # Use sport_market_detector to extract sport/competition/teams
            detected = detect_sport_market(
                question=market.get("title", "") or event.get("title", ""),
                source="kalshi",
            )

            if not detected.get("is_sport", False):
                continue

            candidates.append({
                "contract_id": ticker,
                "question": market.get("title", "") or event.get("title", ""),
                "price": price,
                "no_price": no_price,
                "liquidity": float(market.get("liquidity_dollars", 0) or 0),
                "volume": float(market.get("volume_fp", 0) or 0),
                "source": "kalshi",
                "detected_sport": detected.get("sport", ""),
                "detected_competition": detected.get("competition", ""),
                "detected_teams": detected.get("teams", []),
                "detected_date": detected.get("date"),
            })

            # Polite rate limit
            await asyncio.sleep(settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS)

        return candidates

    except Exception:
        logger.warning("Failed to fetch Kalshi sport markets", exc_info=True)
        return []
```

Add to `backend/app/core/config.py` before `settings = Settings()`:

```python
    # === Phase 11 — Kalshi Sports Market Integration ===
    PHASE11_KALSHI_SPORTS_ENABLED: bool = _env_bool("PHASE11_KALSHI_SPORTS_ENABLED", "false")
    KALSHI_SPORTS_FETCH_INTERVAL_SECONDS: int = int(os.getenv("KALSHI_SPORTS_FETCH_INTERVAL_SECONDS", "600"))
    KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS: float = float(os.getenv("KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS", "1.0"))
```

Add to `backend/.env.example` after the Phase 10 block:

```
# === Phase 11 — Kalshi Sports Market Integration ===
PHASE11_KALSHI_SPORTS_ENABLED=false
KALSHI_SPORTS_FETCH_INTERVAL_SECONDS=600
KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS=1.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kalshi_sports_source.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/kalshi_sports_source.py backend/app/core/config.py backend/.env.example backend/tests/test_kalshi_sports_source.py
git commit -m "feat(phase11): add Kalshi sports source + config + .env.example"
```

---

## Task 2: Bridge Service Extension — link_kalshi_market + _fetch_kalshi_price

**Files:**
- Modify: `backend/app/kernel/sport_market_bridge_service.py`
- Test: `backend/tests/test_kalshi_bridge_integration.py`

**Interfaces:**
- Consumes: `fetch_kalshi_sport_markets` output dicts, `SportMarketLinkStore`, existing `_rule_match` + `_llm_match`
- Produces: `link_kalshi_market(candidate: dict) -> dict` method, `_fetch_kalshi_price(contract_id: str) -> dict` method

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_kalshi_bridge_integration.py
"""Tests for Kalshi bridge integration — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.kernel.sport_market_bridge_service import SportMarketBridgeService


@pytest.fixture
def bridge():
    return SportMarketBridgeService()


@pytest.mark.asyncio
async def test_link_kalshi_market_stores_with_source_kalshi(bridge):
    """link_kalshi_market stores link with source='kalshi'."""
    candidate = {
        "contract_id": "KXNBAGAME-25JAN01-LAL-BOS",
        "question": "Lakers vs Celtics Jan 1",
        "price": 0.65,
        "no_price": 0.35,
        "liquidity": 5000,
        "volume": 12000,
        "source": "kalshi",
        "detected_sport": "basketball",
        "detected_competition": "nba",
        "detected_teams": ["Lakers", "Celtics"],
        "detected_date": "2025-01-01",
    }

    with patch.object(bridge, "_rule_match", return_value={"is_match": True, "confidence": 0.95, "match_id": "nba-2025-01-01-LAL-BOS", "mapped_outcome": "home_win"}):
        with patch.object(bridge, "_link_store") as mock_store:
            mock_store.upsert_link = MagicMock(return_value={"link_id": 1, "verified": True})

            result = await bridge.link_kalshi_market(candidate)

    assert result["source"] == "kalshi"
    mock_store.upsert_link.assert_called_once()
    call_kwargs = mock_store.upsert_link.call_args
    assert call_kwargs.kwargs.get("source") == "kalshi" or call_kwargs[1].get("source") == "kalshi"


@pytest.mark.asyncio
async def test_link_kalshi_market_auto_verifies_on_rule_match(bridge):
    """Rule match with confidence >= 0.9 auto-verifies."""
    candidate = {
        "contract_id": "KXNBAGAME-25JAN01-LAL-BOS",
        "question": "Lakers vs Celtics",
        "price": 0.65,
        "no_price": 0.35,
        "liquidity": 5000,
        "volume": 12000,
        "source": "kalshi",
        "detected_teams": ["Lakers", "Celtics"],
        "detected_date": "2025-01-01",
    }

    with patch.object(bridge, "_rule_match", return_value={"is_match": True, "confidence": 0.95, "match_id": "nba-LAL-BOS-2025-01-01", "mapped_outcome": "home_win"}):
        with patch.object(bridge, "_link_store") as mock_store:
            mock_store.upsert_link = MagicMock(return_value={"link_id": 1, "verified": True})
            result = await bridge.link_kalshi_market(candidate)

    assert result["verified"] is True


@pytest.mark.asyncio
async def test_link_kalshi_market_sends_to_pending_on_medium_confidence(bridge):
    """LLM match with confidence 0.6-0.85 goes to pending (verified=False)."""
    candidate = {
        "contract_id": "KXNBAGAME-25JAN01-LAL-BOS",
        "question": "Lakers vs Celtics",
        "price": 0.65,
        "no_price": 0.35,
        "liquidity": 5000,
        "volume": 12000,
        "source": "kalshi",
        "detected_teams": ["Lakers", "Celtics"],
        "detected_date": "2025-01-01",
    }

    with patch.object(bridge, "_rule_match", return_value={"is_match": False, "confidence": 0.3}):
        with patch.object(bridge, "_llm_match", return_value={"is_match": True, "confidence": 0.70, "match_id": "nba-LAL-BOS-2025-01-01", "mapped_outcome": "home_win"}):
            with patch.object(bridge, "_link_store") as mock_store:
                mock_store.upsert_link = MagicMock(return_value={"link_id": 2, "verified": False})
                result = await bridge.link_kalshi_market(candidate)

    assert result["verified"] is False


@pytest.mark.asyncio
async def test_fetch_kalshi_price_parses_response(bridge):
    """_fetch_kalshi_price correctly parses Kalshi market response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "markets": [{
            "ticker": "KXNBAGAME-25JAN01-LAL-BOS",
            "last_price_dollars": 0.68,
            "yes_bid_dollars": 0.66,
            "yes_ask_dollars": 0.70,
            "liquidity_dollars": 8000,
            "volume_fp": 15000,
        }]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await bridge._fetch_kalshi_price("KXNBAGAME-25JAN01-LAL-BOS")

    assert result["implied_prob"] == pytest.approx(0.68)
    assert result["price"] == pytest.approx(0.68)
    assert result["liquidity"] == 8000
    assert result["volume"] == 15000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kalshi_bridge_integration.py -v`
Expected: FAIL — `AttributeError: 'SportMarketBridgeService' object has no attribute 'link_kalshi_market'`

- [ ] **Step 3: Write minimal implementation**

READ `backend/app/kernel/sport_market_bridge_service.py` first to understand:
- The `link_polymarket_market` method signature and flow
- The `_rule_match` and `_llm_match` method signatures
- The `capture_snapshots` method and how it dispatches to `_fetch_latest_price`
- The `_link_store` attribute

Add `link_kalshi_market` method (parallel to `link_polymarket_market`):

```python
async def link_kalshi_market(self, candidate: dict) -> dict:
    """Link a Kalshi sports market to a match via the three-layer matching engine.

    Same flow as link_polymarket_market, but source='kalshi'.
    """
    # Run rule match first
    rule_result = self._rule_match(candidate)
    if rule_result.get("is_match") and rule_result.get("confidence", 0) >= 0.9:
        return self._store_kalshi_link(candidate, rule_result, verified=True)

    # Run LLM match
    llm_result = await self._llm_match(candidate)
    if llm_result.get("is_match") and llm_result.get("confidence", 0) >= 0.85:
        return self._store_kalshi_link(candidate, llm_result, verified=True)
    elif llm_result.get("is_match") and llm_result.get("confidence", 0) >= 0.6:
        return self._store_kalshi_link(candidate, llm_result, verified=False)

    return {"linked": False, "reason": "no match", "source": "kalshi"}


def _store_kalshi_link(self, candidate: dict, match_result: dict, verified: bool) -> dict:
    """Store a Kalshi market link."""
    match_id = match_result["match_id"]
    mapped_outcome = match_result.get("mapped_outcome", "yes")

    self._link_store.upsert_link(
        match_id=match_id,
        contract_id=candidate["contract_id"],
        source="kalshi",
        outcome_label="yes",
        mapped_outcome=mapped_outcome,
        link_method="rule" if verified else "llm",
        link_confidence=match_result.get("confidence", 0),
        verified=verified,
        market_question=candidate["question"],
        implied_prob=candidate["price"],
    )
    return {"linked": True, "verified": verified, "source": "kalshi", "match_id": match_id}
```

Add `_fetch_kalshi_price` method:

```python
async def _fetch_kalshi_price(self, contract_id: str) -> dict:
    """Fetch current price for a Kalshi market by ticker.

    Returns: {"implied_prob": float, "price": float, "liquidity": float, "volume": float}
    """
    import httpx
    from app.core.config import settings

    # Kalshi markets endpoint (strip /events from the URL and use /markets/{ticker})
    base_url = settings.KALSHI_API_URL.replace("/events", "/markets")
    url = f"{base_url}/{contract_id}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    markets = data.get("markets", [])
    if not markets:
        raise ValueError(f"No market found for ticker {contract_id}")

    market = markets[0]
    last_price = market.get("last_price_dollars", 0) or 0
    yes_bid = market.get("yes_bid_dollars", 0) or 0
    yes_ask = market.get("yes_ask_dollars", 0) or 0

    if last_price > 0:
        price = last_price
    elif yes_bid > 0 and yes_ask > 0:
        price = (yes_bid + yes_ask) / 2
    else:
        price = 0.5

    return {
        "implied_prob": price,
        "price": price,
        "liquidity": float(market.get("liquidity_dollars", 0) or 0),
        "volume": float(market.get("volume_fp", 0) or 0),
    }
```

Modify `capture_snapshots` to dispatch to `_fetch_kalshi_price` for `source="kalshi"` links. READ the existing `capture_snapshots` method first to find where it calls `_fetch_latest_price` and add an `if link.source == "kalshi":` branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kalshi_bridge_integration.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kernel/sport_market_bridge_service.py backend/tests/test_kalshi_bridge_integration.py
git commit -m "feat(phase11): add link_kalshi_market + _fetch_kalshi_price to bridge service"
```

---

## Task 3: Scheduler Extension — Kalshi Discovery

**Files:**
- Modify: `backend/app/core/scheduler.py`
- Test: `backend/tests/test_kalshi_scheduler.py`

**Interfaces:**
- Consumes: `fetch_kalshi_sport_markets` from Task 1, `SportMarketBridgeService.link_kalshi_market` from Task 2, `config.settings.PHASE11_KALSHI_SPORTS_ENABLED`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_kalshi_scheduler.py
"""Tests for Kalshi scheduler discovery — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.core.scheduler import _job_discover_sport_markets


@pytest.mark.asyncio
async def test_scheduler_discovers_kalshi_when_enabled(monkeypatch):
    """When PHASE11_KALSHI_SPORTS_ENABLED=true, scheduler fetches Kalshi markets."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE11_KALSHI_SPORTS_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED", False)

    mock_bridge = MagicMock()
    mock_bridge.link_kalshi_market = AsyncMock(return_value={"linked": True})

    with patch("app.core.scheduler.fetch_kalshi_sport_markets", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = [{"contract_id": "KXNBAGAME-TEST", "source": "kalshi"}]
        with patch("app.core.scheduler.SportMarketBridgeService", return_value=mock_bridge):
            try:
                await _job_discover_sport_markets()
            except Exception:
                pass  # Other parts of the job may fail; we just verify Kalshi was called

    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_skips_kalshi_when_disabled(monkeypatch):
    """When PHASE11_KALSHI_SPORTS_ENABLED=false, scheduler does NOT fetch Kalshi."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE11_KALSHI_SPORTS_ENABLED", False)
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED", False)

    with patch("app.core.scheduler.fetch_kalshi_sport_markets", new_callable=AsyncMock) as mock_fetch:
        try:
            await _job_discover_sport_markets()
        except Exception:
            pass

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_kalshi_failure_does_not_break_polymarket(monkeypatch):
    """Kalshi discovery failure doesn't break Polymarket discovery."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE11_KALSHI_SPORTS_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED", True)

    polymarket_called = False

    with patch("app.core.scheduler.fetch_kalshi_sport_markets", new_callable=AsyncMock) as mock_kalshi:
        mock_kalshi.side_effect = RuntimeError("Kalshi API down")
        with patch("app.core.scheduler.fetch_polymarket_sport_markets", new_callable=AsyncMock) as mock_poly:
            mock_poly.return_value = []  # Empty but successful
            try:
                await _job_discover_sport_markets()
            except Exception:
                pass
            polymarket_called = mock_poly.called

    assert polymarket_called  # Polymarket was still called despite Kalshi failure
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kalshi_scheduler.py -v`
Expected: FAIL — `fetch_kalshi_sport_markets` not imported in scheduler

- [ ] **Step 3: Write minimal implementation**

READ `backend/app/core/scheduler.py` to find the `_job_discover_sport_markets` function. Add Kalshi discovery block after the existing Polymarket discovery:

```python
# At the top of scheduler.py, add import (lazy or module-level):
from app.services.kalshi_sports_source import fetch_kalshi_sport_markets

# Inside _job_discover_sport_markets, after the Polymarket discovery block:
if settings.PHASE11_KALSHI_SPORTS_ENABLED:
    try:
        kalshi_candidates = await fetch_kalshi_sport_markets(limit=100)
        for candidate in kalshi_candidates:
            try:
                await bridge.link_kalshi_market(candidate)
            except Exception:
                logger.warning("Failed to link Kalshi market", exc_info=True)
    except Exception:
        logger.warning("Kalshi sports discovery failed", exc_info=True)
```

**IMPORTANT:** The exact placement and variable names depend on the existing code. READ the function carefully. The Kalshi block must be:
1. After the Polymarket discovery block (or at least independent of it)
2. Wrapped in `if settings.PHASE11_KALSHI_SPORTS_ENABLED:` + `try/except`
3. Using the same `bridge` instance as the Polymarket discovery

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kalshi_scheduler.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/scheduler.py backend/tests/test_kalshi_scheduler.py
git commit -m "feat(phase11): add Kalshi sports discovery in scheduler"
```

---

## Self-Review

### 1. Spec coverage
- ✅ Kalshi sports source (Task 1) — spec §5.1
- ✅ Bridge service extension (Task 2) — spec §5.2, §5.4
- ✅ Scheduler extension (Task 3) — spec §5.3
- ✅ Config changes (Task 1) — spec §7
- ✅ .env.example (Task 1) — spec §7
- ✅ No new DB tables — spec §6 (reuses kernel_sport_market_links with source="kalshi")
- ✅ Zero-invasion: EdgeDetector, Settlement, Recommendation unchanged — spec §10

### 2. Placeholder scan
- Task 2 Step 3 says "READ the existing capture_snapshots method first" — this is necessary because the exact dispatch point depends on the existing code. The implementer must read and adapt.
- Task 3 Step 3 says "READ the function carefully" — same reason.
- All test code is complete with actual assertions.

### 3. Type consistency
- `fetch_kalshi_sport_markets(limit: int = 100) -> list[dict]` — consistent
- `link_kalshi_market(candidate: dict) -> dict` — consistent
- `_fetch_kalshi_price(contract_id: str) -> dict` — consistent
- Output dict structure: `contract_id, question, price, no_price, liquidity, volume, source, detected_*` — consistent with Polymarket source output
