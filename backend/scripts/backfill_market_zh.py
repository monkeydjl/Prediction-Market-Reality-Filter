"""backfill_market_zh.py
========================
One-off backfill for already-stored events (event_store.json), complementing the
forward-only changes in the event sources + discovery flow:

1. Market URL (source.url) for prediction_market events that lack it:
   - Kalshi:     built offline from the event_ticker (source_id).
   - Polymarket: fetched live from the gamma API by market id (slug -> URL).
   Best-effort: a source whose URL can't be obtained is left unchanged (a future
   discovery scan captures it natively).

2. News translation: English evidence_items (title / summary) are translated to
   Simplified Chinese in place via translation_service.translate_articles, adding
   title_zh / summary_zh (the UI shows zh with the English original as fallback).

Records are saved back through the store (preserves first_seen + user tracking),
and the per-question event cache is cleared so stale English cached records are
not re-served on the next scan.

Usage (from the backend/ directory):
    python scripts/backfill_market_zh.py
    python scripts/backfill_market_zh.py --no-network    # Kalshi URLs + translation only
    python scripts/backfill_market_zh.py --no-translate  # URLs only
    python scripts/backfill_market_zh.py --limit 10      # only the first N events
Uses the configured LLM Gateway for translation; network is needed only for
Polymarket URLs. URL lookups degrade gracefully when unavailable.
"""

import argparse
import asyncio
import os
import sys

# Make `app` importable when run as a plain file (sys.path[0] is scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.memory.event_store import list_all_events, save_events  # noqa: E402
from app.services.llm_gateway_service import has_configured_llm_route  # noqa: E402
from app.services.translation_service import translate_articles  # noqa: E402


def _kalshi_url(ticker: str) -> str:
    return f"https://kalshi.com/markets/{ticker.lower()}" if ticker else ""


async def _polymarket_url(client: httpx.AsyncClient, market_id: str) -> str:
    if not market_id:
        return ""
    try:
        resp = await client.get(
            "https://gamma-api.polymarket.com/markets", params={"id": market_id}
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - best effort, skip on failure
        print(f"    ! polymarket {market_id}: {exc}")
        return ""
    if isinstance(data, list):
        item = data[0] if data else {}
    elif isinstance(data, dict):
        item = data
    else:
        item = {}
    slug = str(item.get("slug") or "")
    return f"https://polymarket.com/event/{slug}" if slug else ""


async def _resolve_url(
    client: httpx.AsyncClient, source: dict, use_network: bool
) -> str:
    platform = str(source.get("platform") or "").lower()
    source_id = str(source.get("source_id") or "")
    if "kalshi" in platform:
        return _kalshi_url(source_id)
    if not use_network:
        return ""
    if "polymarket" in platform:
        return await _polymarket_url(client, source_id)
    return ""


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill market URLs + Chinese news on stored events."
    )
    parser.add_argument("--no-network", action="store_true",
                        help="Skip Polymarket API lookups (Kalshi URLs only)")
    parser.add_argument("--no-translate", action="store_true",
                        help="Skip news translation (URLs only)")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N events")
    args = parser.parse_args()

    entries = list_all_events()
    if args.limit > 0:
        entries = entries[: args.limit]
    if not entries:
        print("No stored events.")
        return

    if args.no_translate:
        print("Translation disabled (--no-translate).")
    elif not has_configured_llm_route("translation"):
        print("! No configured LLM Gateway translation route - skipping translation, URLs only.")
        args.no_translate = True

    updated: list[dict] = []
    url_added = 0
    translated_events = 0
    translated_items = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for entry in entries:
            record = entry.get("record") or {}
            if not record.get("event_id"):
                continue
            changed = False

            source = record.get("source") or {}
            if source.get("type") == "prediction_market" and not source.get("url"):
                url = await _resolve_url(client, source, not args.no_network)
                if url:
                    source["url"] = url
                    record["source"] = source
                    url_added += 1
                    changed = True

            if not args.no_translate:
                items = record.get("evidence_items") or []
                pending = [
                    it for it in items
                    if (it.get("title") or "").strip() and not (it.get("title_zh") or "").strip()
                ]
                if pending:
                    await translate_articles(items)  # mutates in place
                    got = sum(1 for it in items if (it.get("title_zh") or "").strip())
                    if got:
                        translated_events += 1
                        translated_items += got
                        changed = True

            if changed:
                updated.append(record)
                label = (record.get("event_title_zh") or record.get("event_title", ""))[:50]
                print(f"  updated {record.get('event_id')}: {label}")

    if updated:
        save_events(updated)
        print(f"Saved {len(updated)} record(s) back to the store.")
    else:
        print("Nothing to update.")
    print(
        f"URLs added: {url_added} | "
        f"events translated: {translated_events} | items translated: {translated_items}"
    )

    cache_path = os.path.abspath(settings.EVENT_CACHE_FILE)
    if os.path.exists(cache_path):
        os.remove(cache_path)
        print(f"Cleared event cache: {cache_path}")


if __name__ == "__main__":
    asyncio.run(main())
