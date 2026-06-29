# 采集规模提升 + LLM 新闻情感分析 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提升事件采集规模（limit 上限 + bid/ask spread 透传），并引入 LLM 新闻情感分析替代纯关键词规则法，使系统的数据输入端与分析质量达到"可参与市场"的基线。

**Architecture:** 环节1 在现有适配器上做增量改进（提高 limit、透传 spread 字段），不改变采集流程结构。环节2 新增 `news_sentiment_service.py` 模块，在 `evidence_scoring_service` 与 `probability_engine_service` 之间插入 LLM 情感分析层：对过滤后的高质量新闻做批量 LLM 分析，输出 sentiment_polarity / impact_assessment，供 evidence profile 和最终概率重估消费。

**Tech Stack:** Python 3.11+ / FastAPI / httpx / AsyncOpenAI (DashScope) / pytest / trafilatura（新增，网页正文提取）

## Global Constraints

- Python 后端文件使用 `logger` 方法（info/error/warning），禁止 `print()`
- API 端点使用 Pydantic 类型注解
- 新增依赖必须带版本上下界（如 `trafilatura>=1.12,<2.0`）
- LLM 调用必须走 `settings.OPENAI_API_KEY` + `settings.DASHSCOPE_BASE_URL`（与现有 probability_engine_service 一致）
- 所有 datetime 使用 timezone-aware 对象
- 失败时走 `fail_closed_empty_list` / `fail_closed_none` 模式，不冒泡 500
- 前端页面在环节1+2 阶段不修改

---

## 文件结构

### 新增文件
- `backend/app/services/news_sentiment_service.py` — LLM 新闻情感分析服务（批量分析、情感极性、影响评估）
- `backend/tests/test_news_sentiment_service.py` — 网络无关单元测试

### 修改文件
- `backend/app/api/routes/events.py:161` — 提高 discover limit 上限
- `backend/app/services/kalshi_event_source.py` — 透传 bid/ask/spread 字段
- `backend/app/services/polymarket_event_source.py` — 透传 volume/liquidity 更详细字段（可选）
- `backend/app/services/event_collection_service.py` — 新增全文抓取（trafilatura）
- `backend/app/services/evidence_scoring_service.py` — 集成 sentiment 字段
- `backend/app/services/event_intelligence_service.py` — 在 analyze_event 中调用 news_sentiment_service
- `backend/app/services/probability_engine_service.py` — LLM prompt 增加 sentiment 上下文
- `backend/app/core/config.py` — 新增配置项
- `backend/.env.example` — 文档化新增配置
- `backend/requirements.txt` — 新增 trafilatura 依赖

---

## 阶段1：环节1 — 采集规模提升

### Task 1: 提高 discover 接口 limit 上限

**Files:**
- Modify: `backend/app/api/routes/events.py:159-166`
- Test: `backend/tests/test_events_routes.py`

**Interfaces:**
- Produces: `discover` 端点接受 `limit` 最大 50（原 20）

- [ ] **Step 1: 查看当前 limit 定义并修改**

读取 `backend/app/api/routes/events.py` 第 159-166 行，将 `le=20` 改为 `le=50`：

```python
@router.get("/discover", response_model=EventDiscoveryResponse)
async def discover_event_intelligence(
    limit: int = Query(default=10, ge=1, le=50),
    use_cache: bool = Query(default=True),
    _auth: None = Depends(require_write_key),
):
    """Discover high-value events and return intelligence records."""
    return await discover_events(limit=limit, use_cache=use_cache)
```

- [ ] **Step 2: 检查是否有测试断言 limit 上限**

```bash
cd backend && grep -rn "le=20\|limit.*20\|max.*20" tests/test_events_routes.py
```

如有断言限制 limit=20 的测试，更新为 50。

- [ ] **Step 3: 运行测试验证**

```bash
cd backend && python -m pytest tests/test_events_routes.py -v --tb=short
```

Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes/events.py backend/tests/test_events_routes.py
git commit -m "feat(api): raise discover limit cap from 20 to 50"
```

---

### Task 2: 透传 Kalshi bid/ask spread 字段

**Files:**
- Modify: `backend/app/services/kalshi_event_source.py`（`_to_candidate_event` 与 `_baseline_pct`）
- Test: `backend/tests/test_kalshi_event_source.py`

**Interfaces:**
- Produces: candidate event dict 新增 `bid_ask: {bid: float, ask: float, spread: float}` 字段（仅 Kalshi）

- [ ] **Step 1: 读取当前 _to_candidate_event 与 _baseline_pct**

读取 `backend/app/services/kalshi_event_source.py` 的 `_to_candidate_event`（约行 100-140）和 `_baseline_pct`（约行 142-155）。

- [ ] **Step 2: 修改 _baseline_pct 返回 bid/ask 详情**

将 `_baseline_pct` 改为返回 `(baseline, bid, ask)` 三元组：

```python
def _baseline_and_quote(market: dict[str, Any]) -> tuple[float, float, float]:
    """Return (baseline_pct, bid, ask) from market data. bid/ask are 0.0 if missing."""
    last = safe_float(market.get("last_price_dollars"), 0.0)
    if last > 0:
        return last * 100, 0.0, 0.0
    bid = safe_float(market.get("yes_bid_dollars"), 0.0)
    ask = safe_float(market.get("yes_ask_dollars"), 0.0)
    if bid > 0 or ask > 0:
        return (bid + ask) / 2 * 100, bid * 100, ask * 100
    return 50.0, 0.0, 0.0
```

- [ ] **Step 3: 修改 _to_candidate_event 透传 bid_ask 字段**

在 `_to_candidate_event` 中调用新函数并加入 `bid_ask` 字段：

```python
def _to_candidate_event(market: dict[str, Any]) -> dict[str, Any] | None:
    # ... existing validation ...
    baseline, bid, ask = _baseline_and_quote(market)
    spread = round(ask - bid, 2) if (bid > 0 and ask > 0) else 0.0
    return {
        # ... existing fields ...
        "baseline": baseline,
        "bid_ask": {"bid": bid, "ask": ask, "spread": spread},
    }
```

- [ ] **Step 4: 写测试验证 bid_ask 字段**

在 `test_kalshi_event_source.py` 中新增测试：

```python
def test_bid_ask_transparent_when_last_price_missing():
    """When last_price is 0, bid/ask midpoint is used and bid_ask field is populated."""
    market = {
        "event_ticker": "TEST-EVENT",
        "title": "Test event",
        "yes_bid_dollars": 0.42,
        "yes_ask_dollars": 0.46,
        "last_price_dollars": 0.0,
        "volume": 1000,
        "liquidity": 5000,
        "categories": [{"name": "Politics"}],
        "url": "https://kalshi.com/markets/test-event",
    }
    candidates = asyncio.run(fetch_candidate_events(limit=1))
    # ... mock _fetch_raw_events to return [market] ...
    assert candidates[0]["bid_ask"]["bid"] == 42.0
    assert candidates[0]["bid_ask"]["ask"] == 46.0
    assert candidates[0]["bid_ask"]["spread"] == 4.0
```

- [ ] **Step 5: 运行测试验证**

```bash
cd backend && python -m pytest tests/test_kalshi_event_source.py -v --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/kalshi_event_source.py backend/tests/test_kalshi_event_source.py
git commit -m "feat(kalshi): transparent bid/ask spread in candidate events"
```

---

## 阶段2：环节2 — LLM 新闻情感分析

### Task 3: 新增 trafilatura 依赖并创建全文抓取工具

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/utils/full_text_fetcher.py`
- Test: `backend/tests/test_full_text_fetcher.py`

**Interfaces:**
- Produces: `async def fetch_full_text(url: str, *, timeout: float = 10.0) -> str | None`

- [ ] **Step 1: 添加 trafilatura 依赖**

在 `backend/requirements.txt` 中添加：

```
trafilatura>=1.12,<2.0
```

- [ ] **Step 2: 安装依赖**

```bash
cd backend && pip install trafilatura>=1.12,<2.0
```

- [ ] **Step 3: 创建 full_text_fetcher.py**

```python
"""Fetch full article text from URLs using trafilatura.

Used to enrich news articles beyond title+description for LLM sentiment analysis.
Falls back to empty string on any failure — never blocks the pipeline.
"""
import asyncio
import logging
import httpx
import trafilatura

logger = logging.getLogger(__name__)

_USER_AGENT = "EventIntelligencePlatform/1.0 (+https://github.com/airdrop2474/prediction-market-reality-filter)"


async def fetch_full_text(url: str, *, timeout: float = 10.0) -> str | None:
    """Fetch and extract main article text from a URL.

    Returns extracted text (may be empty), or None on network/parse failure.
    Never raises — callers can treat None as "no full text available".
    """
    if not url:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
        # trafilatura extraction is CPU-bound — run in thread
        text = await asyncio.to_thread(trafilatura.extract, html)
        if text:
            text = text.strip()[:8000]  # cap at 8000 chars to limit LLM cost
        return text
    except Exception as exc:
        logger.warning("full_text_fetch failed for %s: %s", url, exc)
        return None
```

- [ ] **Step 4: 写测试**

```python
# backend/tests/test_full_text_fetcher.py
import pytest
from app.utils.full_text_fetcher import fetch_full_text


def test_fetch_full_text_returns_none_for_empty_url():
    result = asyncio.run(fetch_full_text(""))
    assert result is None


def test_fetch_full_text_returns_none_on_network_error(monkeypatch):
    """Network failures return None, never raise."""
    async def mock_get(*args, **kwargs):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = asyncio.run(fetch_full_text("https://nonexistent.example.com/article"))
    assert result is None
```

- [ ] **Step 5: 运行测试**

```bash
cd backend && python -m pytest tests/test_full_text_fetcher.py -v --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/utils/full_text_fetcher.py backend/tests/test_full_text_fetcher.py
git commit -m "feat(utils): add full_text_fetcher using trafilatura"
```

---

### Task 4: 在新闻采集流程中集成全文抓取

**Files:**
- Modify: `backend/app/services/event_collection_service.py`（`collect_articles`）
- Test: `backend/tests/test_event_collection_service.py`

**Interfaces:**
- Produces: article dict 新增 `full_text: str | None` 字段

- [ ] **Step 1: 修改 collect_articles 加入全文抓取**

在 `event_collection_service.collect_articles` 末尾，对过滤后的高质量文章做全文抓取（限制篇数避免成本失控）：

```python
from app.utils.full_text_fetcher import fetch_full_text

async def collect_articles(
    event_question: str,
    shared_articles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    # ... existing logic to gather articles ...

    # Enrich top articles with full text (cap at 5 to limit cost)
    articles_needing_full_text = articles[:5]
    tasks = [fetch_full_text(a.get("url", "")) for a in articles_needing_full_text]
    full_texts = await asyncio.gather(*tasks, return_exceptions=True)
    for article, full_text in zip(articles_needing_full_text, full_texts):
        if isinstance(full_text, str) and full_text:
            article["full_text"] = full_text
        else:
            article["full_text"] = None
    # Remaining articles get None
    for article in articles[5:]:
        article["full_text"] = None
    return articles
```

- [ ] **Step 2: 更新测试验证 full_text 字段存在**

在现有测试中加入 full_text 字段断言。

- [ ] **Step 3: 运行测试**

```bash
cd backend && python -m pytest tests/test_event_collection_service.py -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/event_collection_service.py backend/tests/test_event_collection_service.py
git commit -m "feat(collection): enrich top articles with full text via trafilatura"
```

---

### Task 5: 创建 news_sentiment_service LLM 情感分析服务

**Files:**
- Create: `backend/app/services/news_sentiment_service.py`
- Test: `backend/tests/test_news_sentiment_service.py`

**Interfaces:**
- Consumes: article dicts with `title`, `description`, `full_text` (optional), `source`, `url`
- Produces: `async def analyze_sentiment(market_question: str, articles: list[dict]) -> dict` 返回 sentiment profile

- [ ] **Step 1: 创建 news_sentiment_service.py**

```python
"""LLM-powered news sentiment analysis for prediction market events.

Replaces the keyword-based evidence_direction heuristic with LLM judgment.
Batch-analyzes up to 6 articles per event in a single LLM call to control cost.
"""
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_ARTICLES_PER_CALL = 6
_MAX_FULL_TEXT_CHARS = 2000  # per article in prompt

_SYSTEM_PROMPT = """You are a news sentiment analyst for prediction markets.
Given a market question and a list of news articles, analyze:
1. Each article's sentiment polarity toward the YES outcome (positive/negative/neutral)
2. Each article's impact on the event probability (high/medium/low)
3. The overall evidence direction (support_yes / oppose_yes / neutral)
4. Key quotes or facts that drive the assessment

Return ONLY valid JSON (no markdown) with this structure:
{
  "articles": [
    {
      "index": 0,
      "sentiment": "positive|negative|neutral",
      "impact": "high|medium|low",
      "key_facts": ["fact 1", "fact 2"],
      "relevance_to_question": 0.0-1.0
    }
  ],
  "overall_direction": "support_yes|oppose_yes|neutral",
  "overall_strength": 0.0-1.0,
  "conflict_level": 0.0-1.0,
  "summary": "中文一句话总结整体证据方向与强度"
}

All natural-language string values MUST be written in Simplified Chinese (简体中文).
Be conservative: only mark high impact for clear, direct evidence.
"""


def _build_user_prompt(market_question: str, articles: list[dict[str, Any]]) -> str:
    article_blocks = []
    for i, article in enumerate(articles[:_MAX_ARTICLES_PER_CALL]):
        title = article.get("title", "")[:200]
        desc = article.get("description", "")[:500]
        full_text = article.get("full_text") or ""
        full_text = full_text[:_MAX_FULL_TEXT_CHARS] if full_text else ""
        source = article.get("source", "unknown")
        block = f"""---
Article {i}:
Source: {source}
Title: {title}
Description: {desc}
"""
        if full_text:
            block += f"Full text: {full_text}\n"
        article_blocks.append(block)
    return f"""Market question: {market_question[:500]}

News articles:
{"".join(article_blocks)}
"""


async def analyze_sentiment(
    market_question: str,
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze news sentiment for a market question using LLM.

    Returns sentiment profile dict. On any failure, returns a deterministic
    neutral fallback (never raises).
    """
    if not articles:
        return _neutral_fallback("no articles")
    if not settings.OPENAI_API_KEY:
        return _neutral_fallback("no OPENAI_API_KEY configured")

    try:
        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
            timeout=30.0,
            max_retries=1,
        )
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(market_question, articles)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        # Validate minimum structure
        if "articles" not in parsed or "overall_direction" not in parsed:
            return _neutral_fallback("malformed LLM response")
        return parsed
    except Exception as exc:
        logger.warning("news_sentiment LLM call failed: %s", exc)
        return _neutral_fallback(f"LLM error: {exc}")


def _neutral_fallback(reason: str) -> dict[str, Any]:
    """Deterministic neutral fallback when LLM is unavailable."""
    return {
        "articles": [],
        "overall_direction": "neutral",
        "overall_strength": 0.0,
        "conflict_level": 0.0,
        "summary": f"情感分析不可用（{reason}），回退为中性",
        "fallback": True,
    }
```

- [ ] **Step 2: 写网络无关测试**

```python
# backend/tests/test_news_sentiment_service.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.news_sentiment_service import (
    analyze_sentiment,
    _neutral_fallback,
    _build_user_prompt,
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
    long_text = "x" * 5000
    articles = [{"title": "T", "description": "D", "full_text": long_text, "source": "s"}]
    prompt = _build_user_prompt("Q?", articles)
    # _MAX_FULL_TEXT_CHARS = 2000
    assert prompt.count("x") <= 2000


@pytest.mark.asyncio
async def test_analyze_sentiment_returns_neutral_for_empty_articles():
    result = await analyze_sentiment("Question?", [])
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True


@pytest.mark.asyncio
async def test_analyze_sentiment_returns_neutral_without_api_key(monkeypatch):
    monkeypatch.setattr("app.services.news_sentiment_service.settings.OPENAI_API_KEY", "")
    result = await analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    assert result["overall_direction"] == "neutral"
    assert result["fallback"] is True


@pytest.mark.asyncio
async def test_analyze_sentiment_parses_valid_llm_response(monkeypatch):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "articles": [{"index": 0, "sentiment": "positive", "impact": "high", "key_facts": ["fact"], "relevance_to_question": 0.8}],
        "overall_direction": "support_yes",
        "overall_strength": 0.7,
        "conflict_level": 0.1,
        "summary": "证据整体支持 YES 结果",
    })
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr("app.services.news_sentiment_service.AsyncOpenAI", MagicMock(return_value=mock_client))
    monkeypatch.setattr("app.services.news_sentiment_service.settings.OPENAI_API_KEY", "fake-key")

    result = await analyze_sentiment("Question?", [{"title": "T", "description": "D"}])
    assert result["overall_direction"] == "support_yes"
    assert result["overall_strength"] == 0.7
    assert "fallback" not in result
```

- [ ] **Step 3: 运行测试**

```bash
cd backend && python -m pytest tests/test_news_sentiment_service.py -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/news_sentiment_service.py backend/tests/test_news_sentiment_service.py
git commit -m "feat(sentiment): add LLM-powered news sentiment analysis service"
```

---

### Task 6: 在 event_intelligence_service 中集成 sentiment

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py`（`analyze_event` 与 `_build_filtered_news`）
- Modify: `backend/app/services/probability_engine_service.py`（`_build_user_prompt` 加入 sentiment 上下文）

**Interfaces:**
- Consumes: `news_sentiment_service.analyze_sentiment`
- Produces: event record 新增 `sentiment_profile` 字段；LLM prompt 含 sentiment 段

- [ ] **Step 1: 在 _build_filtered_news 中调用 sentiment**

在 `event_intelligence_service._build_filtered_news` 末尾，对过滤后的文章调用 sentiment 分析：

```python
from app.services.news_sentiment_service import analyze_sentiment

async def _build_filtered_news(
    event_question: str,
    shared_articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # ... existing logic to filter articles ...
    news_context = _format_news_context(filtered_articles)

    # LLM sentiment analysis on filtered articles
    sentiment_profile = await analyze_sentiment(event_question, filtered_articles)

    return {
        "context": news_context,
        "articles": filtered_articles,
        "evidence_profile": evidence_profile,
        "sentiment_profile": sentiment_profile,
    }
```

- [ ] **Step 2: 在 analyze_event 中将 sentiment 传入 LLM prompt**

修改 `analyze_event` 中调用 `ai_analysis_service.analyze_market` 的地方，把 sentiment_profile 加入 news_context：

```python
sentiment_summary = news_data.get("sentiment_profile", {}).get("summary", "")
if sentiment_summary:
    news_context = f"{news_context}\n\nLLM 情感分析结论: {sentiment_summary}"
```

- [ ] **Step 3: 在 event record 中记录 sentiment_profile**

在 `build_event_record` 中加入：

```python
record["sentiment_profile"] = news_data.get("sentiment_profile", {})
```

- [ ] **Step 4: 更新 probability_engine_service 的 prompt**

在 `_build_user_prompt` 中加入 sentiment 上下文段（如果存在）：

```python
# 在 news_context 之后加入
if sentiment_summary:
    prompt += f"\n\nLLM 情感分析: {sentiment_summary}\n"
```

- [ ] **Step 5: 运行全量测试**

```bash
cd backend && python -m pytest tests/ -x -q --tb=short
```

Expected: 不退化（原有测试全部通过）

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/event_intelligence_service.py backend/app/services/probability_engine_service.py
git commit -m "feat(intelligence): integrate LLM sentiment into event analysis pipeline"
```

---

### Task 7: 新增配置项与文档

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: 添加配置项**

在 `config.py` 中添加：

```python
# News sentiment analysis (LLM-powered, replaces keyword heuristic)
NEWS_SENTIMENT_ENABLED: bool = os.getenv("NEWS_SENTIMENT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
NEWS_SENTIMENT_MAX_ARTICLES: int = int(os.getenv("NEWS_SENTIMENT_MAX_ARTICLES", "6"))
NEWS_FULL_TEXT_FETCH_ENABLED: bool = os.getenv("NEWS_FULL_TEXT_FETCH_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
NEWS_FULL_TEXT_MAX_ARTICLES: int = int(os.getenv("NEWS_FULL_TEXT_MAX_ARTICLES", "5"))
```

- [ ] **Step 2: 修改 news_sentiment_service 和 full_text_fetcher 读取配置**

在 `news_sentiment_service.analyze_sentiment` 开头加入开关检查：

```python
if not getattr(settings, "NEWS_SENTIMENT_ENABLED", True):
    return _neutral_fallback("NEWS_SENTIMENT_ENABLED is false")
```

在 `event_collection_service.collect_articles` 中用 `settings.NEWS_FULL_TEXT_FETCH_ENABLED` 和 `settings.NEWS_FULL_TEXT_MAX_ARTICLES` 控制全文抓取。

- [ ] **Step 3: 更新 .env.example**

```bash
# === NEWS ANALYSIS ===
# LLM-powered sentiment analysis for prediction market events.
# When true, uses LLM to analyze news sentiment + impact (replaces keyword heuristic).
# Costs ~1 LLM call per event (batch analysis of up to 6 articles).
NEWS_SENTIMENT_ENABLED=true
NEWS_SENTIMENT_MAX_ARTICLES=6

# Full text extraction from article URLs (trafilatura).
# When true, fetches and extracts main article text for top articles.
# Costs ~5 HTTP requests per event; falls back gracefully on failure.
NEWS_FULL_TEXT_FETCH_ENABLED=true
NEWS_FULL_TEXT_MAX_ARTICLES=5
```

- [ ] **Step 4: 运行测试**

```bash
cd backend && python -m pytest tests/ -x -q --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/.env.example backend/app/services/news_sentiment_service.py backend/app/services/event_collection_service.py
git commit -m "feat(config): add NEWS_SENTIMENT and NEWS_FULL_TEXT config with docs"
```

---

## Self-Review Checklist

完成后检查：

- [ ] discover limit 已从 20 提升到 50
- [ ] Kalshi bid/ask/spread 字段已透传到 candidate event
- [ ] trafilatura 依赖已添加，全文抓取工具已创建
- [ ] news_sentiment_service 已创建，支持批量 LLM 分析
- [ ] sentiment_profile 已集成到 event record
- [ ] LLM prompt 已包含 sentiment 上下文
- [ ] 所有新功能默认开启但有开关可关闭
- [ ] 全量后端测试不退化（1105+ passed）
- [ ] 失败时走 fail_closed 模式，不冒泡 500

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-28-discovery-scale-and-llm-sentiment.md`. Two execution options:

1. **Subagent-Driven (recommended)** - 每个 Task 派一个 fresh subagent，task 间 review
2. **Inline Execution** - 在当前会话中逐 Task 执行，带 checkpoint

Which approach?
