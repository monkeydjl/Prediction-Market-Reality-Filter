"""Cross-process ticket-store contracts for realtime WebSocket handshakes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from unittest.mock import patch

from app.core.config import settings
from app.realtime import ws_auth


def _redeem_in_worker(path: str, ticket: str) -> bool:
    """Redeem from a spawned interpreter, as a separate API worker would."""
    from app.core.config import settings as worker_settings
    from app.realtime.ws_auth import TicketStore

    worker_settings.LOOP_DB_FILE = path
    return TicketStore().redeem(ticket)


def test_shared_store_does_not_persist_the_bearer_ticket(tmp_path):
    """A shared durable store must not turn the short-lived bearer into data at rest."""
    path = tmp_path / "realtime-tickets.db"
    with patch.object(settings, "LOOP_DB_FILE", str(path)):
        ticket, _ = ws_auth.TicketStore().issue()
        with ws_auth.sqlite_db.reading(str(path)) as conn:
            stored_values = tuple(
                conn.execute(
                    "SELECT ticket FROM realtime_ws_tickets"
                ).fetchone()
            )

    assert ticket not in stored_values


def test_ticket_issued_by_one_store_is_redeemable_by_another(tmp_path):
    """Workers sharing the durable store must share ticket state."""
    path = tmp_path / "realtime-tickets.db"
    with patch.object(settings, "LOOP_DB_FILE", str(path)):
        issuer = ws_auth.TicketStore()
        redeemer = ws_auth.TicketStore()
        ticket, ttl = issuer.issue()

        assert ttl > 0
        assert redeemer.redeem(ticket) is True
        assert issuer.redeem(ticket) is False


def test_two_workers_can_consume_a_ticket_only_once(tmp_path):
    """The consume operation must be atomic across worker-local stores."""
    path = tmp_path / "realtime-tickets.db"
    with patch.object(settings, "LOOP_DB_FILE", str(path)):
        issuer = ws_auth.TicketStore()
        ticket, _ = issuer.issue()
        worker_a = ws_auth.TicketStore()
        worker_b = ws_auth.TicketStore()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda store: store.redeem(ticket),
                    (worker_a, worker_b),
                )
            )

        assert results.count(True) == 1
        assert results.count(False) == 1

def test_spawned_workers_share_atomic_consume_once(tmp_path):
    """The cross-process boundary is covered, not just two objects in one process."""
    path = tmp_path / "realtime-tickets.db"
    with patch.object(settings, "LOOP_DB_FILE", str(path)):
        ticket, _ = ws_auth.TicketStore().issue()
        context = get_context("spawn")
        with context.Pool(2) as pool:
            results = pool.starmap(
                _redeem_in_worker,
                [(str(path), ticket), (str(path), ticket)],
            )

        assert results.count(True) == 1
        assert results.count(False) == 1
