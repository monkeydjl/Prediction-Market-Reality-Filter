"""Tests for app.services.news_sentiment_service.

Network-free: the LLM Gateway call and settings are mocked so no real LLM
call is made. Verifies the fail-closed neutral fallback, prompt building
(including full_text truncation), and the happy path that parses a valid
LLM response.

Note on async pattern: the codebase uses asyncio.run() rather than
pytest-asyncio (see tests/test_full_text_fetcher.py for the established
convention). The async tests below follow the same asyncio.run() pattern.
"""
import asyncio
from unittest.mock import AsyncMock

from app.services.llm_gateway_service import LLMResult
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


def test_analyze_sentiment_uses_gateway_when_legacy_key_is_empty(monkeypatch):
    gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={
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
        "summary": "证据支持 YES",
    }))
    monkeypatch.setattr("app.services.news_sentiment_service.settings.OPENAI_API_KEY", "")
    monkeypatch.setattr("app.services.news_sentiment_service.complete_json", gateway, raising=False)

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )

    assert result["overall_direction"] == "support_yes"
    assert "fallback" not in result
    gateway.assert_awaited_once()
    assert gateway.await_args.kwargs["task"] == "probability_analysis"


def test_analyze_sentiment_returns_neutral_when_disabled(monkeypatch):
    """When NEWS_SENTIMENT_ENABLED is false, the LLM is never called and the
    neutral fallback is returned immediately (short-circuits even before the
    empty-articles check, so non-empty articles still get the fallback).
    """
    monkeypatch.setattr(
        "app.services.news_sentiment_service.settings.NEWS_SENTIMENT_ENABLED", False
    )
    gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={}))
    monkeypatch.setattr("app.services.news_sentiment_service.complete_json", gateway)

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True
    assert "NEWS_SENTIMENT_ENABLED" in result["summary"]
    gateway.assert_not_awaited()


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
    gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={
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
    }))
    monkeypatch.setattr("app.services.news_sentiment_service.complete_json", gateway)

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    assert result["overall_direction"] == "support_yes"
    assert result["overall_strength"] == 0.7
    assert "fallback" not in result


def test_analyze_sentiment_falls_back_when_llm_returns_malformed_json(monkeypatch):
    """Valid JSON missing the required keys (articles / overall_direction) is
    treated as malformed and the neutral fallback is returned (with
    fallback=True)."""
    gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={"foo": "bar"}))
    monkeypatch.setattr("app.services.news_sentiment_service.complete_json", gateway)

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True
    assert "malformed LLM response" in result["summary"]


def test_system_prompt_includes_evidence_fields():
    """The system prompt must instruct the LLM to emit the new per-article
    evidence fields so aggregation has something to work with."""
    from app.services.news_sentiment_service import _SYSTEM_PROMPT
    assert "evidence_direction" in _SYSTEM_PROMPT
    assert "evidence_strength" in _SYSTEM_PROMPT
    assert "source_credibility" in _SYSTEM_PROMPT
    assert "rationale_zh" in _SYSTEM_PROMPT
    # 禁词约束 must appear so LLM avoids trading terms in rationale
    assert "long" in _SYSTEM_PROMPT.lower()
    assert "short" in _SYSTEM_PROMPT.lower()
    assert "position" in _SYSTEM_PROMPT.lower()


def test_analyze_sentiment_preserves_new_evidence_fields(monkeypatch):
    """When the LLM returns the new evidence fields per article, they are
    passed through verbatim in result['articles'][i] (the aggregation layer
    reads them later). This locks the passthrough contract."""
    gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={
        "articles": [{
            "index": 0,
            "sentiment": "positive",
            "impact": "high",
            "key_facts": ["fact"],
            "relevance_to_question": 0.8,
            "evidence_direction": "support",
            "evidence_strength": 0.85,
            "source_credibility": 0.9,
            "rationale_zh": "直接支持 YES 的事实。",
        }],
        "overall_direction": "support_yes",
        "overall_strength": 0.85,
        "conflict_level": 0.1,
        "summary": "证据整体支持 YES",
    }))
    monkeypatch.setattr("app.services.news_sentiment_service.complete_json", gateway)

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    article = result["articles"][0]
    assert article["evidence_direction"] == "support"
    assert article["evidence_strength"] == 0.85
    assert article["source_credibility"] == 0.9
    assert article["rationale_zh"] == "直接支持 YES 的事实。"


def test_analyze_sentiment_does_not_fallback_when_new_fields_missing(monkeypatch):
    """When the LLM returns only the old schema (no evidence_direction etc.),
    analyze_sentiment must NOT整体 fallback. The aggregation layer handles
    missing fields per-item. This locks the partial-failure contract."""
    gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={
        "articles": [{
            "index": 0,
            "sentiment": "positive",
            "impact": "high",
            "key_facts": ["fact"],
            "relevance_to_question": 0.8,
            # NOTE: no evidence_direction/strength/credibility/rationale_zh
        }],
        "overall_direction": "support_yes",
        "overall_strength": 0.7,
        "conflict_level": 0.1,
        "summary": "证据整体支持 YES",
    }))
    monkeypatch.setattr("app.services.news_sentiment_service.complete_json", gateway)

    result = asyncio.run(
        analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    )
    # No fallback: the response is valid (has articles + overall_direction)
    assert "fallback" not in result
    assert result["overall_direction"] == "support_yes"
    # Old fields preserved
    assert result["articles"][0]["sentiment"] == "positive"
