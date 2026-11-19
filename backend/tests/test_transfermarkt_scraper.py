"""The Transfermarkt scrape path: what it must not do to the event loop.

§53.94 left two things open here -- the scraping posture (a spoofed browser UA,
no spacing between requests) and the ToS question code cannot answer. Both were
about a path **nothing in production calls**: ``world_cup_factor_service`` and
the football adapter import ``get_cached_market_value`` and only read the cache.
So the durable part is what is a bug regardless of who drives it:

* ``batch_scrape_world_cup_teams`` is ``async def`` and used ``time.sleep``
  between teams -- 23 consecutive blocking sleeps inside the event loop, i.e. a
  stalled process for the whole batch the moment anyone awaits it from a request
  handler. That is the defect this file pins (mutation: it must fail if the
  blocking call comes back).
* the User-Agent was an inline literal in the single request builder, which made
  a policy line look like a detail. It is a named constant now, and the request
  is asserted to carry it -- so changing the posture is a one-line edit, not a
  hunt through headers dicts.

Not covered here: the cache read path (the only production consumer) opens the
prediction DB and is exercised where that store lives.
"""
from __future__ import annotations

import asyncio
import inspect
import unittest
from pathlib import Path
from unittest import mock

from app.services import transfermarkt_scraper as scraper


class ParseMarketValueTests(unittest.TestCase):
    """The parser is the one pure function here, so it is cheap to pin."""

    def test_billions_become_millions(self):
        self.assertAlmostEqual(scraper.parse_market_value("€1.05bn"), 1050.0, places=3)

    def test_plain_millions_pass_through(self):
        self.assertAlmostEqual(scraper.parse_market_value("€850.00m"), 850.0, places=3)

    def test_thousands_are_scaled_down(self):
        self.assertAlmostEqual(scraper.parse_market_value("€2.30k"), 0.0023, places=6)

    def test_no_unit_is_read_as_millions(self):
        self.assertAlmostEqual(scraper.parse_market_value("€45.5"), 45.5, places=3)

    def test_thousands_separators_are_dropped(self):
        self.assertAlmostEqual(scraper.parse_market_value("€1,050.00m"), 1050.0, places=3)

    def test_an_unparseable_string_is_none(self):
        for bad in ("", "n/a", "€—"):
            with self.subTest(value=bad):
                self.assertIsNone(scraper.parse_market_value(bad))


class BatchSpacingTests(unittest.TestCase):
    """Spacing between teams must not be a blocking sleep.

    ``asyncio.sleep`` is asserted through the module's own ``asyncio`` name: the
    real module is replaced for the duration of the call, so the assertion sees
    exactly what the batch awaited without patching the interpreter's event loop.
    """

    def test_the_batch_awaits_the_delay_instead_of_blocking(self):
        fake_asyncio = mock.Mock()
        fake_asyncio.sleep = mock.AsyncMock()
        with mock.patch.object(
            scraper, "scrape_team_market_value", mock.AsyncMock(return_value=None)
        ), mock.patch.object(scraper, "asyncio", fake_asyncio), mock.patch(
            "time.sleep"
        ) as blocking_sleep:
            result = asyncio.run(scraper.batch_scrape_world_cup_teams(delay_seconds=1.5))

        expected_awaits = len(scraper.TRANSFERMARKT_TEAM_URLS) - 1
        self.assertEqual(fake_asyncio.sleep.await_count, expected_awaits)
        for call in fake_asyncio.sleep.await_args_list:
            self.assertEqual(call.args, (1.5,))
        blocking_sleep.assert_not_called()
        self.assertEqual(result["failed"], len(scraper.TRANSFERMARKT_TEAM_URLS))

    def test_the_module_does_not_reach_for_the_blocking_sleeper(self):
        """A source-level pin, because ``from time import sleep`` would defeat it.

        Patching ``time.sleep`` only sees the call shape the module uses today;
        a rebinding import would slip past the behavioural test above while
        being exactly the same bug.

        Anchored to lines that could be code, not to a bare substring: the
        module names the blocking call **in the comment that explains why it is
        gone**, and the first version of this test failed on exactly that
        (§53.104's lesson -- a source scan has to distinguish prose from code).
        """
        source = Path(scraper.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?m)^[ \t]*(?:time\.sleep\(|from[ \t]+time[ \t]+import\b)")

    def test_the_default_spacing_is_the_polite_one(self):
        """Dropping the default to 0 would silently remove the spacing.

        The number came from §53.94's recommendation (1-2s); it is a default, not
        a policy the test invents -- the *presence* of a non-zero default is the
        invariant.
        """
        default = inspect.signature(scraper.batch_scrape_world_cup_teams).parameters[
            "delay_seconds"
        ].default
        self.assertGreaterEqual(default, 1.0)


class RequestHeaderTests(unittest.TestCase):
    """The UA is a policy line, so the request has to carry the named constant."""

    def _scrape_with_fake_client(self):
        captured: dict[str, object] = {}

        class FakeResponse:
            text = "<html><body></body></html>"

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            def __init__(self, **kwargs):
                captured["client"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers=None):
                captured["url"] = url
                captured["headers"] = headers
                return FakeResponse()

        with mock.patch.object(scraper.httpx, "AsyncClient", FakeClient):
            result = asyncio.run(
                scraper.scrape_team_market_value("Brazil", use_cache=False)
            )
        return result, captured

    def test_the_request_presents_the_named_user_agent(self):
        result, captured = self._scrape_with_fake_client()
        # The stub page has no market value box, so the parse fails and nothing
        # is cached -- that keeps this test off the prediction database.
        self.assertIsNone(result)
        headers = captured["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["User-Agent"], scraper.BROWSER_USER_AGENT)

    def test_the_request_keeps_its_timeout_and_redirect_posture(self):
        _, captured = self._scrape_with_fake_client()
        self.assertEqual(captured["client"]["timeout"], 30.0)
        self.assertTrue(captured["client"]["follow_redirects"])

    def test_an_unknown_team_returns_none_without_a_request(self):
        with mock.patch.object(scraper.httpx, "AsyncClient") as client:
            self.assertIsNone(
                asyncio.run(
                    scraper.scrape_team_market_value("Atlantis", use_cache=False)
                )
            )
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
