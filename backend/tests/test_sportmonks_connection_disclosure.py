"""Sportmonks connection/validate diagnostics must not leak raw exceptions.

``test_world_cup_sportmonks_connection()`` and
``validate_world_cup_sportmonks_pipeline()`` can raise from ``urlopen`` with
sensitive details (upstream URLs, authorization tokens in tracebacks, provider
error JSON, network stack internals), then format those exceptions into the
returned diagnostic dictionaries using ``str(exc)`` or f-string interpolation.
Both functions are exposed through authenticated POST routes that return the
diagnostic dict as JSON, making it a production HTTP response boundary.

The existing ``test_fails_on_fixture_fetch_error`` proves the
``validate_world_cup_sportmonks_pipeline`` path can capture an ``HTTPError``
and return ``{"error": f"Fixture fetch failed: {exc}"}``, but it does not
assert that the response hides the raw exception class/message/URL. These
regressions prove the current implementation leaks and the safe contract
preserves diagnostic semantics while removing unstable exception text.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


class SportmonksConnectionDisclosureTests(unittest.TestCase):
    """Authenticated connection/validate routes must return safe diagnostics."""

    def setUp(self):
        self.client = TestClient(app)
        self.api_key = "test-write-key-sportmonks-12345"
        self.headers = {"X-API-Key": self.api_key}

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-sportmonks-12345")
    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.test/v3/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "secret-token-67890")
    def test_connection_test_hides_raw_urlerror(self, mock_urlopen):
        """URLError from connection test must not reach HTTP response."""
        sensitive = (
            "nodename nor servname provided, or not known "
            "api.sportmonks.test:443 secret-token-67890"
        )
        mock_urlopen.side_effect = URLError(sensitive)

        response = self.client.post(
            "/api/events/sports/world-cup/data/bundle/sportmonks/test",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("error", body)
        self.assertNotIn("nodename nor servname", response.text)
        self.assertNotIn("api.sportmonks.test", response.text)
        self.assertNotIn("secret-token-67890", response.text)
        self.assertNotIn("URLError", response.text)

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-sportmonks-12345")
    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.test/v3/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "secret-token-67890")
    def test_connection_test_hides_raw_httperror(self, mock_urlopen):
        """HTTPError from connection test must not reach HTTP response."""
        mock_urlopen.side_effect = HTTPError(
            "https://api.sportmonks.test/v3/fixtures?api_token=secret-token-67890",
            503,
            "Service Unavailable",
            {},
            None,
        )

        response = self.client.post(
            "/api/events/sports/world-cup/data/bundle/sportmonks/test",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "HTTP 503")
        self.assertNotIn("api.sportmonks.test", response.text)
        self.assertNotIn("secret-token-67890", response.text)
        self.assertNotIn("Service Unavailable", response.text)

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-sportmonks-12345")
    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.test/v3/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "secret-token-67890")
    def test_connection_test_hides_provider_errors(self, mock_urlopen):
        """Raw provider error JSON must not reach HTTP response."""
        sensitive_error = {
            "message": "Invalid API token",
            "token": "secret-token-67890",
            "endpoint": "/v3/fixtures",
        }
        mock_urlopen.return_value.__enter__ = lambda _self: mock_urlopen.return_value
        mock_urlopen.return_value.__exit__ = lambda _self, *_args: None
        mock_urlopen.return_value.read.return_value = json.dumps({
            "errors": [sensitive_error],
        }).encode("utf-8")

        response = self.client.post(
            "/api/events/sports/world-cup/data/bundle/sportmonks/test",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("error", body)
        self.assertNotIn("Invalid API token", response.text)
        self.assertNotIn("secret-token-67890", response.text)
        self.assertNotIn("/v3/fixtures", response.text)

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-sportmonks-12345")
    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.test/v3/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "secret-token-67890")
    def test_validate_hides_fixture_fetch_exception(self, mock_urlopen):
        """Exception from fixture fetch during validate must not reach HTTP response."""
        call_count = [0]

        def side_effect(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                ctx = type("_ctx", (), {
                    "read": lambda _self, _size: json.dumps({"data": []}).encode("utf-8"),
                    "__enter__": lambda _self: ctx,
                    "__exit__": lambda _self, *_args: None,
                })()
                return ctx
            raise HTTPError(
                "https://api.sportmonks.test/v3/fixtures?api_token=secret-token-67890",
                500,
                "Internal Server Error: database connection failed at host db.internal.sportmonks.net",
                {},
                None,
            )

        mock_urlopen.side_effect = side_effect

        with tempfile.TemporaryDirectory() as tmp:
            facts_file = Path(tmp) / "sports_facts.json"
            facts_file.write_text("[]", encoding="utf-8")
            with patch.object(settings, "SPORTS_FACT_FILE", str(facts_file)):
                response = self.client.post(
                    "/api/events/sports/world-cup/data/bundle/sportmonks/validate",
                    headers=self.headers,
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("error", body)
        self.assertNotIn("api.sportmonks.test", response.text)
        self.assertNotIn("secret-token-67890", response.text)
        self.assertNotIn("database connection failed", response.text)
        self.assertNotIn("db.internal.sportmonks.net", response.text)
        self.assertNotIn("Internal Server Error", response.text)
        self.assertNotIn("HTTPError", response.text)


if __name__ == "__main__":
    unittest.main()
