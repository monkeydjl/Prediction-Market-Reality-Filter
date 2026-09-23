"""The realtime socket's accounting must survive concurrency, failure and repeats.

Four defects, all of them in the accounting rather than the policy — the caps and
the credential check from the previous batch were real, and each of these makes one
of them escapable:

1. **The capacity check was not a reservation.** ``connect()`` measured the live
   counts, then ``await websocket.accept()``. That await yields the event loop, so
   two handshakes arriving together both measured "0 of 1 used" and both proceeded:
   a cap of 1 admitted 2. Measured, not reasoned — ``ReservationRaceTests`` below
   fails on the pre-fix manager with ``total_connections() == 2``.

2. **A broadcast dropped the socket from one of three books.** ``broadcast_to_match``
   discarded a failed socket from the match set and left ``_client_of`` and
   ``_per_client`` holding it. Since a send failure is the *normal* way a client
   goes away, every dead client permanently consumed a slot against
   ``WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT`` — the cap leaks to zero over an uptime
   and the endpoint dies quietly, which is the failure mode an operator cannot see.

3. **``match_id`` was never checked for existence.** Any string opened a socket and
   created a subscriber bucket, so an anonymous-but-authenticated caller could mint
   unbounded distinct match ids. The authoritative source is not a format rule: it
   is ``SportMarketLinkStore``, the same store both scheduler broadcast sites
   enumerate (``scheduler.py:930`` and ``:1153`` call
   ``get_matches_with_verified_links()``; ``:1156`` calls ``get_verified_links()``).
   A match with no verified link is never broadcast to, so a socket on it is a slot
   held open for traffic that cannot arrive.

4. **A cancelled broadcast leaked the socket.** ``broadcast_to_match`` caught
   ``Exception`` only; ``CancelledError`` is a ``BaseException``, so a broadcast
   task cancelled mid-``send_json`` skipped the manager's cleanup *and* both
   scheduler call sites' ``except Exception`` handlers (scheduler.py:1005 and
   :1183). The socket stayed in all three books with its per-client slot consumed
   forever, and a later handshake from the same caller was refused at a cap the
   departed socket still held. Measured, not reasoned — ``CancelledBroadcastTests``
   below fails on the pre-fix manager with ``total_connections() == 1``.

The tests here drive the manager directly with a real ``WebSocket`` object rather
than a ``MagicMock``, because the identity the per-client cap keys on is derived
from the ASGI scope: a MagicMock's ``.client.host`` is a fresh mock per instance, so
every socket would land in its own per-client bucket and the cap under test would
never be reached.
"""
from __future__ import annotations

import asyncio
import contextlib
import unittest
from typing import Any
from unittest.mock import patch

from starlette.websockets import WebSocket

from app.core.config import settings
from app.kernel.kernel_db import close_kernel_db, init_kernel_db
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.realtime import connection_manager as cm_module
from app.realtime.connection_manager import AtCapacity, ConnectionManager


class FakeSocket(WebSocket):
    """A real `WebSocket` over a hand-built scope, with the I/O replaced.

    Subclassed rather than mocked so `ws_auth.client_identity` — which is the HTTP
    rate limiter's `_client_host`, reading `.headers` and `.client` off the scope —
    resolves the same way it does in production. `host` is what the per-client cap
    buckets on, so two sockets sharing a host share a bucket.
    """

    def __init__(self, host: str = "10.0.0.1", *, accept_error: BaseException | None = None):
        super().__init__(
            {
                "type": "websocket",
                "headers": [],
                "client": (host, 5555),
                "subprotocols": [],
            },
            receive=self._no_receive,
            send=self._no_send,
        )
        self.accept_error = accept_error
        self.accepted = 0
        self.sent: list[dict[str, Any]] = []
        self.send_error: BaseException | None = None
        self.send_gate: asyncio.Event | None = None
        self.closed_with: list[int] = []

    async def _no_receive(self) -> dict[str, Any]:  # pragma: no cover - never awaited
        return {"type": "websocket.connect"}

    async def _no_send(self, message: dict[str, Any]) -> None:  # pragma: no cover
        return None

    async def accept(self, subprotocol: str | None = None, headers=None) -> None:
        # The yield point that made the pre-fix capacity check a race: control
        # returns to the loop here, so a sibling handshake runs its own check
        # against counts this socket has not been added to yet.
        await asyncio.sleep(0)
        if self.accept_error is not None:
            raise self.accept_error
        self.accepted += 1

    async def send_json(self, message: dict[str, Any], mode: str = "text") -> None:
        if self.send_error is not None:
            raise self.send_error
        if self.send_gate is not None:
            # Park inside the send, the way a slow TCP write does in production:
            # a task cancelled here is cancelled *mid-send*, which is the case the
            # `except Exception` in `broadcast_to_match` could not see.
            await self.send_gate.wait()
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_with.append(code)


@contextlib.contextmanager
def caps(total: int = 100, per_match: int = 100, per_client: int = 100):
    with patch.multiple(
        settings,
        WEBSOCKET_MAX_CONNECTIONS_TOTAL=total,
        WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=per_match,
        WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=per_client,
    ):
        yield


async def _gather_connects(manager, pairs) -> list[BaseException | None]:
    """Start every connect together, then let them run. Returns per-task outcome.

    `asyncio.gather` is what puts the handshakes in the same loop iteration, which
    is the condition the pre-fix check could not survive.
    """

    async def one(match_id: str, socket: FakeSocket) -> BaseException | None:
        try:
            await manager.connect(match_id, socket)
            return None
        except BaseException as exc:  # noqa: BLE001 - the outcome is the assertion
            return exc

    return list(await asyncio.gather(*(one(m, s) for m, s in pairs)))


class ReservationRaceTests(unittest.IsolatedAsyncioTestCase):
    """A cap must hold when the handshakes arrive together, not just in sequence.

    Every test here admits one more socket than the cap allows on the pre-fix
    manager, because the check and the increment were separated by an await.
    """

    async def test_the_total_cap_holds_against_two_concurrent_handshakes(self):
        manager = ConnectionManager()
        sockets = [FakeSocket("10.0.0.1"), FakeSocket("10.0.0.2")]
        with caps(total=1):
            outcomes = await _gather_connects(
                manager, [("m1", sockets[0]), ("m1", sockets[1])]
            )

        admitted = [item for item in outcomes if item is None]
        refused = [item for item in outcomes if isinstance(item, AtCapacity)]
        self.assertEqual(len(admitted), 1, f"cap 1 admitted {len(admitted)}")
        self.assertEqual(len(refused), 1, outcomes)
        self.assertEqual(manager.total_connections(), 1)
        # The refused socket must never have been accepted: an accepted-then-closed
        # socket has already spent the slot the cap exists to protect.
        self.assertEqual(sum(s.accepted for s in sockets), 1)

    async def test_the_per_match_cap_holds_against_concurrent_handshakes(self):
        manager = ConnectionManager()
        # Distinct hosts, so the per-client cap cannot be what refuses.
        sockets = [FakeSocket(f"10.0.0.{i}") for i in range(1, 5)]
        with caps(per_match=2):
            outcomes = await _gather_connects(
                manager, [("m1", socket) for socket in sockets]
            )

        self.assertEqual(len([o for o in outcomes if o is None]), 2, outcomes)
        self.assertEqual(manager.subscriber_count("m1"), 2)
        self.assertEqual(sum(s.accepted for s in sockets), 2)

    async def test_the_per_client_cap_holds_against_concurrent_handshakes(self):
        manager = ConnectionManager()
        # One host, four sockets, four different matches: only the per-client cap
        # can refuse here, which is the cap that stops one noisy caller.
        sockets = [FakeSocket("10.0.0.9") for _ in range(4)]
        with caps(per_client=1):
            outcomes = await _gather_connects(
                manager, [(f"m{i}", s) for i, s in enumerate(sockets)]
            )

        self.assertEqual(len([o for o in outcomes if o is None]), 1, outcomes)
        self.assertEqual(manager.total_connections(), 1)
        self.assertEqual(sum(s.accepted for s in sockets), 1)
        self.assertEqual(list(manager.client_connection_count().values()), [1])

    async def test_a_failed_accept_leaves_no_reservation_behind(self):
        """A reservation is a claim on a slot, so an abandoned one is a leak.

        This is the risk the fix itself introduces: pre-fix there was nothing to
        leak, because nothing was claimed before `accept()`. Removing the release
        in the failure path turns this red.
        """
        manager = ConnectionManager()
        doomed = FakeSocket("10.0.0.1", accept_error=RuntimeError("handshake died"))
        with caps(total=1, per_match=1, per_client=1):
            with self.assertRaises(RuntimeError):
                await manager.connect("m1", doomed)

            self.assertEqual(manager.total_connections(), 0)
            self.assertEqual(manager.subscriber_count("m1"), 0)
            self.assertEqual(manager.client_connection_count(), {})

            # The slot is genuinely reusable, which is the property an operator
            # cares about: a leak here would take the endpoint down over uptime.
            healthy = FakeSocket("10.0.0.1")
            await manager.connect("m1", healthy)
            self.assertEqual(manager.total_connections(), 1)

    async def test_a_cancelled_accept_releases_the_reservation_and_propagates(self):
        """A timeout or a shutdown cancels the task mid-handshake.

        `CancelledError` is a `BaseException`, so a bare `except Exception` around
        `accept()` would leak the reservation; catching and swallowing it would be
        worse, turning a cancellation into a live-looking connection.
        """
        manager = ConnectionManager()
        cancelled = FakeSocket("10.0.0.1", accept_error=asyncio.CancelledError())
        with caps(total=1):
            with self.assertRaises(asyncio.CancelledError):
                await manager.connect("m1", cancelled)

        self.assertEqual(manager.total_connections(), 0)
        self.assertEqual(manager.client_connection_count(), {})

    async def test_a_disconnect_returns_the_slot_to_the_pool(self):
        manager = ConnectionManager()
        with caps(total=1, per_match=1, per_client=1):
            first = FakeSocket("10.0.0.1")
            await manager.connect("m1", first)
            manager.disconnect("m1", first)

            self.assertEqual(manager.total_connections(), 0)
            self.assertEqual(manager.client_connection_count(), {})

            second = FakeSocket("10.0.0.1")
            await manager.connect("m1", second)
            self.assertEqual(manager.subscriber_count("m1"), 1)
            self.assertEqual(second.accepted, 1)


class BroadcastFailureCleanupTests(unittest.IsolatedAsyncioTestCase):
    """A send failure is how a client normally leaves, so it must clean all books.

    `broadcast_to_match` discarded the socket from the match set only. The
    per-client count kept it forever, so `WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT`
    counted down once per dead client and never back up.
    """

    async def test_a_failed_send_clears_every_book_not_just_the_match_set(self):
        manager = ConnectionManager()
        with caps():
            socket = FakeSocket("10.0.0.1")
            await manager.connect("m1", socket)
            socket.send_error = RuntimeError("connection closed")

            await manager.broadcast_to_match("m1", {"type": "odds_snapshot"})

        self.assertEqual(manager.subscriber_count("m1"), 0)
        self.assertEqual(manager.total_connections(), 0)
        self.assertEqual(manager.client_connection_count(), {})
        # The empty bucket is dropped, not left behind: an unbounded dict of empty
        # sets is the same memory-growth shape the caps close.
        self.assertEqual(manager.match_ids(), [])

    async def test_the_dead_clients_slot_is_immediately_reusable(self):
        """The operator-visible consequence of the leak, stated as behaviour.

        With a per-client cap of 1, a client whose send failed could never
        reconnect for the life of the process.
        """
        manager = ConnectionManager()
        with caps(total=1, per_match=1, per_client=1):
            dead = FakeSocket("10.0.0.1")
            await manager.connect("m1", dead)
            dead.send_error = RuntimeError("connection closed")
            await manager.broadcast_to_match("m1", {"type": "heartbeat"})

            replacement = FakeSocket("10.0.0.1")
            await manager.connect("m1", replacement)

        self.assertEqual(manager.subscriber_count("m1"), 1)
        self.assertEqual(replacement.accepted, 1)

    async def test_a_failed_send_does_not_disturb_the_healthy_subscriber(self):
        manager = ConnectionManager()
        with caps():
            dead = FakeSocket("10.0.0.1")
            alive = FakeSocket("10.0.0.2")
            await manager.connect("m1", dead)
            await manager.connect("m1", alive)
            dead.send_error = RuntimeError("connection closed")

            await manager.broadcast_to_match("m1", {"type": "market_snapshot"})

        self.assertEqual(manager.subscriber_count("m1"), 1)
        self.assertEqual(manager.total_connections(), 1)
        self.assertEqual(manager.client_connection_count(), {"10.0.0.2": 1})
        self.assertEqual(len(alive.sent), 1)

    async def test_the_route_cleanup_after_a_failed_broadcast_is_harmless(self):
        """The real sequence: the broadcast reaps the socket, then the route's
        `finally` disconnects the same socket. The second call must be a no-op,
        not a decrement of somebody else's count."""
        manager = ConnectionManager()
        with caps():
            dead = FakeSocket("10.0.0.1")
            alive = FakeSocket("10.0.0.1")  # same host, so the same bucket
            await manager.connect("m1", dead)
            await manager.connect("m1", alive)
            dead.send_error = RuntimeError("connection closed")

            await manager.broadcast_to_match("m1", {"type": "heartbeat"})
            manager.disconnect("m1", dead)  # what the route's finally does

        self.assertEqual(manager.client_connection_count(), {"10.0.0.1": 1})
        self.assertEqual(manager.total_connections(), 1)
        self.assertEqual(manager.subscriber_count("m1"), 1)


class CancelledBroadcastTests(unittest.IsolatedAsyncioTestCase):
    """A broadcast task cancelled mid-``send_json`` must not strand the socket.

    This is the fourth defect. ``broadcast_to_match`` caught ``Exception``, and
    ``asyncio.CancelledError`` is a ``BaseException``, so cancellation skipped the
    reaping loop *and* the ``except Exception`` both scheduler call sites wrap the
    broadcast in (scheduler.py:1005, :1183). The socket kept its row in
    ``_client_of``, its slot in ``_per_client`` and the match bucket, and a later
    handshake from the same caller was refused at a cap a departed socket still
    held. ``connect`` was already fixed for this (its failure paths catch
    ``BaseException``); the broadcast path had the same hole.

    The cancellation here is real — a task is actually cancelled while parked
    inside ``send_json`` — not a stubbed ``CancelledError`` raised by a mock, so
    what is under test is the manager's response to a genuine mid-await
    cancellation, including the re-raise.
    """

    async def test_a_cancelled_broadcast_cleans_up_and_reraises(self):
        manager = ConnectionManager()
        socket = FakeSocket("10.0.0.1")
        socket.send_gate = asyncio.Event()
        with caps(total=1, per_match=1, per_client=1):
            await manager.connect("m1", socket)

            broadcast = asyncio.create_task(
                manager.broadcast_to_match("m1", {"type": "odds_snapshot"})
            )
            await asyncio.sleep(0)  # let the broadcast park inside send_json
            broadcast.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await broadcast

        self.assertEqual(manager.total_connections(), 0)
        self.assertEqual(manager.client_connection_count(), {})
        self.assertEqual(manager.match_ids(), [])
        self.assertEqual(manager.subscriber_count("m1"), 0)

    async def test_the_cancelled_sockets_slot_is_immediately_reusable(self):
        """The operator-visible consequence: under a per-client cap of 1, a caller
        whose broadcast was cancelled could never reconnect."""
        manager = ConnectionManager()
        dead = FakeSocket("10.0.0.1")
        dead.send_gate = asyncio.Event()
        with caps(total=1, per_match=1, per_client=1):
            await manager.connect("m1", dead)
            broadcast = asyncio.create_task(
                manager.broadcast_to_match("m1", {"type": "heartbeat"})
            )
            await asyncio.sleep(0)
            broadcast.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await broadcast

            replacement = FakeSocket("10.0.0.1")
            await manager.connect("m1", replacement)

        self.assertEqual(manager.subscriber_count("m1"), 1)
        self.assertEqual(replacement.accepted, 1)

    async def test_the_route_cleanup_after_a_cancelled_broadcast_is_harmless(self):
        """The real sequence: the broadcast is cancelled and cleans up, then the
        route's ``finally`` disconnects the same socket. The second call must be a
        no-op — not a decrement of a sibling that still holds the bucket."""
        manager = ConnectionManager()
        dead = FakeSocket("10.0.0.1")
        dead.send_gate = asyncio.Event()
        alive = FakeSocket("10.0.0.1")  # same host, so the same per-client bucket
        with caps(per_client=2):
            await manager.connect("m1", dead)
            await manager.connect("m1", alive)
            broadcast = asyncio.create_task(
                manager.broadcast_to_match("m1", {"type": "heartbeat"})
            )
            await asyncio.sleep(0)
            broadcast.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await broadcast

            manager.disconnect("m1", dead)  # what the route's finally does

        self.assertEqual(manager.subscriber_count("m1"), 1)
        self.assertEqual(manager.total_connections(), 1)
        self.assertEqual(manager.client_connection_count(), {"10.0.0.1": 1})

    async def test_a_cancelled_broadcast_does_not_disturb_the_healthy_subscriber(self):
        """Only the cancelled send's own socket is reaped, never a sibling."""
        manager = ConnectionManager()
        stalled = FakeSocket("10.0.0.1")
        stalled.send_gate = asyncio.Event()
        healthy = FakeSocket("10.0.0.2")
        with caps():
            await manager.connect("m1", stalled)
            await manager.connect("m1", healthy)
            broadcast = asyncio.create_task(
                manager.broadcast_to_match("m1", {"type": "market_snapshot"})
            )
            await asyncio.sleep(0)
            broadcast.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await broadcast

        # The hosts make the survivor's identity observable without reaching into
        # the manager's books: the stalled socket (10.0.0.1) is gone, the healthy
        # one (10.0.0.2) keeps its slot.
        self.assertEqual(manager.subscriber_count("m1"), 1)
        self.assertEqual(manager.client_connection_count(), {"10.0.0.2": 1})

        # The survivor still receives a later broadcast. The gate is cleared so
        # this cannot park even if the reap had failed.
        stalled.send_gate = None
        await manager.broadcast_to_match("m1", {"type": "odds_snapshot"})
        self.assertGreaterEqual(len(healthy.sent), 1)


class DisconnectIsIdempotentTests(unittest.IsolatedAsyncioTestCase):
    """Cleanup runs from several places, so it must survive running twice.

    The route's `finally`, a failed broadcast, and a cap refusal can all target the
    same socket. A decrement that fires twice would hand out a slot that is still
    in use; a negative count would make the cap unreachable.
    """

    async def test_disconnecting_twice_does_not_go_negative(self):
        manager = ConnectionManager()
        with caps():
            socket = FakeSocket("10.0.0.1")
            await manager.connect("m1", socket)
            manager.disconnect("m1", socket)
            manager.disconnect("m1", socket)

        self.assertEqual(manager.total_connections(), 0)
        self.assertEqual(manager.client_connection_count(), {})
        self.assertEqual(manager.match_ids(), [])

    async def test_disconnecting_twice_does_not_evict_a_sibling(self):
        manager = ConnectionManager()
        with caps():
            first = FakeSocket("10.0.0.1")
            second = FakeSocket("10.0.0.1")
            await manager.connect("m1", first)
            await manager.connect("m1", second)

            manager.disconnect("m1", first)
            manager.disconnect("m1", first)

        self.assertEqual(manager.subscriber_count("m1"), 1)
        self.assertEqual(manager.total_connections(), 1)
        self.assertEqual(manager.client_connection_count(), {"10.0.0.1": 1})

    async def test_disconnecting_a_socket_that_never_connected_is_a_no_op(self):
        """A cap refusal raises before registration, and the route's `finally`
        still runs. That path must not decrement a live caller's count."""
        manager = ConnectionManager()
        with caps(total=1):
            live = FakeSocket("10.0.0.1")
            await manager.connect("m1", live)

            never = FakeSocket("10.0.0.1")
            with self.assertRaises(AtCapacity):
                await manager.connect("m1", never)
            manager.disconnect("m1", never)  # the route's finally

        self.assertEqual(manager.total_connections(), 1)
        self.assertEqual(manager.client_connection_count(), {"10.0.0.1": 1})
        self.assertEqual(manager.subscriber_count("m1"), 1)


class EveryEndingReachesTheSameCleanupTests(unittest.TestCase):
    """Route `finally`, failed broadcast, cancellation and client hangup converge.

    Four endings, one `disconnect`. These go through the mounted route rather than
    the manager, because "the route's `finally` ran" is the part a manager-level test
    cannot observe.
    """

    AUTH = {"X-API-Key": "lifecycle-write-key-4b2f"}

    @contextlib.contextmanager
    def _client(self, **overrides):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.router import api_router
        from app.api.routes import realtime as realtime_module
        from app.realtime import ws_auth

        values = {
            "PHASE10_REALTIME_PUSH_ENABLED": True,
            "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS": 999,
            "API_WRITE_KEY": self.AUTH["X-API-Key"],
            "ALLOW_OPEN_WRITES": False,
            "WEBSOCKET_MAX_CONNECTIONS_TOTAL": 200,
            "WEBSOCKET_MAX_CONNECTIONS_PER_MATCH": 50,
            "WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT": 5,
            **overrides,
        }
        app = FastAPI()
        app.include_router(api_router, prefix="/api")
        with contextlib.ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(patch.object(settings, name, value))
            stack.enter_context(patch.object(cm_module, "_connection_manager", None))
            stack.enter_context(patch.object(ws_auth, "_ticket_store", None))
            stack.enter_context(
                patch.object(
                    realtime_module, "match_is_broadcastable", lambda match_id: True
                )
            )
            with TestClient(app) as client:
                yield client

    def _assert_fully_released(self, manager) -> None:
        self.assertEqual(manager.total_connections(), 0)
        self.assertEqual(manager.client_connection_count(), {})
        self.assertEqual(manager.match_ids(), [])

    def test_a_client_hangup_releases_every_book(self):
        with self._client() as client:
            with client.websocket_connect(
                "/api/ws/matches/m1/prices", headers=self.AUTH
            ):
                manager = cm_module.get_connection_manager()
                self.assertEqual(manager.total_connections(), 1)
            self._assert_fully_released(manager)

    def test_a_cap_refusal_leaves_nothing_behind(self):
        """The refusal path runs the route's `finally` against a socket that was
        never registered. It must release nothing, not somebody else's slot."""
        from starlette.websockets import WebSocketDisconnect

        with self._client(WEBSOCKET_MAX_CONNECTIONS_TOTAL=1) as client:
            with client.websocket_connect(
                "/api/ws/matches/m1/prices", headers=self.AUTH
            ):
                manager = cm_module.get_connection_manager()
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect(
                        "/api/ws/matches/m2/prices", headers=self.AUTH
                    ):
                        pass
                # The live socket is untouched by the refusal.
                self.assertEqual(manager.total_connections(), 1)
                self.assertEqual(manager.match_ids(), ["m1"])
            self._assert_fully_released(manager)

    def test_a_broadcast_send_failure_releases_the_slot_through_the_route(self):
        """The leak, end to end: the scheduler's push fails, and the caller must
        still be able to reconnect under a per-client cap of 1."""
        with self._client(WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=1) as client:
            with client.websocket_connect(
                "/api/ws/matches/m1/prices", headers=self.AUTH
            ) as socket:
                manager = cm_module.get_connection_manager()
                # The server-side socket object, which no public accessor exposes --
                # `match_ids()` gives the keys, and what this needs is the instance
                # whose `send_json` must fail. Adding a public getter for one test
                # would be the worse trade.
                live = next(iter(manager._connections["m1"]))
                with patch.object(
                    live, "send_json", side_effect=RuntimeError("peer gone")
                ):
                    socket.portal.call(
                        manager.broadcast_to_match, "m1", {"type": "odds_snapshot"}
                    )
                self._assert_fully_released(manager)

            # The cap of 1 is available again, which is the operator-visible proof.
            with client.websocket_connect(
                "/api/ws/matches/m1/prices", headers=self.AUTH
            ):
                self.assertEqual(manager.total_connections(), 1)


class MatchExistenceIsCheckedAgainstTheAuthoritativeStoreTests(unittest.TestCase):
    """Only a match the scheduler can actually broadcast to may open a socket.

    Not a format rule. Both broadcast sites enumerate
    `SportMarketLinkStore.get_matches_with_verified_links()`, and the per-match push
    reads `get_verified_links(match_id=...)`, so "has at least one verified link" is
    the same predicate on both sides. A match with only unverified links is in the
    table and still never receives a broadcast.

    These run against the real kernel DB (conftest redirects `KERNEL_DB_FILE` to the
    test data dir and closes the handle around every test), not a mock: a mock here
    would prove the route calls *something*, which is the seam this repo has been
    burned by before.
    """

    def setUp(self) -> None:
        close_kernel_db()
        init_kernel_db()
        self.store = SportMarketLinkStore()

    def tearDown(self) -> None:
        # A leaked kernel session surfaces later as a Windows PermissionError at
        # rmtree, which reads like a test artefact and is not one.
        close_kernel_db()

    def _seed(self, match_id: str, *, verified: bool) -> None:
        self.store.upsert_link(
            match_id=match_id,
            contract_id=f"c-{match_id}",
            source="polymarket",
            outcome_label="YES",
            mapped_outcome="home_win",
            link_method="rule",
            link_confidence=0.95,
            verified=verified,
            market_question="q",
            implied_prob=0.6,
        )

    def test_a_match_with_a_verified_link_is_broadcastable(self):
        """The positive arm. Without it, a check that refused everything would
        satisfy every refusal test in this class."""
        from app.api.routes import realtime

        self._seed("live-match", verified=True)
        self.assertTrue(realtime.match_is_broadcastable("live-match"))

    def test_an_unknown_match_is_not_broadcastable(self):
        from app.api.routes import realtime

        self._seed("live-match", verified=True)
        self.assertFalse(realtime.match_is_broadcastable("no-such-match"))

    def test_a_match_with_only_unverified_links_is_not_broadcastable(self):
        """Existence in the table is not the predicate: the scheduler pushes for
        verified links only, so this socket would wait forever."""
        from app.api.routes import realtime

        self._seed("pending-match", verified=False)
        self.assertFalse(realtime.match_is_broadcastable("pending-match"))

    def test_a_broken_lookup_is_not_reported_as_unknown(self):
        """Fail closed, and fail *distinguishably*.

        `get_verified_links` raises on a broken table rather than answering `[]`
        (that swallow was removed deliberately). Collapsing the raise into "unknown
        match" would be the same defect one layer up, so the lookup failure has its
        own outcome.
        """
        from sqlalchemy import text

        from app.api.routes import realtime
        from app.kernel.kernel_db import get_kernel_session

        self._seed("live-match", verified=True)
        session = get_kernel_session()
        try:
            session.execute(text("DROP TABLE kernel_sport_market_links"))
            session.commit()
        finally:
            session.close()

        with self.assertRaises(realtime.MatchLookupUnavailable):
            realtime.match_is_broadcastable("live-match")
