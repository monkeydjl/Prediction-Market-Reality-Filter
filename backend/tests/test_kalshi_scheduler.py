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
