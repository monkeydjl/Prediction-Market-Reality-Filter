"""Tests for scheduler broadcast integration — TDD RED phase.

Verifies that the scheduler jobs call ConnectionManager.broadcast_to_match
when PHASE10_REALTIME_PUSH_ENABLED is true, and skip the broadcast when false.
The scheduler job functions have complex deferred-import dependencies, so the
DB/store layers are mocked at their source paths and the run-ledger helpers
are stubbed out. The connection manager is injected via patching
``app.core.scheduler.get_connection_manager`` (create=True so the patch works
even before the import is added in the RED phase).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.core.scheduler import (
    _job_capture_market_snapshots,
    _job_fetch_traditional_odds,
)


@pytest.fixture
def mock_manager():
    """A mock ConnectionManager whose broadcast_to_match is an AsyncMock."""
    manager = MagicMock()
    manager.broadcast_to_match = AsyncMock()
    return manager


@pytest.mark.asyncio
async def test_market_snapshot_broadcasts_when_enabled(mock_manager, monkeypatch):
    """When PHASE10 is enabled, _job_capture_market_snapshots broadcasts market_snapshot."""
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)

    # Stub link_store: one match with one verified link
    mock_link_store = MagicMock()
    mock_link_store.get_matches_with_verified_links.return_value = ["match-1"]
    mock_link_store.get_verified_links.return_value = [
        {"id": 42, "contract_id": "c-1"}
    ]

    # Stub bridge: fetch_current_price returns a price dict
    mock_bridge = MagicMock()
    mock_bridge.fetch_current_price = AsyncMock(
        return_value={
            "implied_prob": 0.65,
            "price": 0.67,
            "liquidity": 100,
            "volume": 200,
        }
    )

    mock_snap_store = MagicMock()

    with patch(
        "app.core.scheduler.get_connection_manager",
        return_value=mock_manager,
        create=True,
    ), patch(
        "app.core.scheduler._start_run", return_value=None
    ), patch(
        "app.core.scheduler._finish_run"
    ), patch(
        "app.kernel.kernel_db.init_kernel_db"
    ), patch(
        "app.kernel.sport_market_bridge_service.SportMarketBridgeService",
        return_value=mock_bridge,
    ), patch(
        "app.kernel.sport_market_link_store.SportMarketLinkStore",
        return_value=mock_link_store,
    ), patch(
        "app.kernel.market_snapshot_store.MarketSnapshotStore",
        return_value=mock_snap_store,
    ):
        await _job_capture_market_snapshots()

    mock_manager.broadcast_to_match.assert_called_once()
    args, _ = mock_manager.broadcast_to_match.call_args
    # broadcast_to_match(match_id, message) — positional args
    assert args[0] == "match-1"
    message = args[1]
    assert message["type"] == "market_snapshot"
    assert message["match_id"] == "match-1"
    assert message["link_id"] == 42
    assert message["implied_prob"] == 0.65
    assert message["price"] == 0.67
    assert "captured_at" in message


@pytest.mark.asyncio
async def test_odds_snapshot_broadcasts_when_enabled(mock_manager, monkeypatch):
    """When PHASE10 is enabled, _job_fetch_traditional_odds broadcasts odds_snapshot."""
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "ODDS_API_ENABLED", True)

    mock_link_store = MagicMock()
    mock_link_store.get_matches_with_verified_links.return_value = [
        "nba-2024-01-01-lakers-celtics"
    ]

    mock_odds_store = MagicMock()

    # Stub the odds list returned by _match_odds_to_match to avoid needing
    # to mock the entire odds API service internals.
    odds_list = [("home_win", 0.65, 1.54, "average", 5)]

    with patch(
        "app.core.scheduler.get_connection_manager",
        return_value=mock_manager,
        create=True,
    ), patch(
        "app.core.scheduler._start_run", return_value=None
    ), patch(
        "app.core.scheduler._finish_run"
    ), patch(
        "app.kernel.kernel_db.init_kernel_db"
    ), patch(
        "app.kernel.traditional_odds_store.TraditionalOddsStore",
        return_value=mock_odds_store,
    ), patch(
        "app.kernel.sport_market_link_store.SportMarketLinkStore",
        return_value=mock_link_store,
    ), patch(
        "app.services.odds_api_service.fetch_all_sports_odds",
        return_value={},
    ), patch(
        "app.core.scheduler._match_odds_to_match", return_value=odds_list
    ):
        await _job_fetch_traditional_odds()

    mock_manager.broadcast_to_match.assert_called_once()
    args, _ = mock_manager.broadcast_to_match.call_args
    assert args[0] == "nba-2024-01-01-lakers-celtics"
    message = args[1]
    assert message["type"] == "odds_snapshot"
    assert message["match_id"] == "nba-2024-01-01-lakers-celtics"
    assert message["outcome"] == "home_win"
    assert message["implied_prob"] == 0.65
    assert message["decimal_odds"] == 1.54
    assert message["bookmaker"] == "average"
    assert "captured_at" in message


@pytest.mark.asyncio
async def test_no_broadcast_when_disabled(mock_manager, monkeypatch):
    """When PHASE10 is disabled, scheduler does NOT broadcast."""
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", False)
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)

    mock_link_store = MagicMock()
    mock_link_store.get_matches_with_verified_links.return_value = ["match-1"]
    mock_link_store.get_verified_links.return_value = [
        {"id": 42, "contract_id": "c-1"}
    ]

    mock_bridge = MagicMock()
    mock_bridge.fetch_current_price = AsyncMock(
        return_value={
            "implied_prob": 0.65,
            "price": 0.67,
            "liquidity": 100,
            "volume": 200,
        }
    )

    mock_snap_store = MagicMock()

    with patch(
        "app.core.scheduler.get_connection_manager",
        return_value=mock_manager,
        create=True,
    ), patch(
        "app.core.scheduler._start_run", return_value=None
    ), patch(
        "app.core.scheduler._finish_run"
    ), patch(
        "app.kernel.kernel_db.init_kernel_db"
    ), patch(
        "app.kernel.sport_market_bridge_service.SportMarketBridgeService",
        return_value=mock_bridge,
    ), patch(
        "app.kernel.sport_market_link_store.SportMarketLinkStore",
        return_value=mock_link_store,
    ), patch(
        "app.kernel.market_snapshot_store.MarketSnapshotStore",
        return_value=mock_snap_store,
    ):
        await _job_capture_market_snapshots()

    mock_manager.broadcast_to_match.assert_not_called()
