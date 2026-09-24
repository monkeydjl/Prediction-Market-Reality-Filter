"""The realtime WebSocket must authenticate its handshake and bound its fan-out.

Measured before this file existed, against the router as `app.main` mounts it:
`/api/ws/matches/m1/prices` **accepted a socket presenting no credential at all**,
with `API_WRITE_KEY` configured. `price_stream` gated only on
`PHASE10_REALTIME_PUSH_ENABLED` and then called `ConnectionManager.connect`, which
calls `accept()` unconditionally; `_connections` is an unbounded `dict[str, set]`;
and `InMemoryRateLimitMiddleware` is a `BaseHTTPMiddleware`, so no WebSocket scope
ever reaches it. What that publishes is not a status page: the scheduler pushes
live bookmaker odds, implied probabilities and Kalshi prices into it
(`scheduler.py:996`, `scheduler.py:1175`).

Two credential transports, because a browser has exactly one lever. `new
WebSocket(url, protocols)` can set `Sec-WebSocket-Protocol` and nothing else -- no
custom headers -- so a browser presents a short-lived single-use **ticket** bought
from `POST /api/ws/tickets` with the key in a header. The write key itself never
travels in a URL, a subprotocol, or a log line. Programmatic clients (ops scripts,
the drill suite) send `X-API-Key` directly.

The enforcement boundary is the one the rest of this codebase already uses, not a
new toggle and not a test-only switch: **a configured `API_WRITE_KEY` is what makes
the socket refuse anonymous callers.** Production is covered because the preflight
independently requires that key to be non-empty, so the two rules compose; a dev
box with no key keeps an open socket, exactly as `_resolve_encryption_key` keeps
plaintext backups legal off production.
"""
import contextlib
import logging
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.api.router import api_router  # noqa: E402
from app.core import preflight  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.realtime import connection_manager as cm_module  # noqa: E402
from app.realtime import ws_auth  # noqa: E402

WRITE_KEY = "ws-write-key-DO-NOT-LOG-7c1d"
PRICES = "/api/ws/matches/m1/prices"
TICKETS = "/api/ws/tickets"


@contextlib.contextmanager
def wired(*, real_match_lookup: bool = False, **overrides):
    """A client over the router as `app.main` mounts it, with fresh singletons.

    Mounted through `api_router` with the `/api` prefix rather than the bare
    `realtime.router`, because the prefix is part of the path under test: the
    sibling suite's bare `FastAPI()` would happily pass against a path production
    does not serve.

    `real_match_lookup=False` stubs `match_is_broadcastable` to True so the tests
    below stay about credentials and caps. It is a stub of a *collaborator*, not of
    the behaviour under test: the predicate itself is measured against the real
    `SportMarketLinkStore` in `tests/test_realtime_connection_lifecycle.py`, and the
    route's use of it is measured with `real_match_lookup=True` in
    `UnknownMatchIsRefusedTests`. Deleting the call from the route reddens those.
    """
    values = {
        "PHASE10_REALTIME_PUSH_ENABLED": True,
        "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS": 999,
        "API_WRITE_KEY": WRITE_KEY,
        "ALLOW_OPEN_WRITES": False,
        "WEBSOCKET_MAX_CONNECTIONS_TOTAL": 200,
        "WEBSOCKET_MAX_CONNECTIONS_PER_MATCH": 50,
        "WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT": 5,
        "WEBSOCKET_TICKET_TTL_SECONDS": 60,
        "WEBSOCKET_MAX_ACTIVE_TICKETS": 500,
        **overrides,
    }
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    with contextlib.ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(patch.object(settings, name, value))
        stack.enter_context(
            patch.object(cm_module, "_connection_manager", None)
        )
        stack.enter_context(patch.object(ws_auth, "_ticket_store", None))
        if not real_match_lookup:
            from app.api.routes import realtime as realtime_module

            stack.enter_context(
                patch.object(
                    realtime_module, "match_is_broadcastable", lambda match_id: True
                )
            )
        with TestClient(app) as client:
            yield client


def buy_ticket(client) -> str:
    """The browser flow: key in a header, ticket back in the body."""
    resp = client.post(TICKETS, headers={"X-API-Key": WRITE_KEY})
    assert resp.status_code == 201, resp.text
    return resp.json()["ticket"]


def closed_with(test, client, path=PRICES, **kwargs) -> int:
    """Connect expecting refusal; return the close code."""
    with test.assertRaises(WebSocketDisconnect) as caught:
        with client.websocket_connect(path, **kwargs):
            pass
    return caught.exception.code


@contextlib.contextmanager
def captured_logs():
    """Every log record from every logger, as formatted text.

    `assertLogs` is the wrong tool for asserting a secret is *absent*: it fails
    when the named logger emits nothing -- so the correct behaviour (issuing a
    ticket logs no line at all) reads as a test failure -- and it watches one
    logger, so a leak from a neighbour passes. This captures at the root instead.
    Callers assert the capture is non-empty, which is what stops "no leak found"
    from meaning "no records looked at".
    """
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = _Collect()
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


class HandshakeRefusesAnonymousSocketsTests(unittest.TestCase):
    """The defect itself: a socket with no credential used to be accepted."""

    def test_no_credential_is_refused(self):
        with wired() as client:
            code = closed_with(self, client)
        self.assertEqual(code, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)

    def test_a_wrong_key_is_refused(self):
        with wired() as client:
            code = closed_with(
                self, client, headers={"X-API-Key": "wrong-key"}
            )
        self.assertEqual(code, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)

    def test_a_valid_header_key_is_accepted(self):
        """The positive arm for programmatic clients. Without it, a handshake
        that refused everything would satisfy every refusal test here."""
        with wired() as client:
            with client.websocket_connect(
                PRICES, headers={"X-API-Key": WRITE_KEY}
            ):
                manager = cm_module.get_connection_manager()
                self.assertEqual(manager.subscriber_count("m1"), 1)

    def test_a_refused_handshake_registers_no_subscriber(self):
        """Refusing after `accept()` would still have cost a slot and would
        still have been counted, which is what an unbounded fan-out needs."""
        with wired() as client:
            closed_with(self, client)
            manager = cm_module.get_connection_manager()
            self.assertEqual(manager.subscriber_count("m1"), 0)
            self.assertEqual(manager.total_connections(), 0)

    def test_the_key_in_a_query_string_does_not_authenticate(self):
        """Explicitly refused, not merely unimplemented.

        A query-string credential lands in proxy access logs and browser
        history. Somebody will try it precisely because it is the only thing a
        browser can do without a ticket, so it gets a test rather than a
        comment.
        """
        with wired() as client:
            code = closed_with(self, client, f"{PRICES}?api_key={WRITE_KEY}")
        self.assertEqual(code, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)

    def test_the_disabled_flag_still_wins_over_a_valid_credential(self):
        """Ordering, pinned: a correct key must not switch the feature on."""
        with wired(PHASE10_REALTIME_PUSH_ENABLED=False) as client:
            code = closed_with(
                self, client, headers={"X-API-Key": WRITE_KEY}
            )
        from app.api.routes.realtime import REALTIME_DISABLED_CLOSE_CODE

        self.assertEqual(code, REALTIME_DISABLED_CLOSE_CODE)


class TicketFlowTests(unittest.TestCase):
    """The browser transport. `new WebSocket(url, protocols)` is the only lever
    a browser has, so the ticket rides `Sec-WebSocket-Protocol`."""

    def test_issuing_a_ticket_requires_the_write_key(self):
        with wired() as client:
            self.assertEqual(client.post(TICKETS).status_code, 401)
            self.assertEqual(
                client.post(
                    TICKETS, headers={"X-API-Key": "wrong-key"}
                ).status_code,
                401,
            )

    def test_a_ticket_is_not_the_write_key(self):
        """If the endpoint returned the key, the browser would hold a
        production secret and the query-string ban would be cosmetic."""
        with wired() as client:
            body = client.post(
                TICKETS, headers={"X-API-Key": WRITE_KEY}
            ).json()
        self.assertNotEqual(body["ticket"], WRITE_KEY)
        self.assertNotIn(WRITE_KEY, body["ticket"])
        # Long enough that guessing is not a strategy.
        self.assertGreaterEqual(len(body["ticket"]), 32)

    def test_the_response_names_the_subprotocol_to_offer(self):
        """The client must not have to hardcode the prefix; a mismatch would
        fail the handshake with no way to tell why."""
        with wired() as client:
            body = client.post(
                TICKETS, headers={"X-API-Key": WRITE_KEY}
            ).json()
        self.assertEqual(
            body["subprotocol"],
            f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}{body['ticket']}",
        )
        self.assertEqual(body["expires_in"], settings.WEBSOCKET_TICKET_TTL_SECONDS)

    def test_a_ticket_authenticates_the_handshake(self):
        with wired() as client:
            ticket = buy_ticket(client)
            offer = f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}{ticket}"
            with client.websocket_connect(PRICES, subprotocols=[offer]):
                manager = cm_module.get_connection_manager()
                self.assertEqual(manager.subscriber_count("m1"), 1)

    def test_a_ticket_is_single_use(self):
        """Otherwise it is a bearer token with a 60-second name."""
        with wired() as client:
            ticket = buy_ticket(client)
            offer = f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}{ticket}"
            with client.websocket_connect(PRICES, subprotocols=[offer]):
                pass
            self.assertEqual(
                closed_with(self, client, subprotocols=[offer]),
                ws_auth.WS_UNAUTHORIZED_CLOSE_CODE,
            )

    def test_a_ticket_expires(self):
        with wired(WEBSOCKET_TICKET_TTL_SECONDS=1) as client:
            ticket = buy_ticket(client)
            offer = f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}{ticket}"
            with patch.object(
                ws_auth.time, "time", return_value=time.time() + 5
            ):
                code = closed_with(self, client, subprotocols=[offer])
        self.assertEqual(code, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)

    def test_an_invented_ticket_is_refused(self):
        with wired() as client:
            offer = f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}not-a-real-ticket"
            code = closed_with(self, client, subprotocols=[offer])
        self.assertEqual(code, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)

    def test_the_accepted_socket_echoes_the_offered_subprotocol(self):
        """RFC 6455: a server that ignores the client's subprotocol offer
        leaves browsers free to fail the handshake."""
        with wired() as client:
            ticket = buy_ticket(client)
            offer = f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}{ticket}"
            with client.websocket_connect(PRICES, subprotocols=[offer]) as ws:
                self.assertEqual(
                    ws.accepted_subprotocol or "",
                    offer,
                )

    def test_the_ticket_store_is_bounded(self):
        """An unauthenticated caller cannot reach this endpoint, but an
        authenticated script in a loop should not be able to grow the process
        without limit either."""
        with wired(WEBSOCKET_MAX_ACTIVE_TICKETS=5) as client:
            for _ in range(20):
                client.post(TICKETS, headers={"X-API-Key": WRITE_KEY})
            self.assertLessEqual(ws_auth.get_ticket_store().active_count(), 5)


class ConnectionLimitTests(unittest.TestCase):
    """`_connections` was an unbounded dict of unbounded sets."""

    AUTH = {"X-API-Key": WRITE_KEY}

    def test_the_per_match_cap_refuses_the_socket_over_it(self):
        with wired(
            WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=2,
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=50,
        ) as client:
            with client.websocket_connect(PRICES, headers=self.AUTH), \
                    client.websocket_connect(PRICES, headers=self.AUTH):
                manager = cm_module.get_connection_manager()
                self.assertEqual(manager.subscriber_count("m1"), 2)
                code = closed_with(self, client, headers=self.AUTH)
                self.assertEqual(code, ws_auth.WS_AT_CAPACITY_CLOSE_CODE)
                # The refusal must not have displaced a live subscriber.
                self.assertEqual(manager.subscriber_count("m1"), 2)

    def test_the_global_cap_refuses_across_different_matches(self):
        """A per-match cap alone is bypassed by asking for a new match id."""
        with wired(
            WEBSOCKET_MAX_CONNECTIONS_TOTAL=2,
            WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=50,
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=50,
        ) as client:
            with client.websocket_connect(
                "/api/ws/matches/a/prices", headers=self.AUTH
            ), client.websocket_connect(
                "/api/ws/matches/b/prices", headers=self.AUTH
            ):
                code = closed_with(
                    self, client, "/api/ws/matches/c/prices", headers=self.AUTH
                )
                self.assertEqual(code, ws_auth.WS_AT_CAPACITY_CLOSE_CODE)
                self.assertEqual(
                    cm_module.get_connection_manager().total_connections(), 2
                )

    def test_the_per_client_cap_refuses_one_noisy_caller(self):
        """The cap that matters against a single host opening sockets in a loop,
        and the reason a global cap alone is not enough: without it one caller
        fills the global budget and locks every other operator out."""
        with wired(
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=1,
            WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=50,
            WEBSOCKET_MAX_CONNECTIONS_TOTAL=50,
        ) as client:
            with client.websocket_connect(
                "/api/ws/matches/a/prices", headers=self.AUTH
            ):
                code = closed_with(
                    self, client, "/api/ws/matches/b/prices", headers=self.AUTH
                )
        self.assertEqual(code, ws_auth.WS_AT_CAPACITY_CLOSE_CODE)

    def test_a_disconnect_frees_the_slot(self):
        """Without this, the caps become a permanent budget that leaks to zero
        over an uptime and the endpoint dies quietly."""
        with wired(WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=1) as client:
            with client.websocket_connect(PRICES, headers=self.AUTH):
                pass
            manager = cm_module.get_connection_manager()
            self.assertEqual(manager.subscriber_count("m1"), 0)
            self.assertEqual(manager.total_connections(), 0)
            self.assertEqual(manager.client_connection_count(), {})
            # And the freed slot is genuinely reusable.
            with client.websocket_connect(PRICES, headers=self.AUTH):
                self.assertEqual(manager.subscriber_count("m1"), 1)

    def test_the_client_identity_is_the_rate_limiters_identity(self):
        """One definition of "who is calling", not two.

        `_client_host` already resolves X-Forwarded-For from the right under
        `TRUSTED_PROXY_HEADER`, with a documented reason per fallback. A second
        copy for WebSockets would drift, and the per-client cap would then be
        keyed on something a caller can rotate at will.
        """
        from app.core.rate_limit import _client_host

        self.assertIs(ws_auth.client_identity, _client_host)


class UnknownMatchIsRefusedTests(unittest.TestCase):
    """The route must refuse a match the scheduler can never broadcast to.

    Every test here runs with `real_match_lookup=True`, so the predicate resolves
    against the real `SportMarketLinkStore` — the same store both broadcast sites
    enumerate. Without this, an authenticated caller could mint an unbounded number
    of distinct subscriber buckets by varying the path segment, and each socket
    would sit in a bucket nothing will ever push to.

    Ordering is asserted, not assumed: authentication runs *first*, so an anonymous
    caller cannot learn which match ids exist by comparing close codes.
    """

    AUTH = {"X-API-Key": WRITE_KEY}

    def setUp(self) -> None:
        from app.kernel.kernel_db import close_kernel_db, init_kernel_db
        from app.kernel.sport_market_link_store import SportMarketLinkStore

        close_kernel_db()
        init_kernel_db()
        SportMarketLinkStore().upsert_link(
            match_id="m1",
            contract_id="c-m1",
            source="polymarket",
            outcome_label="YES",
            mapped_outcome="home_win",
            link_method="rule",
            link_confidence=0.95,
            verified=True,
            market_question="q",
            implied_prob=0.6,
        )

    def tearDown(self) -> None:
        from app.kernel.kernel_db import close_kernel_db

        close_kernel_db()

    def test_a_match_with_a_verified_link_is_accepted(self):
        """The positive arm, through the route, against the real store."""
        with wired(real_match_lookup=True) as client:
            with client.websocket_connect(PRICES, headers=self.AUTH):
                manager = cm_module.get_connection_manager()
                self.assertEqual(manager.subscriber_count("m1"), 1)

    def test_an_unknown_match_is_refused_with_its_own_close_code(self):
        from app.api.routes.realtime import WS_UNKNOWN_MATCH_CLOSE_CODE

        with wired(real_match_lookup=True) as client:
            code = closed_with(
                self, client, "/api/ws/matches/not-a-match/prices", headers=self.AUTH
            )
        self.assertEqual(code, WS_UNKNOWN_MATCH_CLOSE_CODE)

    def test_an_unknown_match_creates_no_bucket_and_accepts_nothing(self):
        """The whole point of checking before `accept()`. A bucket created here
        would be the unbounded-growth surface the caps exist to close."""
        with wired(real_match_lookup=True) as client:
            manager = cm_module.get_connection_manager()
            before = (
                manager.total_connections(),
                manager.client_connection_count(),
                manager.match_ids(),
            )
            closed_with(
                self, client, "/api/ws/matches/not-a-match/prices", headers=self.AUTH
            )
            self.assertEqual(manager.subscriber_count("not-a-match"), 0)
            self.assertEqual(
                (
                    manager.total_connections(),
                    manager.client_connection_count(),
                    manager.match_ids(),
                ),
                before,
            )
            self.assertEqual(before, (0, {}, []))

    def test_a_broken_lookup_refuses_rather_than_admitting(self):
        """Fail closed on a broken table, with a code of its own.

        Reported as unavailable rather than as "unknown match": a client that
        retries an unknown id forever is a different operational picture from one
        whose backing table is broken, and the second is the one an operator must
        page on.
        """
        from sqlalchemy import text

        from app.api.routes.realtime import WS_LOOKUP_UNAVAILABLE_CLOSE_CODE
        from app.kernel.kernel_db import get_kernel_session

        session = get_kernel_session()
        try:
            session.execute(text("DROP TABLE kernel_sport_market_links"))
            session.commit()
        finally:
            session.close()

        with wired(real_match_lookup=True) as client:
            code = closed_with(self, client, PRICES, headers=self.AUTH)
            self.assertEqual(code, WS_LOOKUP_UNAVAILABLE_CLOSE_CODE)
            self.assertEqual(
                cm_module.get_connection_manager().total_connections(), 0
            )

    def test_an_anonymous_caller_cannot_probe_which_matches_exist(self):
        """Ordering, pinned. If the match check ran first, the close code would
        differ between a real and an invented id for a caller with no credential,
        which is an existence oracle on the link table."""
        with wired(real_match_lookup=True) as client:
            known = closed_with(self, client, PRICES)
            unknown = closed_with(
                self, client, "/api/ws/matches/not-a-match/prices"
            )
        self.assertEqual(known, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)
        self.assertEqual(unknown, ws_auth.WS_UNAUTHORIZED_CLOSE_CODE)

    def test_the_disabled_flag_still_wins_over_a_known_match(self):
        from app.api.routes.realtime import REALTIME_DISABLED_CLOSE_CODE

        with wired(
            real_match_lookup=True, PHASE10_REALTIME_PUSH_ENABLED=False
        ) as client:
            code = closed_with(self, client, headers=self.AUTH)
        self.assertEqual(code, REALTIME_DISABLED_CLOSE_CODE)

    def test_every_refusal_code_is_wire_legal_and_distinct(self):
        """RFC 6455 discards anything below 1000: the browser reports 1006 and the
        client cannot tell the five refusals apart. Distinctness is what makes the
        close code usable by the frontend's fallback logic."""
        from app.api.routes.realtime import (
            REALTIME_DISABLED_CLOSE_CODE,
            WS_LOOKUP_UNAVAILABLE_CLOSE_CODE,
            WS_UNKNOWN_MATCH_CLOSE_CODE,
        )

        codes = {
            "disabled": REALTIME_DISABLED_CLOSE_CODE,
            "unauthorized": ws_auth.WS_UNAUTHORIZED_CLOSE_CODE,
            "unknown match": WS_UNKNOWN_MATCH_CLOSE_CODE,
            "at capacity": ws_auth.WS_AT_CAPACITY_CLOSE_CODE,
            "lookup unavailable": WS_LOOKUP_UNAVAILABLE_CLOSE_CODE,
        }
        for name, code in codes.items():
            with self.subTest(refusal=name):
                self.assertTrue(1000 <= code <= 4999, f"{name}={code}")
        self.assertEqual(len(set(codes.values())), len(codes), codes)

    def test_the_refusal_leaks_no_sql_path_or_exception_text(self):
        """A close reason and a log line are both operator-facing surfaces, and
        the broken-table path is the one with an exception in hand."""
        from sqlalchemy import text

        from app.kernel.kernel_db import get_kernel_session

        session = get_kernel_session()
        try:
            session.execute(text("DROP TABLE kernel_sport_market_links"))
            session.commit()
        finally:
            session.close()

        with wired(real_match_lookup=True) as client:
            with captured_logs() as records:
                with self.assertRaises(WebSocketDisconnect) as caught:
                    with client.websocket_connect(PRICES, headers=self.AUTH):
                        pass
            blob = " ".join(records)

        reason = caught.exception.reason or ""
        self.assertTrue(records, "captured no records, so this proved nothing")
        for forbidden in (
            "SELECT",
            "kernel_sport_market_links",
            "no such table",
            "sqlite",
            ".db",
            "Traceback",
            WRITE_KEY,
        ):
            with self.subTest(leak=forbidden):
                self.assertNotIn(forbidden.lower(), reason.lower())
        # The log may name the failure type for triage, but never the statement,
        # the table, the file path, or the key.
        for forbidden in ("SELECT ", "no such table", WRITE_KEY):
            with self.subTest(log_leak=forbidden):
                self.assertNotIn(forbidden.lower(), blob.lower())


class PostureIsDerivedNotDeclaredTests(unittest.TestCase):
    """The preflight posture must be a measurement, not a claim.

    It was two module constants reading `False`. Flipping them to `True` would
    have opened the production gate without changing a line of enforcement, so
    each one now has to follow the setting that actually decides the behaviour.
    """

    def test_authenticated_follows_the_configured_write_key(self):
        with patch.object(settings, "API_WRITE_KEY", WRITE_KEY):
            self.assertTrue(preflight.realtime_push_posture()["authenticated"])
        with patch.object(settings, "API_WRITE_KEY", ""):
            self.assertFalse(preflight.realtime_push_posture()["authenticated"])

    def test_a_whitespace_only_key_does_not_count_as_configured(self):
        with patch.object(settings, "API_WRITE_KEY", "   "):
            self.assertFalse(preflight.realtime_push_posture()["authenticated"])

    def test_connection_limited_follows_every_cap(self):
        """All three must be finite and positive. A single unlimited dimension
        is a bypass for the other two -- the global-cap test above is the
        behavioural proof of that shape.
        """
        caps = (
            "WEBSOCKET_MAX_CONNECTIONS_TOTAL",
            "WEBSOCKET_MAX_CONNECTIONS_PER_MATCH",
            "WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT",
        )
        with patch.multiple(
            settings, **{name: 10 for name in caps}
        ):
            self.assertTrue(
                preflight.realtime_push_posture()["connection_limited"]
            )
        for name in caps:
            with self.subTest(cap=name):
                with patch.multiple(settings, **{item: 10 for item in caps}):
                    with patch.object(settings, name, 0):
                        self.assertFalse(
                            preflight.realtime_push_posture()[
                                "connection_limited"
                            ],
                            f"{name}=0 left the posture claiming a limit",
                        )

    def test_production_now_accepts_the_flag_when_the_settings_back_it(self):
        """The point of the batch: the gate opens on enforcement, not on a
        hand-edited constant."""
        with patch.multiple(
            settings,
            PMRF_ENV="production",
            API_WRITE_KEY=WRITE_KEY,
            ALLOW_OPEN_WRITES=False,
            LLM_DAILY_COST_CAP_USD=25.0,
            OPENAPI_ENABLED=False,
            SERVER_RELOAD=False,
            BACKUP_SCHEDULE_ENABLED=False,
            CORS_ALLOWED_ORIGINS=["https://app.example.com"],
            PHASE10_REALTIME_PUSH_ENABLED=True,
            WEBSOCKET_MAX_CONNECTIONS_TOTAL=200,
            WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=50,
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=5,
        ):
            self.assertEqual(preflight.production_config_failures(), [])

    def test_production_still_refuses_an_unlimited_cap(self):
        """The negative arm of the test above, and the one that matters: the
        flag alone must not be sufficient."""
        with patch.multiple(
            settings,
            PMRF_ENV="production",
            API_WRITE_KEY=WRITE_KEY,
            ALLOW_OPEN_WRITES=False,
            LLM_DAILY_COST_CAP_USD=25.0,
            OPENAPI_ENABLED=False,
            SERVER_RELOAD=False,
            BACKUP_SCHEDULE_ENABLED=False,
            CORS_ALLOWED_ORIGINS=["https://app.example.com"],
            PHASE10_REALTIME_PUSH_ENABLED=True,
            WEBSOCKET_MAX_CONNECTIONS_TOTAL=0,
            WEBSOCKET_MAX_CONNECTIONS_PER_MATCH=50,
            WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT=5,
        ):
            failures = preflight.production_config_failures()
        self.assertTrue(
            any("PHASE10_REALTIME_PUSH_ENABLED" in item for item in failures),
            failures,
        )

    def test_the_posture_is_coupled_to_the_route_that_enforces_it(self):
        """Guards against the failure mode constraint 11 names.

        A posture derived only from settings would still read `authenticated`
        if the route stopped checking. This asserts the route actually calls the
        authenticator, so deleting the check reddens a test rather than
        silently re-opening the production gate.
        """
        import inspect

        from app.api.routes import realtime

        source = inspect.getsource(realtime.price_stream)
        self.assertIn("authenticate_websocket", source)
        self.assertIn("WS_UNAUTHORIZED_CLOSE_CODE", source)


class BothPathsAreOnTheProductionAppTests(unittest.TestCase):
    """The one assertion this file owes the composition root.

    Everything above mounts `api_router` on a local `FastAPI()` -- closer than a
    bare router, but still not `app.main.app`. A ticket route that was never
    included would leave every test here green and the browser flow dead, which is
    the reachability gap this repo has hit repeatedly. Read off the route table
    with no client, so the lifespan never runs.
    """

    def test_the_prices_socket_and_the_ticket_route_are_both_mounted(self):
        from app.main import app as production_app

        paths = {getattr(route, "path", "") for route in production_app.routes}
        self.assertIn("/api/ws/matches/{match_id}/prices", paths)
        self.assertIn("/api/ws/tickets", paths)

    def test_the_unprefixed_path_is_not_served(self):
        """Pins the fact the frontend hook currently gets wrong, so a later batch
        fixing the client cannot be misled about which side is authoritative."""
        from app.main import app as production_app

        paths = {getattr(route, "path", "") for route in production_app.routes}
        self.assertNotIn("/ws/matches/{match_id}/prices", paths)


class NothingSecretReachesTheClientOrTheLogTests(unittest.TestCase):
    """Constraint: neither the key, the ticket, nor full connection details."""

    def test_the_refusal_reason_names_no_credential(self):
        with wired() as client:
            with self.assertRaises(WebSocketDisconnect) as caught:
                with client.websocket_connect(
                    PRICES, headers={"X-API-Key": "wrong-key"}
                ):
                    pass
        reason = caught.exception.reason or ""
        self.assertNotIn(WRITE_KEY, reason)
        self.assertNotIn("wrong-key", reason)

    def test_a_refused_handshake_logs_no_credential(self):
        with wired() as client:
            ticket = buy_ticket(client)
            with captured_logs() as records:
                closed_with(self, client, headers={"X-API-Key": "wrong-key"})
                closed_with(
                    self,
                    client,
                    subprotocols=[
                        f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}bogus-ticket"
                    ],
                )
            blob = " ".join(records)

        # The refusals really did log something, so the assertions below are
        # looking at the lines the operator will actually see.
        self.assertIn("refused", blob.lower())
        self.assertNotIn(WRITE_KEY, blob)
        self.assertNotIn("wrong-key", blob)
        self.assertNotIn(ticket, blob)
        self.assertNotIn("bogus-ticket", blob)

    def test_the_issued_ticket_is_not_logged(self):
        """A nearby marker proves capture works without requiring route logging."""
        with wired() as client:
            with captured_logs() as records:
                ticket = buy_ticket(client)
                logging.getLogger(__name__).info("ticket issuance capture marker")
        self.assertIn("ticket issuance capture marker", " ".join(records))
        self.assertNotIn(ticket, " ".join(records))
