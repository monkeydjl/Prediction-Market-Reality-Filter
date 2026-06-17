"""check_crypto_candidates.py
=============================
Local diagnostic for the upstream candidate-source layer.

The hypothesis from prior investigation: crypto events are NOT missing because
of the reality filter (the evidence layer already returns selected_count=6 for
crypto questions). They are missing because crypto markets never reach the
candidate pool in the first place - the market sources rank by volume / score,
and geopolitics dominates the top of those rankings.

This script hits each market source's raw fetch + its eligibility filter and
prints, per source:
  - how many raw candidates came back
  - how many survive the source's own filter
  - how many of the SURVIVORS are crypto (bitcoin/btc/crypto/ethereum/eth/solana)
  - the top surviving questions (so you can see what crowds crypto out)

It calls the source adapters directly (the SAME fetch_*_events the discovery
pipeline uses), so the numbers reflect exactly what discover_events sees as
candidates BEFORE dedupe / reality filter / LLM analysis.

Needs network to reach Polymarket / Manifold / Kalshi. Read-only: does not
write to the store, cache, or audit. Safe to run anytime.

Usage (from the backend/ directory):
    python scripts/check_crypto_candidates.py
    python scripts/check_crypto_candidates.py 20     # larger candidate window
"""

import asyncio
import os
import sys

# Make `app` importable when run as a plain file (sys.path[0] is scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CRYPTO_KEYWORDS = (
    "bitcoin", "btc", "crypto", "ethereum", "eth", "solana", "sol",
)


def _is_crypto(question: str) -> bool:
    q = (question or "").lower()
    return any(kw in q for kw in CRYPTO_KEYWORDS)


async def _probe_manifold(limit: int) -> None:
    from app.services.manifold_event_source import (
        fetch_candidate_events,
        _fetch_raw_markets,
        _is_eligible,
    )

    print("\n[Manifold]  (sort=score, filter=open BINARY)")
    try:
        raw = await _fetch_raw_markets(limit)
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"  RAW FETCH FAILED: {exc}")
        return
    eligible = [m for m in raw if _is_eligible(m)]
    candidates = await fetch_candidate_events(limit)
    _print_block("raw", raw, key=lambda m: m.get("question", ""))
    _print_block("eligible", eligible, key=lambda m: m.get("question", ""))
    _print_crypto(candidates, key=lambda c: c.get("question", ""))


async def _probe_kalshi(limit: int) -> None:
    from app.services.kalshi_event_source import (
        fetch_candidate_events,
        _fetch_raw_events,
        _is_eligible,
    )

    print("\n[Kalshi]  (status=open, single-leg events only)")
    try:
        raw = await _fetch_raw_events(limit)
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"  RAW FETCH FAILED: {exc}")
        return
    eligible = [e for e in raw if _is_eligible(e)]
    candidates = await fetch_candidate_events(limit)
    _print_block("raw", raw, key=lambda e: e.get("title", ""))
    _print_block("eligible", eligible, key=lambda e: e.get("title", ""))
    _print_crypto(candidates, key=lambda c: c.get("question", ""))


async def _probe_polymarket(limit: int) -> None:
    from app.services.polymarket_service import fetch_markets
    from app.services.polymarket_event_source import fetch_candidate_events

    print("\n[Polymarket]  (order=volume desc, ALLOWED_KEYWORDS gate)")
    # fetch_candidate_events over-fetches and applies market_filter_service too.
    # Probe both layers: the raw keyword-filtered fetch, and the full candidate
    # build (which adds liquidity/volume/certainty filtering + priority sort).
    try:
        raw_markets = await fetch_markets(limit=min(max(limit * 3, limit), 100))
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"  RAW FETCH FAILED: {exc}")
        return
    _print_block("raw (keyword-gated)", raw_markets, key=lambda m: m.question)
    _print_crypto(raw_markets, key=lambda m: m.question, label="raw")
    candidates = await fetch_candidate_events(limit)
    _print_crypto(candidates, key=lambda c: c.get("question", ""), label="candidate")


def _print_block(label: str, items: list, key) -> None:
    total = len(items)
    crypto = [key(i) for i in items if _is_crypto(key(i))]
    print(f"  {label:24s}: total={total:3d}  crypto={len(crypto)}")
    for q in [key(i) for i in items][:8]:
        mark = " *CRYPTO" if _is_crypto(q) else ""
        print(f"      - {(q or '')[:70]}{mark}")


def _print_crypto(candidates: list, key, label: str = "candidate") -> None:
    crypto = [key(c) for c in candidates if _is_crypto(key(c))]
    print(f"  {label} crypto survivors: {len(crypto)} / {len(candidates)}")
    for q in crypto[:8]:
        print(f"      + {q[:70]}")


async def _main(limit: int) -> None:
    print(f"Candidate-source probe  (limit per source = {limit})")
    print("Each source's raw fetch -> its own eligibility filter -> crypto count.")
    await _probe_polymarket(limit)
    await _probe_manifold(limit)
    await _probe_kalshi(limit)
    print("\nDone. If crypto survivors are 0 across sources, the shortage is")
    print("upstream in ranking (volume/score), not in the evidence layer.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip()]
    try:
        limit = max(1, int(args[0])) if args else 10
    except ValueError:
        limit = 10
    asyncio.run(_main(limit))
