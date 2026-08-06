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


if __name__ == "__main__":
    unittest.main()
