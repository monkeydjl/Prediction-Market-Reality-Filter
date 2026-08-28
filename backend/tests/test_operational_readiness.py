import asyncio
import contextlib
import io
import logging
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.api.routes import events as events_routes
from app.api.security import require_write_key, optional_write_key
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core import runtime_stores
from app.core.config import settings
from scripts import backup_stores, healthcheck


class OpenAPIContractTests(unittest.TestCase):
    def test_event_routes_declare_response_models(self):
        missing = [
            route.path
            for route in events_routes.router.routes
            if isinstance(route, APIRoute) and route.response_model is None
        ]
        self.assertEqual(missing, [])


class WriteAuthTests(unittest.TestCase):
    SENSITIVE_GET_ROUTES = {
        "/events/discover",
        "/events/sports/world-cup/data/sources/status",
        "/world-cup/predictions/batch-switch-engine-stream",
    }

    def _client(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        return TestClient(app)

    def _iter_api_routes(self, router, prefix=""):
        for route in router.routes:
            if isinstance(route, APIRoute):
                yield prefix + route.path, route
            elif hasattr(route, "original_router") and hasattr(route, "include_context"):
                yield from self._iter_api_routes(
                    route.original_router,
                    prefix + route.include_context.prefix,
                )

    def _has_write_key_dependency(self, dependant) -> bool:
        return dependant.call in (require_write_key, optional_write_key) or any(
            self._has_write_key_dependency(child)
            for child in dependant.dependencies
        )

    def _route_requires_write_key(self, route: APIRoute) -> bool:
        return any(
            self._has_write_key_dependency(dependency)
            for dependency in route.dependant.dependencies
        )

    def test_all_write_and_sensitive_get_routes_require_write_key(self):
        missing_auth = []
        found_sensitive_gets = set()

        for path, route in self._iter_api_routes(api_router):
            methods = route.methods or set()
            is_write_method = bool(methods - {"GET", "HEAD", "OPTIONS"})
            is_sensitive_get = "GET" in methods and path in self.SENSITIVE_GET_ROUTES
            if is_sensitive_get:
                found_sensitive_gets.add(path)
            if (is_write_method or is_sensitive_get) and not self._route_requires_write_key(route):
                missing_auth.append(f"{','.join(sorted(methods))} {path}")

        self.assertEqual(missing_auth, [])
        self.assertEqual(found_sensitive_gets, self.SENSITIVE_GET_ROUTES)

    def test_llm_diagnostics_route_is_registered_as_read_only(self):
        routes = {
            path: route
            for path, route in self._iter_api_routes(api_router)
            if isinstance(route, APIRoute)
        }
        route = routes.get("/llm/diagnostics")

        self.assertIsNotNone(route)
        self.assertIn("GET", route.methods)
        self.assertFalse(self._route_requires_write_key(route))

    def test_write_key_blocks_mutating_and_costly_routes_when_configured(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self._client().post("/events/analyze", json={})
        self.assertEqual(resp.status_code, 401)

    def test_write_key_allows_request_with_matching_header(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(
                    events_routes,
                    "analyze_event_question",
                    new=AsyncMock(return_value={"event_id": "evt1"}),
                ):
            resp = self._client().post(
                "/events/analyze",
                headers={"X-API-Key": "secret"},
                json={
                    "event_question": "Will the test pass?",
                    "baseline_probability": 50,
                    "news_context": "context",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["event_id"], "evt1")

    def test_write_key_wrong_header_rejected(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self._client().post(
                "/events/analyze",
                headers={"X-API-Key": "wrong"},
                json={},
            )
        self.assertEqual(resp.status_code, 401)

    def test_keyless_with_opt_in_passes_through(self):
        # Empty key + explicit ALLOW_OPEN_WRITES: the operator opted into public
        # writes, so the request path passes through. (The startup guard — not the
        # request path — is what refuses an *accidental* keyless boot; see
        # StartupGuardTests.)
        with patch.object(settings, "API_WRITE_KEY", ""), \
                patch.object(settings, "ALLOW_OPEN_WRITES", True), \
                patch.object(
                    events_routes,
                    "analyze_event_question",
                    new=AsyncMock(return_value={"event_id": "evt1"}),
                ):
            resp = self._client().post(
                "/events/analyze",
                json={
                    "event_question": "Will the test pass?",
                    "baseline_probability": 50,
                    "news_context": "context",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["event_id"], "evt1")


class StartupGuardTests(unittest.TestCase):
    """The fail-closed P0: the app must refuse to boot with no write key unless
    ALLOW_OPEN_WRITES is explicitly set, so a deploy that forgets the key does
    not come up silently wide open."""

    def test_lifespan_refuses_keyless_boot_without_opt_in(self):
        from app.main import app, lifespan

        with patch.object(settings, "API_WRITE_KEY", ""), \
                patch.object(settings, "ALLOW_OPEN_WRITES", False), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertRaises(RuntimeError):
                # TestClient as a context manager drives the lifespan startup.
                with TestClient(app):
                    pass

    def test_lifespan_boots_keyless_with_opt_in(self):
        from app.main import app

        started = {"n": 0}
        with patch.object(settings, "API_WRITE_KEY", ""), \
                patch.object(settings, "ALLOW_OPEN_WRITES", True), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.start_scheduler", lambda: started.__setitem__("n", started["n"] + 1)), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        self.assertEqual(started["n"], 1)

    def test_lifespan_boots_with_key(self):
        from app.main import app

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass

    def test_lifespan_refuses_boot_when_sqlite_maintenance_fails(self):
        from app.main import app

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.main.sqlite_db.maintain",
                      side_effect=RuntimeError("bad loop db")), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertRaisesRegex(RuntimeError, "bad loop db"):
                with TestClient(app):
                    pass

    def test_lifespan_does_not_check_llm_by_default(self):
        from app.main import app

        check = AsyncMock()
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", False), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.validate_primary_llm_startup", check), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        check.assert_not_awaited()

    def test_lifespan_runs_llm_startup_check_when_enabled(self):
        from app.main import app

        check = AsyncMock()
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", True), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.validate_primary_llm_startup", check), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        check.assert_awaited_once()

    def test_lifespan_refuses_boot_when_llm_startup_check_fails(self):
        from app.main import app

        check = AsyncMock(side_effect=RuntimeError("bad llm key"))
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", True), \
                patch("app.main.validate_primary_llm_startup", check), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass
        check.assert_awaited_once()

    def _boot(self, cap):
        """Drive one lifespan startup with the given cost cap, capturing logs."""
        from app.main import app

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_DAILY_COST_CAP_USD", cap), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertLogs("app.main", level="INFO") as captured:
                with TestClient(app):
                    pass
        return captured.output

    def test_lifespan_warns_when_cost_cap_disabled(self):
        """0 is the shipped default and means unlimited, not disabled.

        Without this line the only place that fact is written down is
        .env.example, so an operator who never opened it gets an uncapped paid
        key and no signal.
        """
        output = self._boot(0.0)
        unlimited = [
            line for line in output
            if "UNLIMITED" in line and line.startswith("WARNING")
        ]
        self.assertEqual(len(unlimited), 1, output)

    def test_lifespan_reports_a_configured_cost_cap(self):
        output = self._boot(25.0)
        self.assertTrue(
            any("Daily LLM spend cap: $25.00" in line for line in output),
            output,
        )
        self.assertFalse(any("UNLIMITED" in line for line in output), output)

    def test_lifespan_skips_scheduler_when_disabled(self):
        from app.main import app

        calls = {"start": 0, "stop": 0}
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "SCHEDULER_ENABLED", False), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch(
                    "app.main.start_scheduler",
                    lambda: calls.__setitem__("start", calls["start"] + 1),
                ), \
                patch(
                    "app.main.stop_scheduler",
                    lambda: calls.__setitem__("stop", calls["stop"] + 1),
                ):
            with TestClient(app):
                pass
        self.assertEqual(calls, {"start": 0, "stop": 0})

    def test_lifespan_reconciles_interrupted_optimization_tasks(self):
        """Wiring pin: a 'running' optimization row left by a dead process is
        terminal again after boot.

        Without this, `/auto-tune/status/{task_id}` reports the task running
        forever — nothing re-attaches an asyncio.Task to a stored row — and
        `cleanup_old_tasks` never prunes it because it only prunes terminal
        statuses.
        """
        from app.main import app
        from app.memory import optimization_task_store as task_store
        from app.utils import sqlite_db as util_sqlite_db

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "v2_loop.db")
            with patch.object(util_sqlite_db, "loop_db_path", return_value=db_path):
                task_store.upsert_task({
                    "task_id": "stuck", "engine_name": "hybrid",
                    "status": "running", "progress": 1, "total": 5, "logs": [],
                })
                with patch.object(settings, "API_WRITE_KEY", "secret"), \
                        patch.object(settings, "SCHEDULER_ENABLED", False), \
                        patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                        patch("app.main.start_scheduler", lambda: None), \
                        patch("app.main.stop_scheduler", lambda: None), \
                        patch("app.utils.prediction_db.init_prediction_db",
                              lambda: None), \
                        patch(
                            "app.services.world_cup_scoring_service."
                            "score_all_finished_matches",
                            return_value={"status": "ok"},
                        ):
                    with TestClient(app):
                        pass
                stuck = task_store.get_task("stuck")
        self.assertEqual(stuck["status"], "failed")
        self.assertIsNotNone(stuck["completed_at"])
        self.assertIn("中断", stuck["error"])


class LLMStartupCheckTests(unittest.TestCase):
    def test_llm_startup_check_rejects_failed_gateway_result(self):
        from app.services.llm_gateway_service import LLMResult
        from app.services.llm_startup_check_service import validate_primary_llm_startup

        gateway = AsyncMock(return_value=LLMResult(ok=False, degraded_reason="no route"))
        with patch.object(settings, "OPENAI_API_KEY", ""), \
                patch("app.services.llm_startup_check_service.complete_chat", gateway, create=True):
            with self.assertRaisesRegex(RuntimeError, "Primary LLM startup check failed: no route"):
                asyncio.run(validate_primary_llm_startup())

    def test_llm_startup_check_uses_gateway_without_legacy_key(self):
        from app.services.llm_gateway_service import LLMResult
        from app.services.llm_startup_check_service import validate_primary_llm_startup

        gateway = AsyncMock(return_value=LLMResult(ok=True, content="ok"))
        with patch.object(settings, "OPENAI_API_KEY", ""), \
                patch("app.services.llm_startup_check_service.complete_chat", gateway, create=True):
            asyncio.run(validate_primary_llm_startup())

        gateway.assert_awaited_once()
        self.assertEqual(gateway.await_args.kwargs["task"], "startup_check")

    def test_llm_startup_check_redacts_key_from_failure(self):
        from app.services.llm_startup_check_service import validate_primary_llm_startup

        with patch.object(settings, "OPENAI_API_KEY", "secret-key"), \
                patch(
                    "app.services.llm_startup_check_service.complete_chat",
                    AsyncMock(side_effect=RuntimeError("bad secret-key")),
                    create=True,
                ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(validate_primary_llm_startup())

        message = str(ctx.exception)
        self.assertNotIn("secret-key", message)
        self.assertIn("<redacted>", message)


class RateLimitTests(unittest.TestCase):
    def test_rate_limit_returns_429_after_configured_limit(self):
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1):
            client = TestClient(app)
            self.assertEqual(client.get("/ping").status_code, 200)
            resp = client.get("/ping")

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.headers["Retry-After"], "60")

    def test_rate_limit_uses_normalized_path_for_dynamic_segments(self):
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/items/{item_id}/resolve")
        async def resolve_item(item_id: str):
            return {"item_id": item_id}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1):
            client = TestClient(app)
            self.assertEqual(client.get("/items/a/resolve").status_code, 200)
            resp = client.get("/items/b/resolve")

        self.assertEqual(resp.status_code, 429)

    def test_rate_limit_ignores_forwarded_header_by_default(self):
        # Default (TRUSTED_PROXY_HEADER=false): X-Forwarded-For is attacker-
        # controllable on direct deploys, so it MUST be ignored. All requests
        # share the TestClient socket peer as the rate-limit key.
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1), \
                patch.object(settings, "TRUSTED_PROXY_HEADER", False):
            client = TestClient(app)
            self.assertEqual(
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"}).status_code,
                200,
            )
            # Same socket peer even though we rotated the spoofed header.
            resp = client.get("/ping", headers={"X-Forwarded-For": "203.0.113.2"})

        self.assertEqual(resp.status_code, 429)

    def test_rate_limit_honors_forwarded_header_when_trusted_proxy_enabled(self):
        # Opt-in (TRUSTED_PROXY_HEADER=true) behind a proxy that *replaces*
        # X-Forwarded-For with the real peer — deploy/Caddyfile.example does
        # this (`header_up X-Forwarded-For {remote_host}`), so the header holds
        # exactly one address and it is not caller-controlled.
        #
        # This is the only topology this test covers, which is why it stayed
        # green through the leftmost-entry bug: with a single-entry chain the
        # leftmost and rightmost address are the same one. The appending
        # topology (deploy/nginx.conf.example) is covered below.
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1), \
                patch.object(settings, "TRUSTED_PROXY_HEADER", True):
            client = TestClient(app)
            self.assertEqual(
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"}).status_code,
                200,
            )
            self.assertEqual(
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"}).status_code,
                429,
            )
            # A different real client -> different rate-limit key -> allowed.
            resp = client.get("/ping", headers={"X-Forwarded-For": "203.0.113.2"})

        self.assertEqual(resp.status_code, 200)

    # ── E3: trusting the wrong end of X-Forwarded-For ─────────────────────
    # deploy/nginx.conf.example forwards `$proxy_add_x_forwarded_for`, which
    # APPENDS the real peer to whatever the caller sent, so the app receives
    # "<caller-supplied>, <real peer>". Keying off the leftmost entry handed the
    # rate-limit key to the caller: measured at limit=2, one attacker rotating
    # the prefix got 8/8 requests through. The client is read `hops` addresses
    # from the RIGHT instead.

    def _limited_app(self):
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        return app

    @staticmethod
    def _proxy_settings(*, trusted=True, hops=1, limit=1):
        return (
            patch.object(settings, "RATE_LIMIT_ENABLED", True),
            patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60),
            patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", limit),
            patch.object(settings, "TRUSTED_PROXY_HEADER", trusted),
            patch.object(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", hops),
        )

    @contextlib.contextmanager
    def _proxied(self, **kw):
        with contextlib.ExitStack() as stack:
            for p in self._proxy_settings(**kw):
                stack.enter_context(p)
            yield

    def test_rotating_the_spoofed_prefix_cannot_buy_a_fresh_bucket(self):
        client = TestClient(self._limited_app())
        attacker = "203.0.113.9"

        with self._proxied(limit=1):
            first = client.get(
                "/ping", headers={"X-Forwarded-For": f"10.0.0.0, {attacker}"}
            ).status_code
            # Every later request rotates the caller-supplied prefix. Under the
            # leftmost read each of these was a brand-new bucket.
            rotated = [
                client.get(
                    "/ping", headers={"X-Forwarded-For": f"10.0.0.{i}, {attacker}"}
                ).status_code
                for i in range(1, 6)
            ]

        self.assertEqual(first, 200)
        self.assertEqual(rotated, [429] * 5)

    def test_two_real_clients_behind_an_appending_proxy_keep_separate_buckets(self):
        # The guard above must not be satisfied by collapsing everyone into one
        # bucket: distinct real peers still have to be throttled independently.
        client = TestClient(self._limited_app())

        with self._proxied(limit=1):
            a_first = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.1, 203.0.113.1"}
            ).status_code
            a_again = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.1, 203.0.113.1"}
            ).status_code
            b_first = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.1, 203.0.113.2"}
            ).status_code

        self.assertEqual((a_first, a_again, b_first), (200, 429, 200))

    def test_hop_count_of_two_reads_past_the_cdn_hop(self):
        # CDN sets XFF to the client, nginx appends the CDN edge: "client, edge".
        # With two trusted hops the client is the second address from the right.
        client = TestClient(self._limited_app())

        with self._proxied(hops=2, limit=1):
            first = client.get(
                "/ping", headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.7"}
            ).status_code
            # Same client, a different CDN edge -> still the same bucket.
            same_client = client.get(
                "/ping", headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.8"}
            ).status_code
            other_client = client.get(
                "/ping", headers={"X-Forwarded-For": "203.0.113.2, 198.51.100.7"}
            ).status_code

        self.assertEqual((first, same_client, other_client), (200, 429, 200))

    def test_short_chain_falls_back_to_real_ip_never_the_leftmost(self):
        # Two hops declared but only one address arrived: the request did not
        # traverse the expected proxies. X-Real-IP is replaced by both shipped
        # proxy configs, so it is trustworthy; the leftmost entry is not.
        client = TestClient(self._limited_app())

        with self._proxied(hops=2, limit=1):
            first = client.get(
                "/ping",
                headers={"X-Forwarded-For": "10.0.0.1", "X-Real-IP": "203.0.113.9"},
            ).status_code
            # Rotating the caller-supplied entry must not buy a new bucket.
            rotated = client.get(
                "/ping",
                headers={"X-Forwarded-For": "10.0.0.2", "X-Real-IP": "203.0.113.9"},
            ).status_code
            # ...but a different X-Real-IP is a genuinely different client and
            # must get its own. This is the arm that pins the key to X-Real-IP
            # specifically: drop the fallback and both requests collapse onto
            # the socket peer, making this 429. Asserting only the shared-bucket
            # arm above would pass either way.
            other_client = client.get(
                "/ping",
                headers={"X-Forwarded-For": "10.0.0.3", "X-Real-IP": "203.0.113.8"},
            ).status_code

        self.assertEqual((first, rotated, other_client), (200, 429, 200))

    def test_short_chain_without_real_ip_falls_back_to_socket_peer(self):
        client = TestClient(self._limited_app())

        with self._proxied(hops=3, limit=1):
            first = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
            ).status_code
            rotated = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.3, 10.0.0.4"}
            ).status_code

        # Both collapse onto the TestClient socket peer rather than onto any
        # caller-supplied address.
        self.assertEqual((first, rotated), (200, 429))

    def test_forwarded_header_sent_twice_is_joined_before_slicing(self):
        # Reading only the first header instance would let a caller push our own
        # proxy's entry out of the trusted tail by splitting the chain in two.
        client = TestClient(self._limited_app())

        with self._proxied(limit=1):
            first = client.get(
                "/ping",
                headers=[("X-Forwarded-For", "10.0.0.1"),
                         ("X-Forwarded-For", "203.0.113.9")],
            ).status_code
            rotated = client.get(
                "/ping",
                headers=[("X-Forwarded-For", "10.0.0.2"),
                         ("X-Forwarded-For", "203.0.113.9")],
            ).status_code

        self.assertEqual((first, rotated), (200, 429))

    def test_hop_count_below_one_is_clamped_to_one(self):
        # 0 would mean `chain[-0]` == `chain[0]` — the leftmost, caller-supplied
        # entry — so the clamp is what keeps a stray 0 from reopening the hole.
        client = TestClient(self._limited_app())

        with self._proxied(hops=0, limit=1):
            first = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.1, 203.0.113.9"}
            ).status_code
            rotated = client.get(
                "/ping", headers={"X-Forwarded-For": "10.0.0.2, 203.0.113.9"}
            ).status_code

        self.assertEqual((first, rotated), (200, 429))

    def test_warns_once_when_proxy_headers_arrive_but_trust_is_off(self):
        # The documented default: loopback-bound app behind nginx with
        # TRUSTED_PROXY_HEADER unset. Every caller shares the proxy's bucket.
        client = TestClient(self._limited_app())

        with self._proxied(trusted=False, limit=50):
            with self.assertLogs("app.core.rate_limit", level="WARNING") as caught:
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"})
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.2"})
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.3"})

        self.assertEqual(len(caught.records), 1, "should warn once per process")
        self.assertIn("every caller shares one bucket", caught.output[0])

    def test_warns_when_trust_is_on_but_no_proxy_headers_arrive(self):
        client = TestClient(self._limited_app())

        with self._proxied(trusted=True, limit=50):
            with self.assertLogs("app.core.rate_limit", level="WARNING") as caught:
                client.get("/ping")

        self.assertEqual(len(caught.records), 1)
        self.assertIn("not behind the trusted proxy", caught.output[0])

    def test_no_warning_when_the_configuration_matches_the_traffic(self):
        logger = logging.getLogger("app.core.rate_limit")

        # Proxy headers present and trusted.
        with self._proxied(trusted=True, limit=50):
            client = TestClient(self._limited_app())
            with self.assertNoLogs(logger, level="WARNING"):
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"})

        # No proxy headers and no trust configured.
        with self._proxied(trusted=False, limit=50):
            client = TestClient(self._limited_app())
            with self.assertNoLogs(logger, level="WARNING"):
                client.get("/ping")


class _FakeScheduler:
    """Stand-in for the APScheduler whose `running` is a read-only property."""

    def __init__(self, running: bool):
        self.running = running


class HealthTests(unittest.TestCase):
    def test_cors_defaults_do_not_use_wildcard_methods_or_headers(self):
        self.assertNotIn("*", settings.CORS_ALLOWED_METHODS)
        self.assertNotIn("*", settings.CORS_ALLOWED_HEADERS)

    def test_cors_rejects_wildcard_origin_with_credentials(self):
        from app.main import _validate_cors_settings

        with patch.object(settings, "CORS_ALLOWED_ORIGINS", ["*"]), \
                patch.object(settings, "CORS_ALLOW_CREDENTIALS", True):
            with self.assertRaises(RuntimeError):
                _validate_cors_settings()

    def test_cors_rejects_wildcard_methods_or_headers(self):
        from app.main import _validate_cors_settings

        with patch.object(settings, "CORS_ALLOWED_METHODS", ["*"]):
            with self.assertRaises(RuntimeError):
                _validate_cors_settings()
        with patch.object(settings, "CORS_ALLOWED_HEADERS", ["*"]):
            with self.assertRaises(RuntimeError):
                _validate_cors_settings()

    def test_cors_preflight_allows_expected_write_headers(self):
        from app.main import app

        resp = TestClient(app).options(
            "/api/events/analyze",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "content-type,x-api-key,x-client-source,x-operator"
                ),
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("POST", resp.headers["access-control-allow-methods"])
        allowed_headers = resp.headers["access-control-allow-headers"]
        self.assertIn("X-API-Key", allowed_headers)
        self.assertIn("X-Client-Source", allowed_headers)
        self.assertIn("X-Operator", allowed_headers)

    def test_security_headers_are_set(self):
        from app.main import app

        resp = TestClient(app).get("/api")

        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", resp.headers["Content-Security-Policy"])
        self.assertIn("max-age=31536000", resp.headers["Strict-Transport-Security"])

    def test_csp_hardens_operator_key_xss_surface(self):
        # P-9 mitigation: the operator API key lives in sessionStorage and an
        # XSS could try to exfiltrate it. CSP must (a) block fetch() to
        # attacker origins via connect-src 'self', (b) block <form> POST to
        # attacker origins via form-action 'self', (c) block any iframe the
        # app might be tricked into embedding via frame-src 'none', and (d)
        # not allow arbitrary external image loads (img-src must NOT include
        # the bare 'https:' wildcard). script-src retains 'unsafe-inline'
        # because the Next.js static export emits inline hydration scripts.
        from app.main import app

        resp = TestClient(app).get("/api")
        csp = resp.headers["Content-Security-Policy"]

        self.assertIn("connect-src 'self'", csp)
        self.assertIn("form-action 'self'", csp)
        self.assertIn("frame-src 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        # img-src must NOT include the bare 'https:' wildcard (was too
        # permissive; the app loads no external images).
        self.assertIn("img-src 'self' data:", csp)
        self.assertNotIn("img-src 'self' data: https:", csp)
        # script-src retains 'unsafe-inline' for Next.js static export.
        self.assertIn("script-src 'self' 'unsafe-inline'", csp)

    def test_api_health_ok_returns_200_when_healthy(self):
        from app.main import app

        client = TestClient(app)
        with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("scheduler_running", body)
        self.assertIn("loop", body)

    def test_api_health_requests_sanitized_status_by_default(self):
        from app.main import app

        client = TestClient(app)
        with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ) as status_mock:
            client.get("/api/health")

        status_mock.assert_called_once_with(
            scheduler_running=True,
            include_run_details=False,
        )

    def test_api_health_allows_run_details_with_write_key(self):
        from app.main import app

        client = TestClient(app)
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ) as status_mock:
            client.get("/api/health", headers={"X-API-Key": "secret"})

        status_mock.assert_called_once_with(
            scheduler_running=True,
            include_run_details=True,
        )

    def test_api_health_returns_503_when_degraded(self):
        from app.main import app

        client = TestClient(app)
        # A failed scheduled run must surface as 503 so container/systemd
        # healthchecks and uptime monitors trip instead of seeing a 200.
        with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {"event_discover": {"status": "failed"}}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "degraded")

    def test_api_health_returns_503_when_scheduler_stopped(self):
        from app.main import app

        client = TestClient(app)
        with patch("app.core.scheduler.scheduler", _FakeScheduler(False)), \
                patch(
                    "app.core.scheduler.scheduler_start_skipped_due_to_lock",
                    return_value=False,
                ), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 503)

    def test_api_health_ok_when_scheduler_explicitly_disabled(self):
        from app.main import app

        client = TestClient(app)
        with patch.object(settings, "SCHEDULER_ENABLED", False), \
                patch("app.core.scheduler.scheduler", _FakeScheduler(False)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["scheduler_enabled"])

    def test_api_health_ok_when_scheduler_lock_owned_elsewhere(self):
        from app.main import app

        client = TestClient(app)
        with patch.object(settings, "SCHEDULER_ENABLED", True), \
                patch("app.core.scheduler.scheduler", _FakeScheduler(False)), \
                patch(
                    "app.core.scheduler.scheduler_start_skipped_due_to_lock",
                    return_value=True,
                ), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["scheduler_lock_skipped"])


class HealthcheckScriptTests(unittest.TestCase):
    def _run_healthcheck(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return healthcheck.run_healthcheck(*args, **kwargs)

    def test_healthcheck_pings_deadman_after_local_health_is_ok(self):
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            if url == "http://local/api/health":
                return 200, b'{"status":"ok"}'
            return 200, b"ok"

        code = self._run_healthcheck(
            {
                "PMRF_HEALTHCHECK_URL": "http://local/api/health",
                "PMRF_DEADMAN_URL": "https://deadman.example/ping",
                "PMRF_HEALTHCHECK_TIMEOUT_SECONDS": "2",
            },
            fetch=fetch,
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [
                ("http://local/api/health", 2.0),
                ("https://deadman.example/ping", 2.0),
            ],
        )

    def test_healthcheck_does_not_ping_deadman_when_health_is_degraded(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return 200, b'{"status":"degraded"}'

        code = self._run_healthcheck(
            {
                "PMRF_HEALTHCHECK_URL": "http://local/api/health",
                "PMRF_DEADMAN_URL": "https://deadman.example/ping",
            },
            fetch=fetch,
        )

        self.assertEqual(code, 1)
        self.assertEqual(calls, ["http://local/api/health"])

    def test_healthcheck_skips_deadman_when_not_configured(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return 200, b'{"status":"ok"}'

        code = self._run_healthcheck(
            {"PMRF_HEALTHCHECK_URL": "http://local/api/health"},
            fetch=fetch,
        )

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["http://local/api/health"])


@contextlib.contextmanager
def _state_stores_in(root: Path, *, create: bool = True):
    """Redirect **every** declared state store into `root`, optionally creating them.

    The previous version of these tests patched the four settings it knew about.
    The other four kept pointing at the operator's real files, so a test whose
    job was to verify backup coverage archived the live 6 MB
    `kernel_predictions.db` into a temp directory. An incomplete redirect is not
    just non-hermetic, it silently reads production data.

    Yields the mapping of setting name -> path so a caller can assert per store.
    """
    paths: dict[str, Path] = {}
    with contextlib.ExitStack() as stack:
        for name in runtime_stores.state_setting_names():
            target = root / Path(getattr(settings, name)).name
            paths[name] = target
            stack.enter_context(patch.object(settings, name, str(target)))
        if create:
            for target in paths.values():
                target.write_text("x", encoding="utf-8")
        yield paths


class BackupTests(unittest.TestCase):
    def test_backup_archives_every_declared_state_store(self):
        """Exact set equality, not membership.

        The original assertion was three `assertIn` calls, which is why four
        omitted stores could never redden it: a subset assertion cannot express
        "and nothing is missing". See app/core/runtime_stores.py.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _state_stores_in(base) as paths:
                archive = backup_stores.create_backup(str(base / "backups"))
                self.assertTrue(archive.exists())
                names = set(zipfile.ZipFile(archive).namelist())

        expected = {p.name for p in paths.values()}
        self.assertEqual(
            names,
            expected,
            f"archive contents differ from the declared state stores; "
            f"missing={sorted(expected - names)} unexpected={sorted(names - expected)}",
        )

    def test_backup_includes_the_four_stores_it_used_to_omit(self):
        """Name the regression explicitly, so a reclassification cannot hide it."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _state_stores_in(base):
                archive = backup_stores.create_backup(str(base / "backups"))
                names = set(zipfile.ZipFile(archive).namelist())

        for basename in (
            "kernel_predictions.db",
            "world_cup_predictions.db",
            "domain_reliability.db",
            "sports_facts.json",
        ):
            with self.subTest(store=basename):
                self.assertIn(basename, names)

    def test_backup_includes_sidecars_for_every_sqlite_store(self):
        """WAL/SHM travel with their own store, not only the loop DB's.

        A `.db` copied without its `-wal` can be missing committed transactions,
        so this is data loss, not tidiness.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _state_stores_in(base) as paths:
                dbs = [p for p in paths.values() if p.suffix == ".db"]
                self.assertGreaterEqual(len(dbs), 4)
                for db in dbs:
                    for suffix in runtime_stores.SQLITE_SIDECAR_SUFFIXES:
                        Path(str(db) + suffix).write_text("s", encoding="utf-8")
                archive = backup_stores.create_backup(str(base / "backups"))
                names = set(zipfile.ZipFile(archive).namelist())

        for db in dbs:
            for suffix in runtime_stores.SQLITE_SIDECAR_SUFFIXES:
                with self.subTest(sidecar=db.name + suffix):
                    self.assertIn(db.name + suffix, names)

    def test_backup_skips_stores_that_do_not_exist(self):
        """A store the operator never populated is not an error."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _state_stores_in(base, create=False) as paths:
                only = paths["EVENT_STORE_FILE"]
                only.write_text("{}", encoding="utf-8")
                archive = backup_stores.create_backup(str(base / "backups"))
                names = set(zipfile.ZipFile(archive).namelist())

        self.assertEqual(names, {"event_store.json"})

    def test_backup_excludes_ephemeral_and_derived_stores(self):
        """The scheduler lock and the log must never enter an archive."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with _state_stores_in(base):
                archive = backup_stores.create_backup(str(base / "backups"))
                names = set(zipfile.ZipFile(archive).namelist())

        for setting in (*runtime_stores.EPHEMERAL_STORES, *runtime_stores.DERIVED_STORES):
            value = getattr(settings, setting, "")
            if not value:
                continue
            with self.subTest(setting=setting):
                self.assertNotIn(Path(value).name, names)

    def test_backup_refuses_colliding_archive_member_names(self):
        """Two stores sharing a basename would make a restore ambiguous."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            clash = base / "other" / "event_store.json"
            clash.parent.mkdir(parents=True)
            with _state_stores_in(base):
                clash.write_text("{}", encoding="utf-8")
                with patch.object(settings, "SPORTS_FACT_FILE", str(clash)):
                    with self.assertRaises(ValueError) as ctx:
                        backup_stores.create_backup(str(base / "backups"))
        self.assertIn("event_store.json", str(ctx.exception))

    def test_backup_prunes_old_archives_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            backups.mkdir()
            event_store = base / "event_store.json"
            event_store.write_text("{}", encoding="utf-8")
            old_names = [
                "pmrf-backup-20260101-000000Z.zip",
                "pmrf-backup-20260102-000000Z.zip",
                "pmrf-backup-20260103-000000Z.zip",
            ]
            for name in old_names:
                (backups / name).write_text("old", encoding="utf-8")
            (backups / "manual-note.txt").write_text("keep me", encoding="utf-8")

            with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
                    patch.object(settings, "EVENT_AUDIT_FILE", str(base / "missing.jsonl")), \
                    patch.object(settings, "EVENT_CACHE_FILE", str(base / "missing.json")), \
                    patch.object(settings, "LOOP_DB_FILE", str(base / "missing.db")):
                archive = backup_stores.create_backup(str(backups), keep=2)

            remaining = {path.name for path in backups.iterdir()}

        self.assertIn(archive.name, remaining)
        self.assertIn("pmrf-backup-20260103-000000Z.zip", remaining)
        self.assertNotIn("pmrf-backup-20260101-000000Z.zip", remaining)
        self.assertNotIn("pmrf-backup-20260102-000000Z.zip", remaining)
        self.assertIn("manual-note.txt", remaining)

    def test_backup_encrypted_when_key_provided(self):
        import zipfile

        try:
            import pyzipper  # noqa: F401
        except ImportError:
            self.skipTest("pyzipper not installed")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            event_store.write_text('{"secret": 1}', encoding="utf-8")

            with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
                    patch.object(settings, "EVENT_AUDIT_FILE", str(base / "missing.jsonl")), \
                    patch.object(settings, "EVENT_CACHE_FILE", str(base / "missing.json")), \
                    patch.object(settings, "LOOP_DB_FILE", str(base / "missing.db")):
                archive = backup_stores.create_backup(
                    str(base / "backups"),
                    encryption_key="hunter2",
                )

            self.assertTrue(archive.exists())

            # The encrypted archive cannot be opened as a plain zip (bad password
            # / unsupported compression), proving it is actually encrypted.
            with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                with zipfile.ZipFile(archive) as zf:
                    zf.read("event_store.json")

            # pyzipper with the right key reads the contents back.
            import pyzipper
            with pyzipper.AESZipFile(archive) as zf:
                zf.setpassword(b"hunter2")
                content = zf.read("event_store.json")
            self.assertEqual(content.decode("utf-8"), '{"secret": 1}')

    def test_backup_plaintext_when_key_empty(self):
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            event_store.write_text("{}", encoding="utf-8")

            with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
                    patch.object(settings, "EVENT_AUDIT_FILE", str(base / "missing.jsonl")), \
                    patch.object(settings, "EVENT_CACHE_FILE", str(base / "missing.json")), \
                    patch.object(settings, "LOOP_DB_FILE", str(base / "missing.db")), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
                archive = backup_stores.create_backup(str(base / "backups"))

            self.assertTrue(archive.exists())
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            self.assertIn("event_store.json", names)

    def test_backup_falls_back_to_configured_setting_key(self):
        import zipfile

        try:
            import pyzipper  # noqa: F401
        except ImportError:
            self.skipTest("pyzipper not installed")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            event_store.write_text("data", encoding="utf-8")

            with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
                    patch.object(settings, "EVENT_AUDIT_FILE", str(base / "missing.jsonl")), \
                    patch.object(settings, "EVENT_CACHE_FILE", str(base / "missing.json")), \
                    patch.object(settings, "LOOP_DB_FILE", str(base / "missing.db")), \
                    patch.object(settings, "BACKUP_ENCRYPTION_KEY", "from-settings"):
                archive = backup_stores.create_backup(str(base / "backups"))

            # Encrypted with the configured key, not plaintext.
            with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                with zipfile.ZipFile(archive) as zf:
                    zf.read("event_store.json")

            import pyzipper
            with pyzipper.AESZipFile(archive) as zf:
                zf.setpassword(b"from-settings")
                content = zf.read("event_store.json")
            self.assertEqual(content.decode("utf-8"), "data")


if __name__ == "__main__":
    unittest.main()
