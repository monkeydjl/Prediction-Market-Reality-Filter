"""Tests for ConnectionManager — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.realtime.connection_manager import ConnectionManager, get_connection_manager


@pytest.fixture
def manager():
    return ConnectionManager()


@pytest.fixture
def mock_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_connect_adds_websocket_to_match_set(manager, mock_ws):
    await manager.connect("match-1", mock_ws)
    assert mock_ws.accept.called
    assert manager.subscriber_count("match-1") == 1


@pytest.mark.asyncio
async def test_disconnect_removes_websocket(manager, mock_ws):
    await manager.connect("match-1", mock_ws)
    manager.disconnect("match-1", mock_ws)
    assert manager.subscriber_count("match-1") == 0


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_subscribers(manager):
    ws1 = MagicMock()
    ws1.accept = AsyncMock()
    ws1.send_json = AsyncMock()
    ws2 = MagicMock()
    ws2.accept = AsyncMock()
    ws2.send_json = AsyncMock()
    await manager.connect("match-1", ws1)
    await manager.connect("match-1", ws2)

    await manager.broadcast_to_match("match-1", {"type": "test"})

    ws1.send_json.assert_called_once_with({"type": "test"})
    ws2.send_json.assert_called_once_with({"type": "test"})


@pytest.mark.asyncio
async def test_broadcast_drops_dead_connections(manager):
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock(side_effect=RuntimeError("connection closed"))
    await manager.connect("match-1", ws)

    await manager.broadcast_to_match("match-1", {"type": "test"})

    assert manager.subscriber_count("match-1") == 0


@pytest.mark.asyncio
async def test_get_connection_manager_returns_singleton():
    m1 = get_connection_manager()
    m2 = get_connection_manager()
    assert m1 is m2
