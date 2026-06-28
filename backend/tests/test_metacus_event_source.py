"""
Unit tests for the Metaculus event-source adapter.

Network-free: ``_fetch_raw_posts`` (the httpx seam) is mocked, mirroring how
``test_manifold_event_source`` mocks ``_fetch_raw_markets``. Covers
normalization to the shared candidate-event shape, the binary-only eligibility
filter, the no-token / no-URL off switches, and graceful failure (fetch
errors -> empty list).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import metacus_event_source as source


def _post(**overrides) -> dict:
    post = {
        "id": 12345,
        "title": "Will AGI arrive by 2030?",
        "url": "https://www.metaculus.com/questions/12345/agi-2030/",
        "question": {
            "question_type": "binary",
            "resolution": None,
        },
        "community_prediction": {
            "probability_yes": 0.42,
        },
        "user_counts": {
            "forecasters": 312,
        },
    }
    post.update(overrides)
    return post


class MetaculusEventSourceTests(unittest.TestCase):
    def test_fetch_candidate_events_normalizes(self):
        with patch.object(source, "_fetch_raw_posts",
                          new=AsyncMock(return_value=[_post()])), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [{
            "question": "Will AGI arrive by 2030?",
            "baseline_probability": 42.0,
            "volume": 312.0,
            "liquidity": 0.0,
            "source": {
                "type": "prediction_question",
                "platform": "Metaculus",
                "source_id": "12345",
                "question": "Will AGI arrive by 2030?",
                "baseline_probability": 42.0,
                "liquidity": 0.0,
                "volume": 312.0,
                "url": "https://www.metaculus.com/questions/12345/agi-2030/",
            },
        }])

    def test_ineligible_posts_are_filtered_out(self):
        raw = [
            _post(id=1),  # eligible binary
            _post(id=2, question={"question_type": "numeric", "resolution": None}),
            _post(id=3, question={"question_type": "date", "resolution": None}),
            _post(id=4, question={"question_type": "multiple_choice", "resolution": None}),
            _post(id=5, title="   "),  # blank title
            _post(id=6, question={"question_type": "binary", "resolution": "yes"}),  # resolved
            _post(id=7, community_prediction={}),  # no probability
        ]
        with patch.object(source, "_fetch_raw_posts", new=AsyncMock(return_value=raw)), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["1"])

    def test_missing_optional_fields_default_safely(self):
        # No url, no user_counts, no community_prediction. A post with no
        # probability signal is filtered out (eligible=False), so the result
        # is empty rather than a candidate with a fabricated 50% baseline.
        raw = [{
            "id": 99,
            "title": "Bare post",
            "question": {"question_type": "binary", "resolution": None},
        }]
        with patch.object(source, "_fetch_raw_posts", new=AsyncMock(return_value=raw)), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])

    def test_falls_back_to_forecasts_record(self):
        # No community_prediction, but a forecasts[] with a latest record.
        raw = [{
            "id": 7,
            "title": "Fallback forecasts",
            "question": {"question_type": "binary", "resolution": None},
            "forecasts": [
                {"probability_yes": 0.10},
                {"probability_yes": 0.65},  # latest (most recent)
            ],
        }]
        with patch.object(source, "_fetch_raw_posts", new=AsyncMock(return_value=raw)), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["baseline_probability"], 65.0)

    def test_url_synthesized_from_id_when_missing(self):
        raw = [{
            "id": 42,
            "title": "No URL",
            "question": {"question_type": "binary", "resolution": None},
            "community_prediction": {"probability_yes": 0.5},
        }]
        with patch.object(source, "_fetch_raw_posts", new=AsyncMock(return_value=raw)), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(
            events[0]["source"]["url"],
            "https://www.metaculus.com/questions/42/",
        )

    def test_probability_clamped_to_0_100(self):
        raw = [{
            "id": 8,
            "title": "Out of range",
            "question": {"question_type": "binary", "resolution": None},
            "community_prediction": {"probability_yes": 1.5},  # >1.0, clamp to 100
        }]
        with patch.object(source, "_fetch_raw_posts", new=AsyncMock(return_value=raw)), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["baseline_probability"], 100.0)

    def test_no_token_returns_empty(self):
        # Empty token is the auto-off switch; no network call is made.
        with patch.object(source.settings, "METACULUS_API_TOKEN", ""), \
             patch.object(source, "_fetch_raw_posts",
                          new=AsyncMock(side_effect=AssertionError("should not fetch"))) as mocked:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        mocked.assert_not_called()

    def test_no_url_returns_empty(self):
        # Token set but URL empty -> disabled.
        with patch.object(source.settings, "METACULUS_API_TOKEN", "tok"), \
             patch.object(source.settings, "METACULUS_API_URL", ""):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source, "_fetch_raw_posts",
                          new=AsyncMock(side_effect=RuntimeError("network down"))), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"), \
             self.assertLogs("app.services.metacus_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        text = "\n".join(logs.output)
        self.assertIn("source=metaculus_candidates", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_limit_caps_returned_candidates(self):
        # 3 eligible but limit=2 -> only 2 returned.
        raw = [
            _post(id=1, title="Q1"),
            _post(id=2, title="Q2"),
            _post(id=3, title="Q3"),
        ]
        with patch.object(source, "_fetch_raw_posts", new=AsyncMock(return_value=raw)), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=2))
        self.assertEqual(len(events), 2)

    def test_response_with_results_key(self):
        # Standard Metaculus paginated envelope.
        with patch.object(source, "_fetch_raw_posts",
                          new=AsyncMock(return_value=[_post()])), \
             patch.object(source.settings, "METACULUS_API_TOKEN", "tok"):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        # _fetch_raw_posts already unwraps results; we just verify the
        # adapter consumes a list of posts correctly.
        self.assertEqual(len(events), 1)


class MetaculusFetchRawTests(unittest.TestCase):
    """Direct tests for the httpx seam: header injection and response parsing."""

    def test_authorization_header_included(self):
        with patch.object(source.settings, "METACULUS_API_TOKEN", "my-token"), \
             patch.object(source.settings, "METACULUS_API_URL",
                          "https://www.metaculus.com/api2/posts/"):
            captured: dict = {}

            class _FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"results": [_post()]}

            class _FakeClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, params=None, headers=None):
                    captured["url"] = url
                    captured["params"] = params
                    captured["headers"] = headers
                    return _FakeResp()

            with patch.object(source.httpx, "AsyncClient", return_value=_FakeClient()):
                result = asyncio.run(source._fetch_raw_posts(limit=5))
        self.assertEqual(len(result), 1)
        self.assertEqual(captured["headers"]["Authorization"], "Token my-token")
        self.assertEqual(captured["params"]["status"], "open")
        self.assertEqual(captured["params"]["type"], "forecast")

    def test_results_envelope_unwrapped(self):
        with patch.object(source.settings, "METACULUS_API_TOKEN", "tok"), \
             patch.object(source.settings, "METACULUS_API_URL", "https://x/api2/posts/"):
            class _FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"results": [{"id": 1}, {"id": 2}], "count": 2, "next": None}

            class _FakeClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, params=None, headers=None):
                    return _FakeResp()

            with patch.object(source.httpx, "AsyncClient", return_value=_FakeClient()):
                result = asyncio.run(source._fetch_raw_posts(limit=5))
        self.assertEqual(len(result), 2)

    def test_bare_list_response_tolerated(self):
        with patch.object(source.settings, "METACULUS_API_TOKEN", "tok"), \
             patch.object(source.settings, "METACULUS_API_URL", "https://x/api2/posts/"):
            class _FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return [{"id": 1}, {"id": 2}]  # bare list, no envelope

            class _FakeClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, params=None, headers=None):
                    return _FakeResp()

            with patch.object(source.httpx, "AsyncClient", return_value=_FakeClient()):
                result = asyncio.run(source._fetch_raw_posts(limit=5))
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
