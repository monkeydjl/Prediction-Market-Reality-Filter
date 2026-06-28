"""Tests for app.services.news_sentiment_service.

Network-free: the AsyncOpenAI client and settings are mocked so no real LLM
call is made. Verifies the fail-closed neutral fallback, prompt building
(including full_text truncation), and the happy path that parses a valid
LLM response.

Note on async pattern: the codebase uses asyncio.run() rather than
pytest-asyncio (see tests/test_full_text_fetcher.py for the established
convention). The async tests below follow the same asyncio.run() pattern.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from app.services.news_sentiment_service import (
    _build_user_prompt,
    _neutral_fallback,
    analyze_sentiment,
)


def test_neutral_fallback_structure():
    result = _neutral_fallback("test reason")
    assert result["overall_direction"] == "neutral"
    assert result["overall_strength"] == 0.0
    assert result["fallback"] is True
    assert "test reason" in result["summary"]


def test_build_user_prompt_includes_title_and_description():
    articles = [
        {"title": "Fed cuts rates", "description": "The Fed announced...", "source": "WSJ"},
    ]
    prompt = _build_user_prompt("Will Fed cut rates?", articles)
    assert "Fed cuts rates" in prompt
    assert "The Fed announced" in prompt
    assert "Will Fed cut rates?" in prompt


def test_build_user_prompt_includes_full_text_when_available():
    articles = [
        {"title": "Test", "description": "Desc", "full_text": "FULL TEXT HERE", "source": "src"},
    ]
    prompt = _build_user_prompt("Question?", articles)
    assert "FULL TEXT HERE" in prompt


def test_build_user_prompt_truncates_long_full_text():
    # Use 'z' as the filler char so the count is not polluted by the literal
    # "Full text:" label in the prompt template (which contains an 'x').
    long_text = "z" * 5000
    articles = [{"title": "T", "description": "D", "full_text": long_text, "source": "s"}]
    prompt = _build_user_prompt("Q?", articles)
    # _MAX_FULL_TEXT_CHARS = 2000
    assert prompt.count("z") <= 2000
    assert prompt.count("z") == 2000  # exactly truncated, not dropped


def test_analyze_sentiment_returns_neutral_for_empty_articles():
    result = asyncio.run(analyze_sentiment("Question?", []))
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True


def test_analyze_sentiment_returns_neutral_without_api_key(monkeypatch):
    monkeypatch.setattr("app.services.news_sentiment_service.settings.OPENAI_API_KEY", "")
    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True


def test_analyze_sentiment_returns_neutral_when_disabled(monkeypatch):
    """When NEWS_SENTIMENT_ENABLED is false, the LLM is never called and the
    neutral fallback is returned immediately (short-circuits even before the
    empty-articles check, so non-empty articles still get the fallback).
    """
    monkeypatch.setattr(
        "app.services.news_sentiment_service.settings.NEWS_SENTIMENT_ENABLED", False
    )
    # A real-looking client mock is set up so the test FAILS if the disabled
    # flag doesn't short-circuit (the LLM call would actually be attempted).
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock()
    )
    monkeypatch.setattr(
        "app.services.news_sentiment_service.AsyncOpenAI",
        MagicMock(return_value=mock_client),
    )
    monkeypatch.setattr(
        "app.services.news_sentiment_service.settings.OPENAI_API_KEY", "fake-key"
    )

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True
    assert "NEWS_SENTIMENT_ENABLED" in result["summary"]
    mock_client.chat.completions.create.assert_not_called()


def test_build_user_prompt_respects_max_articles_setting(monkeypatch):
    """The article cap is read at call time from settings.NEWS_SENTIMENT_MAX_ARTICLES,
    so a monkeypatch lowering it from the default 6 cuts the prompt short.
    """
    monkeypatch.setattr(
        "app.services.news_sentiment_service.settings.NEWS_SENTIMENT_MAX_ARTICLES", 2
    )
    articles = [
        {"title": f"title-{i}", "description": "desc", "source": "src"}
        for i in range(5)
    ]
    prompt = _build_user_prompt("Q?", articles)
    # Only the first 2 articles are included.
    assert "title-0" in prompt
    assert "title-1" in prompt
    assert "title-2" not in prompt
    assert "title-3" not in prompt
    assert "title-4" not in prompt


def test_analyze_sentiment_parses_valid_llm_response(monkeypatch):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "articles": [{
            "index": 0,
            "sentiment": "positive",
            "impact": "high",
            "key_facts": ["fact"],
            "relevance_to_question": 0.8,
        }],
        "overall_direction": "support_yes",
        "overall_strength": 0.7,
        "conflict_level": 0.1,
        "summary": "证据整体支持 YES 结果",
    })
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(
        "app.services.news_sentiment_service.AsyncOpenAI",
        MagicMock(return_value=mock_client),
    )
    monkeypatch.setattr(
        "app.services.news_sentiment_service.settings.OPENAI_API_KEY", "fake-key"
    )

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    assert result["overall_direction"] == "support_yes"
    assert result["overall_strength"] == 0.7
    assert "fallback" not in result
