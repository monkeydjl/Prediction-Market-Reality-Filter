"""check_semantic_relevance.py
==============================
Local diagnostic for the opt-in embedding semantic-relevance path. Run it to
confirm that enabling embeddings actually rescues crypto / price-threshold
events that keyword relevance alone drops at the reality filter
(selected_count == 0 -> the candidate is skipped in discovery).

It runs a handful of real event questions through the SAME pipeline discovery
uses (collect_articles -> annotate_semantic_relevance -> filter_news_for_market)
and prints, per question:
  - selected_count / input_count           (did the reality filter keep anything?)
  - max keyword relevance vs max semantic relevance across the collected articles
  - the top kept article sources

Compare two runs:
  1. WITHOUT embeddings configured  -> baseline (keyword only)
  2. WITH embeddings configured     -> semantic should lift relevance and
     selected_count for the threshold questions.

This needs network (live RSS + Google News) and, for the semantic column, an
embedding provider. The default chat provider (DeepSeek) has NO embeddings
endpoint, so configure a provider that does, in backend/.env:

    EMBEDDING_MODEL=text-embedding-v3
    EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBEDDING_API_KEY=sk-...            # your DashScope key

  (OpenAI alternative: EMBEDDING_MODEL=text-embedding-3-small,
   EMBEDDING_BASE_URL empty, EMBEDDING_API_KEY=your-openai-key.)

Usage (from the backend/ directory):
    python scripts/check_semantic_relevance.py
    python scripts/check_semantic_relevance.py "Will Bitcoin reach $150,000 in 2026?"

Read-only: it does not write to the store, cache, or audit. Safe to run anytime.
"""

import asyncio
import os
import sys

# Make `app` importable when run as a plain file (sys.path[0] is scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.event_collection_service import collect_articles
from app.services.market_semantics_service import parse_market_semantics
from app.services.news_filter_service import filter_news_for_market
from app.services.semantic_relevance_service import annotate_semantic_relevance

# Price-threshold crypto questions are the canonical case that keyword relevance
# struggles with (the "$2,000" / "June" tokens rarely appear verbatim in news).
DEFAULT_QUESTIONS = [
    "Will Ethereum reach $2,000 in June?",
    "Will Bitcoin reach $100,000 by end of 2026?",
    "Will Solana reach $300 in 2026?",
]


def _max_field(articles, field):
    values = [
        a[field] for a in articles
        if isinstance(a.get(field), (int, float))
    ]
    return max(values) if values else None


async def _check_one(question: str) -> None:
    articles = await collect_articles(question)
    semantics = parse_market_semantics(question)
    # Annotate in place (no-op unless EMBEDDING_MODEL is set), then filter exactly
    # as discovery does.
    await annotate_semantic_relevance(question, articles, semantics)
    result = filter_news_for_market(market_question=question, articles=articles)

    summary = result["summary"]
    max_semantic = _max_field(articles, "semantic_relevance")
    max_keyword = _max_field(result["articles"], "relevance_score")

    print(f"\n{'=' * 70}\nQ: {question}")
    print(f"  entities          : {semantics.get('entities')}")
    print(f"  input_count       : {summary['input_count']}")
    print(f"  selected_count    : {summary['selected_count']}"
          f"   {'  <-- DROPPED (reality filter skips this event)' if summary['selected_count'] == 0 else ''}")
    print(f"  max keyword relev : {max_keyword}")
    print(f"  max semantic relev: {max_semantic}"
          f"   {'(embeddings OFF - keyword only)' if max_semantic is None else ''}")
    for a in result["articles"][:3]:
        print(f"    kept: rel={a.get('relevance_score')} "
              f"sem={a.get('semantic_relevance')} [{a.get('source')}] {a.get('title', '')[:54]}")


async def _main(questions: list[str]) -> None:
    enabled = bool(settings.EMBEDDING_MODEL)
    print(f"EMBEDDING_MODEL = {settings.EMBEDDING_MODEL or '(unset)'}  ->  "
          f"semantic relevance {'ENABLED' if enabled else 'DISABLED (keyword only)'}")
    if not enabled:
        print("Tip: set EMBEDDING_MODEL/BASE_URL/API_KEY in backend/.env to test "
              "the semantic column (see this file's docstring).")
    for question in questions:
        try:
            await _check_one(question)
        except Exception as exc:  # noqa: BLE001 - diagnostic, keep going
            print(f"\nQ: {question}\n  ERROR: {exc}")


if __name__ == "__main__":
    args = [q for q in sys.argv[1:] if q.strip()]
    asyncio.run(_main(args or DEFAULT_QUESTIONS))
