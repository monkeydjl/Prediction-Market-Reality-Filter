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
