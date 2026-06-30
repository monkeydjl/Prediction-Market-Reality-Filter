# Evidence Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-article `evidence_breakdown` field to `EventRecord` by extending the existing `analyze_sentiment` LLM output schema and aggregating it through a new pure-function service.

**Architecture:** Extend `news_sentiment_service._SYSTEM_PROMPT` to make the LLM also emit per-article `evidence_direction` / `evidence_strength` / `source_credibility` / `rationale_zh`. A new `evidence_aggregation_service.aggregate_evidence_breakdown()` pure function filters, normalizes, and禁词-cleans those items into `EventRecord.evidence_breakdown`. The integration point is `analyze_event()` (not `build_event_record()`), because `build_event_record()` cannot access `filtered_articles`. The new field defaults to `[]` everywhere, so the change is fully backward-compatible.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, OpenAI SDK (DashScope), pytest.

## Global Constraints

- All natural-language string values produced by the LLM MUST be written in Simplified Chinese (简体中文); JSON keys and enum values stay in English.
- The decision report and any field that flows into it MUST NOT contain trading terms: `long`, `short`, `buy`, `sell`, `position`, `kelly`, `order` (case-insensitive). `rationale_zh` is post-filtered by the aggregation layer.
- `evidence_breakdown` is an explanation/audit layer. It MUST NOT participate in `evidence_profile`, `ai_probability`, `regression_to_market`, or `actionable_recommendation` calculations.
- Pydantic mutable defaults MUST use `Field(default_factory=list)`, not `= []`.
- The new model MUST be named `EvidenceBreakdownItem` (not `EvidenceItem`, which already exists for UI evidence items).
- `analyze_sentiment` partial failures (missing new fields on some articles) MUST degrade per-item, never as a whole-response fallback.
- Frontend pages MUST NOT be modified (existing project hard constraint).

---

### Task 1: Config flag + Pydantic schema

**Files:**
- Modify: `backend/app/core/config.py:84-89` (NEWS_SENTIMENT block)
- Modify: `backend/app/models/event.py:142-162` (EvidenceItem area) and `backend/app/models/event.py:246-276` (EventRecord)
- Modify: `backend/.env.example`
- Test: `backend/tests/test_event_store.py` (existing `_make_record` should keep working)

**Interfaces:**
- Consumes: none
- Produces:
  - `settings.EVIDENCE_BREAKDOWN_ENABLED: bool` (default True)
  - `EvidenceBreakdownItem` Pydantic model in `app.models.event`
  - `EventRecord.evidence_breakdown: list[EvidenceBreakdownItem]` field (default `[]` via `Field(default_factory=list)`)

- [ ] **Step 1: Add config flag in `backend/app/core/config.py`**

Insert immediately after the `NEWS_SENTIMENT_MAX_ARTICLES` block (after line 89):

```python
    # Per-article evidence breakdown (Stage: evidence decomposition). Emits
    # structured direction/strength/credibility/rationale per article from the
    # existing news sentiment LLM call. Explanation/audit layer only; does NOT
    # participate in evidence_profile or ai_probability.
    EVIDENCE_BREAKDOWN_ENABLED: bool = _env_bool(
        "EVIDENCE_BREAKDOWN_ENABLED", "true"
    )
```

- [ ] **Step 2: Add `EvidenceBreakdownItem` model in `backend/app/models/event.py`**

Insert immediately AFTER the existing `EvidenceItem` class (after line 162, before `Tracking`):

```python
class EvidenceBreakdownItem(BaseModel):
    """One article's contribution to the event-level YES/NO evidence (Stage:
    evidence decomposition).

    Produced by aggregating the per-article fields emitted by the
    ``analyze_sentiment`` LLM call. Unlike ``EvidenceItem`` (which carries
    quality/relevance for the UI), this model carries the LLM's directional
    judgment (support/oppose) and is purely an audit/explanation layer: it
    MUST NOT feed back into ``evidence_profile`` or ``ai_probability``.
    """

    source: str = ""
    title: str = ""
    direction: str  # support | oppose
    strength: float = 0.0  # 0-1
    credibility: float = 0.0  # 0-1
    rationale_zh: str = ""
```

- [ ] **Step 3: Add `evidence_breakdown` field to `EventRecord`**

In `backend/app/models/event.py`, find the `EventRecord` class. After the existing `actionable_recommendation: ActionableRecommendation | None = None` line (line 275), add:

```python
    evidence_breakdown: list[EvidenceBreakdownItem] = Field(default_factory=list)
```

- [ ] **Step 4: Document the flag in `backend/.env.example`**

Find the `NEWS_SENTIMENT_*` section and add after it:

```env
# Per-article evidence breakdown. Extends the existing news sentiment LLM
# output with direction/strength/credibility/rationale per article. Audit
# layer only; does NOT affect probability calculation.
EVIDENCE_BREAKDOWN_ENABLED=true
```

- [ ] **Step 5: Run existing event store tests to verify no regression**

Run: `python -m pytest backend/tests/test_event_store.py -v`
Expected: PASS (all existing tests; `_make_record` uses `EventRecord(extra="allow")` so a new optional field is harmless)

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/config.py backend/app/models/event.py backend/.env.example
git commit -m "feat: add EvidenceBreakdownItem schema and EVIDENCE_BREAKDOWN_ENABLED flag" -m "New EventRecord.evidence_breakdown field (default []) is an audit/explanation layer for per-article YES/NO evidence. Does not affect probability calculation. Backward compatible."
```

---

### Task 2: Aggregation pure function (TDD)

**Files:**
- Create: `backend/app/services/evidence_aggregation_service.py`
- Create: `backend/tests/test_evidence_aggregation_service.py`

**Interfaces:**
- Consumes: `sentiment_articles: list[dict]` (LLM output from `analyze_sentiment`), `original_articles: list[dict]` (filtered news articles from `filter_news_for_market`)
- Produces: `aggregate_evidence_breakdown(sentiment_articles, original_articles) -> list[dict[str, Any]]` — list of `{source, title, direction, strength, credibility, rationale_zh}` dicts

- [ ] **Step 1: Write the failing test file `backend/tests/test_evidence_aggregation_service.py`**

```python
"""Tests for evidence_aggregation_service.aggregate_evidence_breakdown.

Pure-function tests (no LLM, no IO). Verifies filtering, normalization,
禁词 replacement, index join, and ordering.
"""
import unittest

from app.services.evidence_aggregation_service import aggregate_evidence_breakdown


class AggregateEvidenceBreakdownTests(unittest.TestCase):
    def test_empty_inputs_return_empty_list(self):
        self.assertEqual(aggregate_evidence_breakdown([], []), [])
        self.assertEqual(aggregate_evidence_breakdown(None, None), [])
        self.assertEqual(aggregate_evidence_breakdown([], None), [])
        self.assertEqual(aggregate_evidence_breakdown(None, []), [])

    def test_neutral_direction_filtered_out(self):
        sentiment = [{"index": 0, "evidence_direction": "neutral", "evidence_strength": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_missing_direction_filtered_out(self):
        sentiment = [{"index": 0, "evidence_strength": 0.9}]  # no direction key
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_unknown_direction_filtered_out(self):
        sentiment = [{"index": 0, "evidence_direction": "maybe", "evidence_strength": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_strength_below_threshold_filtered_out(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.19}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_strength_at_threshold_kept(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.2,
                      "source_credibility": 0.8, "rationale_zh": "原因"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["strength"], 0.2)

    def test_support_article_produces_breakdown_item(self):
        sentiment = [{
            "index": 0,
            "evidence_direction": "support",
            "evidence_strength": 0.8,
            "source_credibility": 0.9,
            "rationale_zh": "直接支持 YES 的事实。",
        }]
        original = [{"source": "Reuters", "title": "Fed signals rate cut"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 1)
        item = out[0]
        self.assertEqual(item["source"], "Reuters")
        self.assertEqual(item["title"], "Fed signals rate cut")
        self.assertEqual(item["direction"], "support")
        self.assertEqual(item["strength"], 0.8)
        self.assertEqual(item["credibility"], 0.9)
        self.assertEqual(item["rationale_zh"], "直接支持 YES 的事实。")

    def test_oppose_article_produces_breakdown_item(self):
        sentiment = [{
            "index": 0,
            "evidence_direction": "oppose",
            "evidence_strength": 0.7,
            "source_credibility": 0.6,
            "rationale_zh": "反对 YES。",
        }]
        original = [{"source": "AP", "title": "Bill stalled"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["direction"], "oppose")

    def test_index_missing_skipped(self):
        sentiment = [{"evidence_direction": "support", "evidence_strength": 0.8}]  # no index
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_non_integer_skipped(self):
        sentiment = [{"index": 1.5, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_bool_skipped(self):
        # bool is a subclass of int in Python; must be rejected explicitly.
        sentiment = [{"index": True, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_negative_skipped(self):
        sentiment = [{"index": -1, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_index_out_of_range_skipped(self):
        sentiment = [{"index": 5, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]  # only index 0 exists
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_original_article_missing_title_skipped(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters"}]  # no title
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_original_article_empty_title_skipped(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "   "}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_strength_clamped_to_range(self):
        sentiment = [{"index": 0, "evidence_direction": "support",
                      "evidence_strength": 1.5, "source_credibility": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["strength"], 1.0)

    def test_strength_negative_clamped_to_zero(self):
        sentiment = [{"index": 0, "evidence_direction": "support",
                      "evidence_strength": -0.5, "source_credibility": 0.9}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        # -0.5 clamps to 0.0, which is below 0.2 threshold, so filtered out
        self.assertEqual(out, [])

    def test_credibility_clamped_to_range(self):
        sentiment = [{"index": 0, "evidence_direction": "support",
                      "evidence_strength": 0.8, "source_credibility": 1.7}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["credibility"], 1.0)

    def test_credibility_missing_defaults_to_half(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["credibility"], 0.5)

    def test_source_missing_defaults_to_unknown(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"title": "T"}]  # no source
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["source"], "unknown")

    def test_title_truncated_to_200_chars(self):
        long_title = "A" * 500
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": long_title}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out[0]["title"]), 200)

    def test_rationale_truncated_to_300_chars(self):
        long_rationale = "原因" * 200  # 400 chars
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": long_rationale}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out[0]["rationale_zh"]), 300)

    def test_rationale_missing_becomes_empty_string(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["rationale_zh"], "")

    def test_banned_word_long_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "这是 long 信号"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("long", out[0]["rationale_zh"].lower())
        self.assertIn("支持 YES", out[0]["rationale_zh"])

    def test_banned_word_short_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "这是 short 信号"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("short", out[0]["rationale_zh"].lower())
        self.assertIn("支持 NO", out[0]["rationale_zh"])

    def test_banned_word_case_insensitive_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "这是 LONG 信号"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("long", out[0]["rationale_zh"].lower())

    def test_banned_word_buy_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "建议 buy"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("buy", out[0]["rationale_zh"].lower())
        self.assertIn("支持 YES", out[0]["rationale_zh"])

    def test_banned_word_position_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "调整 position"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("position", out[0]["rationale_zh"].lower())
        self.assertIn("配置", out[0]["rationale_zh"])

    def test_banned_word_kelly_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "kelly 公式"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("kelly", out[0]["rationale_zh"].lower())
        self.assertIn("风险预算", out[0]["rationale_zh"])

    def test_banned_word_order_replaced(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": "提交 order"}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertNotIn("order", out[0]["rationale_zh"].lower())
        self.assertIn("决策", out[0]["rationale_zh"])

    def test_order_preserved_from_sentiment_articles(self):
        sentiment = [
            {"index": 0, "evidence_direction": "support", "evidence_strength": 0.7,
             "source_credibility": 0.8, "rationale_zh": "第一条"},
            {"index": 1, "evidence_direction": "oppose", "evidence_strength": 0.6,
             "source_credibility": 0.7, "rationale_zh": "第二条"},
            {"index": 2, "evidence_direction": "support", "evidence_strength": 0.5,
             "source_credibility": 0.6, "rationale_zh": "第三条"},
        ]
        original = [
            {"source": "Reuters", "title": "A"},
            {"source": "AP", "title": "B"},
            {"source": "Bloomberg", "title": "C"},
        ]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["title"], "A")
        self.assertEqual(out[1]["title"], "B")
        self.assertEqual(out[2]["title"], "C")

    def test_mixed_valid_invalid_items_only_valid_returned(self):
        sentiment = [
            {"index": 0, "evidence_direction": "support", "evidence_strength": 0.8},  # valid
            {"index": 1, "evidence_direction": "neutral", "evidence_strength": 0.9},  # filtered
            {"index": 99, "evidence_direction": "support", "evidence_strength": 0.8},  # out of range
            {"index": 2, "evidence_direction": "support", "evidence_strength": 0.8},  # valid
        ]
        original = [
            {"source": "Reuters", "title": "A"},
            {"source": "AP", "title": "B"},
            {"source": "Bloomberg", "title": "C"},
        ]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "A")
        self.assertEqual(out[1]["title"], "C")

    def test_non_dict_sentiment_article_skipped(self):
        sentiment = ["not a dict", None, 42]
        original = [{"source": "Reuters", "title": "T"}]
        self.assertEqual(aggregate_evidence_breakdown(sentiment, original), [])

    def test_rationale_non_string_coerced_to_string(self):
        sentiment = [{"index": 0, "evidence_direction": "support", "evidence_strength": 0.8,
                      "rationale_zh": 12345}]
        original = [{"source": "Reuters", "title": "T"}]
        out = aggregate_evidence_breakdown(sentiment, original)
        self.assertEqual(out[0]["rationale_zh"], "12345")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails (module not found)**

Run: `python -m pytest backend/tests/test_evidence_aggregation_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.evidence_aggregation_service'`

- [ ] **Step 3: Create `backend/app/services/evidence_aggregation_service.py`**

```python
"""Evidence aggregation service (Stage: evidence decomposition).

Pure-function layer that transforms the per-article fields emitted by
``analyze_sentiment`` into the ``EventRecord.evidence_breakdown`` audit list.

This is an EXPLANATION layer only. It MUST NOT feed back into
``evidence_profile``, ``ai_probability``, ``regression_to_market``, or
``actionable_recommendation`` calculations.

Filtering rules:
- direction missing/unknown/neutral -> skip
- strength < 0.2 -> skip
- index missing/non-integer/bool/negative/out-of-range -> skip
- original article missing/empty title -> skip

Normalization:
- strength/credibility clamped to [0, 1]
- credibility missing -> 0.5
- source missing -> "unknown"
- title truncated to 200 chars
- rationale_zh: stringified, stripped, banned-word replaced, truncated to 300 chars

Output order preserves the sentiment_articles order (no re-sorting).
"""
from typing import Any

# Banned trading terms and their event-vocabulary replacements. Case-insensitive
# on the keys. Replacements use event vocabulary (YES/NO) per the decision-report
# invariant locked by test_report_uses_event_vocabulary_only.
_BANNED_WORD_REPLACEMENTS: dict[str, str] = {
    "long": "支持 YES",
    "buy": "支持 YES",
    "short": "支持 NO",
    "sell": "支持 NO",
    "position": "配置",
    "kelly": "风险预算",
    "order": "决策",
}

_TITLE_MAX = 200
_RATIONALE_MAX = 300
_STRENGTH_THRESHOLD = 0.2
_DEFAULT_CREDIBILITY = 0.5
_VALID_DIRECTIONS = {"support", "oppose"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_valid_index(index: Any, length: int) -> bool:
    """True only for genuine non-negative ints in range. bool is rejected
    explicitly because bool is a subclass of int in Python."""
    if isinstance(index, bool):
        return False
    if not isinstance(index, int):
        return False
    if index < 0 or index >= length:
        return False
    return True


def _filter_banned_words(text: str) -> str:
    """Replace banned trading terms with event-vocabulary equivalents.

    Case-insensitive matching on the banned word; replacement preserves the
    rest of the string. Word-boundary safe is NOT enforced because the banned
    terms rarely appear as substrings of legitimate Chinese text; the simple
    lower() + replace is sufficient for the禁词 invariant.
    """
    if not text:
        return ""
    result = text
    lowered = result.lower()
    for banned, replacement in _BANNED_WORD_REPLACEMENTS.items():
        if banned in lowered:
            # Case-insensitive replace: rebuild the string segment by segment.
            # Simple approach: lower the whole string, replace, but that loses
            # original case. Better: find all occurrences case-insensitively
            # and replace each with the replacement string.
            import re
            pattern = re.compile(re.escape(banned), re.IGNORECASE)
            result = pattern.sub(replacement, result)
            lowered = result.lower()
    return result


def aggregate_evidence_breakdown(
    sentiment_articles: list[dict[str, Any]] | None,
    original_articles: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Transform LLM sentiment article output into EventRecord.evidence_breakdown.

    Args:
        sentiment_articles: per-article output from ``analyze_sentiment`` LLM
            call. Each item may carry ``index``, ``evidence_direction``,
            ``evidence_strength``, ``source_credibility``, ``rationale_zh``.
        original_articles: the filtered news articles (from
            ``filter_news_for_market``) that were fed to the LLM. Used as the
            source of truth for ``source`` and ``title`` (LLM self-reported
            source/title is not trusted).

    Returns:
        List of breakdown items, each with keys ``source``, ``title``,
        ``direction``, ``strength``, ``credibility``, ``rationale_zh``.
        Order preserves the input ``sentiment_articles`` order. Items that
        fail validation are skipped (per-item fail-closed).
    """
    if not sentiment_articles or not original_articles:
        return []

    breakdown: list[dict[str, Any]] = []
    original_len = len(original_articles)

    for item in sentiment_articles:
        if not isinstance(item, dict):
            continue

        index = item.get("index")
        if not _is_valid_index(index, original_len):
            continue

        direction = item.get("evidence_direction")
        if not isinstance(direction, str) or direction not in _VALID_DIRECTIONS:
            continue

        strength_raw = item.get("evidence_strength", 0)
        try:
            strength = _clamp(float(strength_raw), 0.0, 1.0)
        except (TypeError, ValueError):
            continue
        if strength < _STRENGTH_THRESHOLD:
            continue

        original = original_articles[index]
        title = str(original.get("title") or "").strip()
        if not title:
            continue

        credibility_raw = item.get("source_credibility", _DEFAULT_CREDIBILITY)
        try:
            credibility = _clamp(float(credibility_raw), 0.0, 1.0)
        except (TypeError, ValueError):
            credibility = _DEFAULT_CREDIBILITY

        source = str(original.get("source") or "unknown").strip() or "unknown"
        if not source:
            source = "unknown"

        rationale_raw = item.get("rationale_zh", "")
        rationale = _filter_banned_words(str(rationale_raw).strip())[:_RATIONALE_MAX]

        breakdown.append({
            "source": source,
            "title": title[:_TITLE_MAX],
            "direction": direction,
            "strength": strength,
            "credibility": credibility,
            "rationale_zh": rationale,
        })

    return breakdown
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_evidence_aggregation_service.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/evidence_aggregation_service.py backend/tests/test_evidence_aggregation_service.py
git commit -m "feat: add evidence_aggregation_service pure function" -m "Transforms analyze_sentiment per-article output into EventRecord.evidence_breakdown. Per-item fail-closed: neutral/low-strength/invalid-index items skipped. Banned trading terms replaced with event vocabulary. Fully unit-tested."
```

---

### Task 3: Extend `analyze_sentiment` LLM prompt

**Files:**
- Modify: `backend/app/services/news_sentiment_service.py:19-45` (`_SYSTEM_PROMPT`)
- Test: `backend/tests/test_news_sentiment_service.py` (add prompt + passthrough tests)

**Interfaces:**
- Consumes: none
- Produces: `analyze_sentiment()` LLM output now includes `evidence_direction` / `evidence_strength` / `source_credibility` / `rationale_zh` on each article item (when LLM honors the prompt)

- [ ] **Step 1: Extend `_SYSTEM_PROMPT` in `backend/app/services/news_sentiment_service.py`**

Find the existing `_SYSTEM_PROMPT` (lines 19-45). Replace its JSON schema block and add the new assessment instructions. The full new prompt:

```python
_SYSTEM_PROMPT = """You are a news sentiment analyst for prediction markets.
Given a market question and a list of news articles, analyze:
1. Each article's sentiment polarity toward the YES outcome (positive/negative/neutral)
2. Each article's impact on the event probability (high/medium/low)
3. The overall evidence direction (support_yes / oppose_yes / neutral)
4. Key quotes or facts that drive the assessment
5. evidence_direction: does this article's concrete facts support or oppose the YES outcome?
   (support | oppose | neutral) - based on facts, not tone.
6. evidence_strength: how strongly does this article move the probability?
   (0.0-1.0) - consider specificity, directness, freshness, source authority.
7. source_credibility: how trustworthy is this source for this topic?
   (0.0-1.0) - official/regulatory > Reuters/AP/Bloomberg > established media > aggregators/blogs.
8. rationale_zh: one Simplified Chinese sentence explaining your direction+strength assessment.
   Use event vocabulary (YES/NO/支持/反对). Do NOT use trading terms: long, short, buy, sell, position, kelly, order.

Return ONLY valid JSON (no markdown) with this structure:
{
  "articles": [
    {
      "index": 0,
      "sentiment": "positive|negative|neutral",
      "impact": "high|medium|low",
      "key_facts": ["fact 1", "fact 2"],
      "relevance_to_question": 0.0-1.0,
      "evidence_direction": "support|oppose|neutral",
      "evidence_strength": 0.0-1.0,
      "source_credibility": 0.0-1.0,
      "rationale_zh": "一句中文说明"
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
```

- [ ] **Step 2: Add failing tests in `backend/tests/test_news_sentiment_service.py`**

Append these tests to the existing test file:

```python
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
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
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
    article = result["articles"][0]
    assert article["evidence_direction"] == "support"
    assert article["evidence_strength"] == 0.85
    assert article["source_credibility"] == 0.9
    assert article["rationale_zh"] == "直接支持 YES 的事实。"


def test_analyze_sentiment_does_not_fallback_when_new_fields_missing(monkeypatch):
    """When the LLM returns only the old schema (no evidence_direction etc.),
    analyze_sentiment must NOT整体 fallback. The aggregation layer handles
    missing fields per-item. This locks the partial-failure contract."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
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
    # No fallback: the response is valid (has articles + overall_direction)
    assert "fallback" not in result
    assert result["overall_direction"] == "support_yes"
    # Old fields preserved
    assert result["articles"][0]["sentiment"] == "positive"
```

- [ ] **Step 3: Run tests to verify the prompt test passes (Step 1 already implemented it) and the passthrough tests pass**

Run: `python -m pytest backend/tests/test_news_sentiment_service.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/news_sentiment_service.py backend/tests/test_news_sentiment_service.py
git commit -m "feat: extend analyze_sentiment prompt with per-article evidence fields" -m "LLM now emits evidence_direction/strength/source_credibility/rationale_zh per article. Partial-failure tolerant: missing new fields do not trigger整体 fallback. Aggregation layer handles per-item skip."
```

---

### Task 4: Wire `analyze_event` integration

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py:223-232` (`analyze_event` signature) and the two discovery call sites (around line 346 and 646)
- Test: `backend/tests/test_event_intelligence_service.py`

**Interfaces:**
- Consumes: `aggregate_evidence_breakdown` from Task 2, `settings.EVIDENCE_BREAKDOWN_ENABLED` from Task 1
- Produces: `analyze_event()` accepts `filtered_articles` kwarg; populates `record["evidence_breakdown"]`

- [ ] **Step 1: Add `filtered_articles` parameter to `analyze_event`**

In `backend/app/services/event_intelligence_service.py`, find `analyze_event` signature (lines 223-232). Add `filtered_articles` parameter after `market_quote`:

```python
async def analyze_event(
    event_question: str,
    baseline_probability: float = 50.0,
    news_context: str = "",
    source: dict[str, Any] | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
    sentiment_profile: dict[str, Any] | None = None,
    market_quote: dict[str, Any] | None = None,
    filtered_articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

- [ ] **Step 2: Add aggregation call inside `analyze_event`**

Find the end of `analyze_event` where `record` is built and returned (look for the `return record` line inside `analyze_event`). Just before the `return record` line, insert:

```python
    from app.services.evidence_aggregation_service import aggregate_evidence_breakdown

    if settings.EVIDENCE_BREAKDOWN_ENABLED and sentiment_profile and filtered_articles:
        record["evidence_breakdown"] = aggregate_evidence_breakdown(
            sentiment_profile.get("articles", []),
            filtered_articles,
        )
    else:
        record["evidence_breakdown"] = []
```

- [ ] **Step 3: Add `filtered_articles=` at the two discovery call sites**

Find the two places that call `analyze_event` and pass `sentiment_profile`. They are around lines 342-360 and 646-657.

First call site (around line 352, inside the `else` branch of `analyze_event_from_question` or similar):

```python
        record = await analyze_event(
            event_question=event_question,
            baseline_probability=baseline_probability,
            news_context=filtered_news["context"],
            source={"type": "manual"},
            volume=volume,
            liquidity=liquidity,
            sentiment_profile=filtered_news.get("sentiment_profile"),
            filtered_articles=filtered_news.get("articles", []),
        )
```

Second call site (around line 646, inside `process_event`):

```python
                record = await analyze_event(
                    event_question=question,
                    baseline_probability=safe_float(
                        candidate.get("baseline_probability"), 50.0
                    ),
                    news_context=filtered_news["context"],
                    source=source,
                    volume=candidate.get("volume"),
                    liquidity=candidate.get("liquidity"),
                    sentiment_profile=filtered_news.get("sentiment_profile"),
                    market_quote=market_quote,
                    filtered_articles=filtered_news.get("articles", []),
                )
```

- [ ] **Step 4: Add failing tests in `backend/tests/test_event_intelligence_service.py`**

Append these tests to the existing test file. They use the same `_run` + `AsyncMock` + `patch` pattern as the existing sentiment_profile tests:

```python
class EvidenceBreakdownTests(unittest.TestCase):
    """Locks the evidence_breakdown integration in analyze_event (Stage:
    evidence decomposition). The field is an audit/explanation layer and
    MUST NOT affect ai_probability / evidence_profile / actionable_recommendation."""

    SENTIMENT_WITH_EVIDENCE = {
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
    }

    FILTERED_ARTICLES = [
        {"source": "Reuters", "title": "Fed signals rate cut", "description": "desc"}
    ]

    def test_analyze_event_populates_evidence_breakdown_when_enabled(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)), \
                patch("app.services.event_intelligence_service.settings.EVIDENCE_BREAKDOWN_ENABLED",
                      True):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT_WITH_EVIDENCE,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertIn("evidence_breakdown", record)
        self.assertEqual(len(record["evidence_breakdown"]), 1)
        item = record["evidence_breakdown"][0]
        self.assertEqual(item["source"], "Reuters")
        self.assertEqual(item["title"], "Fed signals rate cut")
        self.assertEqual(item["direction"], "support")
        self.assertEqual(item["strength"], 0.85)
        self.assertEqual(item["credibility"], 0.9)
        self.assertEqual(item["rationale_zh"], "直接支持 YES 的事实。")

    def test_analyze_event_evidence_breakdown_empty_when_disabled(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)), \
                patch("app.services.event_intelligence_service.settings.EVIDENCE_BREAKDOWN_ENABLED",
                      False):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT_WITH_EVIDENCE,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_evidence_breakdown_empty_when_no_sentiment(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=None,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_evidence_breakdown_empty_when_no_filtered_articles(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT_WITH_EVIDENCE,
                filtered_articles=None,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_evidence_breakdown_filters_neutral_articles(self):
        # sentiment article with neutral direction -> filtered out by aggregation
        sentiment = {
            "articles": [{
                "index": 0,
                "evidence_direction": "neutral",
                "evidence_strength": 0.9,
            }],
            "overall_direction": "neutral",
            "overall_strength": 0.0,
            "conflict_level": 0.0,
            "summary": "neutral",
        }
        analyze = AsyncMock(return_value={
            "market_question": "Q?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Q?",
                baseline_probability=50,
                news_context="direction: neutral",
                sentiment_profile=sentiment,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_does_not_break_without_filtered_articles_kwarg(self):
        """Backward compat: old callers that do not pass filtered_articles
        still get a working record with evidence_breakdown=[]."""
        analyze = AsyncMock(return_value={
            "market_question": "Q?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Q?",
                baseline_probability=50,
                news_context="direction: support",
                # NOTE: no filtered_articles kwarg
            ))
        self.assertEqual(record.get("evidence_breakdown", []), [])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_event_intelligence_service.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Run the full backend test suite to verify no regression**

Run: `python -m pytest backend/tests/ -x --timeout=60`
Expected: PASS (no failures)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/event_intelligence_service.py backend/tests/test_event_intelligence_service.py
git commit -m "feat: wire evidence_breakdown into analyze_event" -m "analyze_event accepts filtered_articles kwarg and populates record['evidence_breakdown'] via aggregate_evidence_breakdown. Disabled flag / missing sentiment / missing articles all degrade to []. Backward compatible: callers without filtered_articles still work."
```

---

### Task 5: End-to-end verification + 禁词 invariant test

**Files:**
- Test: `backend/tests/test_decision_report_service.py` (extend existing禁词 test)
- No production code changes

**Interfaces:**
- Consumes: all prior tasks
- Produces: verification that `evidence_breakdown` flows through the full stack and respects the禁词 invariant

- [ ] **Step 1: Extend the禁词 test in `backend/tests/test_decision_report_service.py`**

Find the existing `test_report_uses_event_vocabulary_only` test (around line 71-76). Update it to also include `evidence_breakdown` in the test record so the invariant covers the new field:

```python
    def test_report_uses_event_vocabulary_only(self):
        # The decision report must not introduce trading terms (event-conventions).
        # Includes evidence_breakdown[*].rationale_zh since it flows into the
        # event record (and could surface in future report extensions).
        pred = _prediction()
        rec = _record()
        rec["evidence_breakdown"] = [
            {
                "source": "Reuters",
                "title": "Test",
                "direction": "support",
                "strength": 0.8,
                "credibility": 0.9,
                "rationale_zh": "支持 YES 的证据",  # clean — no banned words
            }
        ]
        report = build_decision_report(pred, rec)
        blob = str(report).lower()
        for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(banned, blob)
```

- [ ] **Step 2: Run the extended禁词 test to verify it passes**

Run: `python -m pytest backend/tests/test_decision_report_service.py::BuildDecisionReportTests::test_report_uses_event_vocabulary_only -v`
Expected: PASS

- [ ] **Step 3: Run the full backend test suite final verification**

Run: `python -m pytest backend/tests/ --timeout=60 -q`
Expected: PASS (no failures; same count as before + new tests added)

- [ ] **Step 4: Verify EventRecord schema accepts the new field**

Run this one-off check to confirm Pydantic validation passes with a populated `evidence_breakdown`:

```bash
python -c "from app.models.event import EventRecord, EvidenceBreakdownItem; print('OK')"
```

Expected: prints `OK` with no import / validation errors.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_decision_report_service.py
git commit -m "test: extend禁词 invariant test to cover evidence_breakdown" -m "Locks the event-vocabulary invariant on the new evidence_breakdown[*].rationale_zh field. Final verification: full backend test suite passes with no regression."
```

---

## Self-Review Notes

### Spec coverage check
- `analyze_sentiment` prompt extension → Task 3 ✅
- `evidence_aggregation_service.py` pure function → Task 2 ✅
- `EventRecord.evidence_breakdown` schema → Task 1 ✅
- `analyze_event` integration → Task 4 ✅
- `EVIDENCE_BREAKDOWN_ENABLED` config → Task 1 ✅
- `.env.example` doc → Task 1 ✅
- `test_news_sentiment_service` prompt + passthrough → Task 3 ✅
- `test_evidence_aggregation_service` full unit tests → Task 2 ✅
- `test_event_intelligence_service` integration → Task 4 ✅
- 禁词 invariant extension → Task 5 ✅
- Acceptance criteria 1-7 from spec → all covered across Tasks 1-5 ✅

### Placeholder scan
No TBD / TODO / "implement later" / "similar to" / "add appropriate" found. Every step has complete code.

### Type consistency
- `aggregate_evidence_breakdown(sentiment_articles, original_articles) -> list[dict[str, Any]]` — same in Task 2 (producer) and Task 4 (consumer) ✅
- `EvidenceBreakdownItem` fields: `source, title, direction, strength, credibility, rationale_zh` — same in Task 1 (schema) and Task 2 (aggregation output keys) ✅
- `analyze_event` `filtered_articles: list[dict[str, Any]] | None = None` — same in Task 4 signature and both call sites ✅
- `settings.EVIDENCE_BREAKDOWN_ENABLED` — same name in Task 1 (config) and Task 4 (integration) ✅
