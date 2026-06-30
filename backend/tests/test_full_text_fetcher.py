"""Tests for app.utils.full_text_fetcher.

Verifies fail-closed behaviour (never raises), the 8000-char cap, and that
httpx + trafilatura are correctly mocked.
"""
import asyncio

import httpx

from app.utils import full_text_fetcher
from app.utils.full_text_fetcher import fetch_full_text


def test_fetch_full_text_returns_none_for_empty_url():
    result = asyncio.run(fetch_full_text(""))
    assert result is None


def test_fetch_full_text_returns_none_on_network_error(monkeypatch):
    """Network failures return None, never raise. Mock must actually intercept."""
    calls = {"count": 0}

    async def mock_get(self, url, **kwargs):
        calls["count"] += 1
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = asyncio.run(
        fetch_full_text("https://nonexistent.example.com/article")
    )
    assert result is None
    # Verify the mock actually intercepted the call (no real network attempt).
    assert calls["count"] == 1


def test_fetch_full_text_returns_extracted_text(monkeypatch):
    """Successful fetch + extraction returns the extracted text."""

    class _FakeResponse:
        text = "<html><body><p>hello</p></body></html>"

        def raise_for_status(self):
            pass

    async def mock_get(self, url, **kwargs):
        return _FakeResponse()

    def mock_extract(html):
        return "Extracted article body"

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(full_text_fetcher.trafilatura, "extract", mock_extract)

    result = asyncio.run(fetch_full_text("https://example.test/article"))
    assert result == "Extracted article body"


def test_fetch_full_text_truncates_to_8000_chars(monkeypatch):
    """Text longer than 8000 chars is truncated to limit LLM cost."""

    class _FakeResponse:
        text = "<html><body><p>long</p></body></html>"

        def raise_for_status(self):
            pass

    async def mock_get(self, url, **kwargs):
        return _FakeResponse()

    long_text = "a" * 10000

    def mock_extract(html):
        return long_text

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(full_text_fetcher.trafilatura, "extract", mock_extract)

    result = asyncio.run(fetch_full_text("https://example.test/long"))
    assert result is not None
    assert len(result) == 8000
