"""translate_events_zh.py
=========================
One-off backfill: make already-stored event records Chinese.

Why this exists
---------------
The Chinese-output changes (LLM prompt + code templates) only affect events
analyzed *after* the change. Events already in event_store.json stay English
until re-touched. This script translates them in place WITHOUT re-running the
analysis, so probabilities, scores and the event set are preserved exactly.

What it does per stored event
-----------------------------
1. Deterministically regenerates the templated report fields (headline,
   probability_assessment, recommended_action) in Chinese from the stored
   numbers - no LLM, always runs.
2. LLM-translates the free-text fields that are still English:
   event_title -> event_title_zh, event_summary, intelligence_report.why_it_matters.
   (event_title itself is left untouched: it is the upstream market question and
   is used as the stable key for dedup / similarity / event_id.)
3. Saves the record back via the normal store (preserves first_seen and any
   user tracking decision).

Finally it clears the per-question event cache so the next discovery scan does
not serve stale English cached records.

Usage (from the backend/ directory):
    python scripts/translate_events_zh.py
    python scripts/translate_events_zh.py --limit 20      # only the first N
    python scripts/translate_events_zh.py --keep-cache    # don't clear cache
Needs OPENAI_API_KEY in backend/.env for the translation step; without it the
templated fields are still regenerated in Chinese and text is left as-is.
"""

import argparse
import asyncio
import os
import sys

# Make `app` importable when run as a plain file (sys.path[0] is scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Console output is UTF-8 safe (Windows consoles default to GBK and choke on
# accented chars in source titles / on Chinese output).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.core.config import settings  # noqa: E402
from app.memory.event_store import list_all_events, save_events  # noqa: E402
from app.services.event_intelligence_service import (  # noqa: E402
    build_headline,
    build_probability_assessment,
    recommended_action,
)
from app.services.probability_engine_service import get_client  # noqa: E402
from app.services.translation_service import translate_fields  # noqa: E402


def _looks_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in (text or ""))


def _num(value, fallback=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _regen_templates(record: dict) -> None:
    """Rebuild the deterministic templated report fields in Chinese."""
    prob = record.get("probability") or {}
    baseline = _num(prob.get("baseline"))
    estimated = _num(prob.get("estimated"))
    change = _num(prob.get("change"))
    trust = int(_num((record.get("credibility") or {}).get("score")))
    impact = int(_num((record.get("impact") or {}).get("score")))
    # Prefer the Chinese title for the headline; fall back to the raw question.
    headline_title = (record.get("event_title_zh") or "").strip() or record.get(
        "event_title", ""
    )
    report = dict(record.get("intelligence_report") or {})
    report["headline"] = build_headline(headline_title, change, trust, impact)
    report["probability_assessment"] = build_probability_assessment(
        baseline, estimated, trust
    )
    report["recommended_action"] = recommended_action(trust, impact, change)
    record["intelligence_report"] = report


async def _process(client, record: dict) -> None:
    title = record.get("event_title", "") or ""
    summary = record.get("event_summary", "") or ""
    report = record.get("intelligence_report") or {}
    why = report.get("why_it_matters", "") or ""

    payload: dict[str, str] = {}
    if not (record.get("event_title_zh") or "").strip() and title and not _looks_chinese(title):
        payload["title_zh"] = title
    if summary and not _looks_chinese(summary):
        payload["summary"] = summary
    if why and not _looks_chinese(why):
        payload["why"] = why

    if payload and client is not None:
        translated = await translate_fields(payload, client=client)
        if translated.get("title_zh"):
            record["event_title_zh"] = translated["title_zh"][:300]
        if translated.get("summary"):
            record["event_summary"] = translated["summary"][:500]
        if translated.get("why"):
            report = dict(report)
            report["why_it_matters"] = translated["why"][:500]
            record["intelligence_report"] = report

    # Always regenerate the templated fields in Chinese (uses event_title_zh now).
    _regen_templates(record)


def _clear_cache() -> None:
    path = os.path.abspath(settings.EVENT_CACHE_FILE)
    if os.path.exists(path):
        os.remove(path)
        print(f"Cleared event cache: {path}")
    else:
        print("Event cache already empty.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Translate stored events to Chinese.")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N events")
    parser.add_argument("--keep-cache", action="store_true", help="Do not clear cache")
    args = parser.parse_args()

    entries = list_all_events()
    if args.limit > 0:
        entries = entries[: args.limit]
    if not entries:
        print("No stored events to translate.")
        return

    client = None
    if settings.OPENAI_API_KEY:
        client = get_client()
    else:
        print("! OPENAI_API_KEY not set - regenerating templates only, text kept as-is.")

    semaphore = asyncio.Semaphore(4)
    updated: list[dict] = []

    async def run(entry):
        record = entry.get("record") or {}
        if not record.get("event_id"):
            return
        async with semaphore:
            await _process(client, record)
        updated.append(record)
        print(f"  done: {record.get('event_id')}  {record.get('event_title','')[:60]}")

    print(f"Translating {len(entries)} stored event(s)...")
    await asyncio.gather(*(run(entry) for entry in entries))

    if updated:
        save_events(updated)
        print(f"Saved {len(updated)} record(s) back to the store.")
    if not args.keep_cache:
        _clear_cache()
    print("Backfill complete.")


if __name__ == "__main__":
    asyncio.run(main())
