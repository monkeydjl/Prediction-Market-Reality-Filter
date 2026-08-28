"""Tests for the daily LLM cost cap (P0 spend guardrail).

Covers the store (day-keyed accumulation) and the gateway enforcement points
(``_complete`` for chat/json and ``complete_embeddings``), including the two
properties that make the feature safe to ship:

- cap disabled (the default, 0) never touches SQLite and never changes behavior
- a storage failure fails OPEN, so a broken loop DB cannot brick every LLM path
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import llm_daily_spend_store as store
from app.services import llm_gateway_service as gateway
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(
        sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")
    )


class _FakeCompletions:
    def __init__(self, create):
        self.create = AsyncMock(side_effect=create)


class _FakeChat:
    def __init__(self, create):
        self.completions = _FakeCompletions(create)


class _FakeClient:
    def __init__(self, create):
        self.chat = _FakeChat(create)
        self.embeddings = _FakeCompletions(create)


def _fake_response(content="{}", total_tokens=1000):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=total_tokens,
            completion_tokens=0,
            total_tokens=total_tokens,
        ),
    )


def _fake_embedding_response(vectors, total_tokens=1000):
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=v) for v in vectors],
        usage=SimpleNamespace(
            prompt_tokens=total_tokens, completion_tokens=0, total_tokens=total_tokens
        ),
    )


_ROUTE = [gateway.LLMModelRoute("p1", ["gpt-4o-mini"])]
_CONFIGS = {"p1": gateway.LLMProviderConfig("p1", "key", "http://example")}


class DailySpendStoreTests(unittest.TestCase):
    def test_spend_starts_at_zero_and_accumulates(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertEqual(store.get_spend_today(), 0.0)
            store.add_spend(1.25)
            store.add_spend(0.75)
            self.assertAlmostEqual(store.get_spend_today(), 2.0)

    def test_non_positive_amounts_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            store.add_spend(0.0)
            store.add_spend(-5.0)
            self.assertEqual(store.get_spend_today(), 0.0)


class CostCapEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, create):
        return await gateway.complete_json(
            task="default",
            messages=[{"role": "user", "content": "x"}],
            route=_ROUTE,
            provider_configs=_CONFIGS,
            client_factory=lambda provider: _FakeClient(create),
        )

    async def test_disabled_cap_never_touches_storage(self):
        """The default (0) must be a pure no-op: no DB access, no behavior change."""
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["model"])
            return _fake_response()

        with patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 0.0), \
                patch.object(sqlite_db, "loop_db_path",
                             side_effect=AssertionError("storage must not be touched")):
            result = await self._call(create)

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["gpt-4o-mini"])

    async def test_call_refused_once_spend_reaches_cap(self):
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["model"])
            return _fake_response()

        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 1.0):
            store.add_spend(1.0)
            result = await self._call(create)

        self.assertFalse(result.ok)
        self.assertEqual(result.degraded_reason, "daily_cost_cap_exceeded")
        self.assertEqual(calls, [], "provider must not be called once capped")
        self.assertEqual([a.status for a in result.attempts], ["skipped"])
        self.assertEqual(result.attempts[0].error_type, "daily_cost_cap_exceeded")

    async def test_call_allowed_while_under_cap_and_records_spend(self):
        async def create(**kwargs):
            # gpt-4o-mini is $0.00015/1K -> 1000 tokens == $0.00015
            return _fake_response(total_tokens=1000)

        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 1.0):
            result = await self._call(create)
            spend = store.get_spend_today()

        self.assertTrue(result.ok)
        self.assertAlmostEqual(spend, 0.00015)

    async def test_embeddings_are_capped_too(self):
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["model"])
            return _fake_embedding_response([[1.0, 0.0]])

        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 1.0):
            store.add_spend(2.0)
            result = await gateway.complete_embeddings(
                input=["query"],
                route=_ROUTE,
                provider_configs=_CONFIGS,
                client_factory=lambda provider: _FakeClient(create),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.degraded_reason, "daily_cost_cap_exceeded")
        self.assertEqual(calls, [])

    async def test_storage_failure_fails_open(self):
        """A broken counter must not block LLM calls — the cap is a guard, not a lock."""
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["model"])
            return _fake_response()

        with patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 1.0), \
                patch.object(store, "get_spend_today",
                             side_effect=RuntimeError("disk gone")):
            result = await self._call(create)

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["gpt-4o-mini"])


class CapAndTelemetryAgreementTests(unittest.IsolatedAsyncioTestCase):
    """The counter that enforces the cap and the cost an operator can see must
    agree about the same call.

    ``_record_usage`` prices by ``LLMResult.model`` -- the model the route walk
    actually reached -- while the telemetry block was handed
    ``settings.OPENAI_MODEL``, the legacy last-resort name that ``.env.example``
    tells operators to comment out. On the shipped production template both
    readings are live (``LLM_TELEMETRY_ENABLED=true``, cap 25), and a
    gpt-4-served call was charged $3.00 against the cap while reporting $0.014
    under a ``deepseek-chat`` label: a 214x understatement.

    This runs the whole chain once -- real gateway call, real ``_ask_ai``, real
    ``analyze_market``, real telemetry builder -- so no hand-written
    intermediate dict can hide a break in it. All three readings of the same
    call are compared: the cap counter, the per-event ``estimated_token_cost``,
    and the ``/metrics`` series.
    """

    async def test_the_cap_charge_and_the_visible_cost_agree(self):
        import app.services.ai_analysis_service as ai
        from app.services.llm_telemetry_service import build_llm_telemetry
        from app.utils.metrics import LLM_TOKEN_COST

        served, configured = "gpt-4", "deepseek-chat"
        self.assertNotEqual(served, configured, "the two models must differ or "
                                               "the test cannot see the defect")

        def _metric_cost(model: str) -> float:
            return sum(
                sample.value
                for metric in LLM_TOKEN_COST.collect()
                for sample in metric.samples
                if sample.name.endswith("_total")
                and sample.labels.get("model") == model
            )

        async def create(**kwargs):
            return _fake_response(
                content='{"ai_probability": 61, "narrative_type": "factual", '
                        '"reasoning": "结构化证据支持。"}',
                total_tokens=100_000,
            )

        before_metric_served = _metric_cost(served)
        before_metric_configured = _metric_cost(configured)
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 25.0), \
                patch.object(gateway, "build_route",
                             return_value=[gateway.LLMModelRoute("p1", [served])]), \
                patch.object(gateway, "_provider_configs", return_value=_CONFIGS), \
                patch.object(gateway, "_default_client_factory",
                             new=lambda config: _FakeClient(create)), \
                patch.object(ai, "translate_title", new=AsyncMock(return_value="")):
            before = store.get_spend_today()
            analysis = await ai.analyze_market(
                market_question="Will the agency approve the policy?",
                market_probability=50,
                news_context="Reuters reports an official filing.",
            )
            charged = store.get_spend_today() - before

        self.assertEqual(analysis["llm_model"], served)
        block = build_llm_telemetry(
            analysis=analysis,
            sentiment_profile=None,
            news_context="Reuters reports an official filing.",
            model=configured,
            enabled=True,
        )
        # 100_000 tokens of gpt-4 at $0.03/1K.
        self.assertAlmostEqual(charged, 3.0, places=6)
        self.assertAlmostEqual(block["estimated_token_cost"], charged, places=6)
        self.assertEqual(block["model"], served)
        # The /metrics series is the third reading of the same call, and it is
        # incremented at the gateway rather than by the telemetry block -- so
        # this also pins that the enrichment path is counted exactly once.
        self.assertAlmostEqual(
            _metric_cost(served) - before_metric_served, charged, places=6
        )
        self.assertAlmostEqual(
            _metric_cost(configured) - before_metric_configured, 0.0, places=9
        )


class SchemaMemoizationTests(unittest.TestCase):
    """_ensure_schema must run once per DB path, not once per call.

    Every other store in app/memory/ memoizes this behind a double-checked
    lock; this one did not, so each get_spend_today() took a *write*
    transaction (CREATE TABLE + migrations + version record) before its
    SELECT — serializing every LLM call behind the global write lock.
    """

    def test_schema_is_not_rebuilt_on_every_access(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            store._INITIALIZED.clear()

            writes = []
            real_writing = sqlite_db.writing

            def _counting_writing(path):
                writes.append(path)
                return real_writing(path)

            with patch.object(store, "writing", _counting_writing):
                store.get_spend_today()
                store.get_spend_today()
                store.get_spend_today()

            # One schema build, and reads take no write transaction at all.
            self.assertEqual(
                len(writes),
                1,
                f"3 reads opened {len(writes)} write transactions; schema setup "
                "must be memoized per path",
            )

    def test_spend_still_accumulates_across_a_fresh_path(self):
        """Memoization is per path — a new DB must still get its schema."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            store._INITIALIZED.clear()
            store.add_spend(0.5)
            self.assertAlmostEqual(store.get_spend_today(), 0.5)


class CostCapOffloadTests(unittest.IsolatedAsyncioTestCase):
    """The cap's SQLite work must not run on the event loop.

    _cost_cap_exceeded() reads and _record_usage() writes, and both were called
    synchronously from ``async def``. Under the write lock a single slow call
    froze every other coroutine in the process.
    """

    async def test_cap_check_does_not_starve_the_event_loop(self):
        import asyncio
        import time

        ticks = 0

        async def _heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        async def create(**kwargs):
            return _fake_response()

        beat = asyncio.create_task(_heartbeat())
        with patch.object(gateway.settings, "LLM_DAILY_COST_CAP_USD", 1.0),                 patch.object(gateway, "_cost_cap_exceeded",
                             side_effect=lambda: time.sleep(0.25) or (False, 0.0, 1.0)):
            result = await gateway.complete_json(
                task="default",
                messages=[{"role": "user", "content": "x"}],
                route=_ROUTE,
                provider_configs=_CONFIGS,
                client_factory=lambda provider: _FakeClient(create),
            )
        beat.cancel()

        self.assertTrue(result.ok)
        self.assertGreater(
            ticks,
            15,
            f"the cap check blocked the event loop: the heartbeat only got "
            f"{ticks} ticks during a 0.25s storage read",
        )


if __name__ == "__main__":
    unittest.main()
