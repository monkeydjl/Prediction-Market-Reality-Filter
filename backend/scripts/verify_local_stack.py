#!/usr/bin/env python3
"""Local smoke checks for PMRF Sports + flags (no long Optuna runs).

Usage (from repo root or backend/):
  python backend/scripts/verify_local_stack.py
  python backend/scripts/verify_local_stack.py --base http://127.0.0.1:8000

Exits 0 if core health is ok; prints a table of endpoint status / flag hints.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


CHECKS: list[tuple[str, str, str | None]] = [
    # path, method, optional flag hint when 503
    ("/api/health", "GET", None),
    ("/metrics", "GET", None),
    ("/api/predictions/engines", "GET", "KERNEL_PREDICTION_ENABLED"),
    ("/api/betting/catalog", "GET", None),
    ("/api/sport-edges/discrepancies?limit=5", "GET", "PHASE7_EDGE_DETECTOR_ENABLED"),
    (
        "/api/sport-recommendations/open?limit=5",
        "GET",
        "PHASE7_SPORT_RECOMMENDATION_ENABLED",
    ),
    (
        "/api/sport-settlements/history?limit=5",
        "GET",
        "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED",
    ),
    (
        "/api/sport-optimization/params",
        "GET",
        "PHASE9_ACCURACY_SPRINT_ENABLED",
    ),
    (
        "/api/sport-markets/pending?limit=5",
        "GET",
        "PHASE7_SPORT_MARKET_BRIDGE_ENABLED",
    ),
    # Event loop (EIP) — should work without sports flags
    ("/api/events/decisions/open?limit=5", "GET", None),
    ("/api/events/calibration", "GET", None),
    # Quality / drift (always readable; alert *dispatch* is flag-gated)
    ("/api/quality-metrics/summary", "GET", None),
    ("/api/quality-metrics/drift", "GET", None),
    ("/api/quality-metrics/alerts", "GET", None),
]


def fetch(base: str, path: str, method: str = "GET") -> tuple[int, Any]:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body) if body else None
            except json.JSONDecodeError:
                data = body[:200]
            return resp.status, data
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = str(e)
        return e.code, detail
    except Exception as e:
        return 0, str(e)


def main() -> int:
    parser = argparse.ArgumentParser(description="PMRF local stack smoke check")
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:8000",
        help="API base URL (default http://127.0.0.1:8000)",
    )
    args = parser.parse_args()
    base = args.base

    print(f"Checking {base} ...\n")
    print(f"{'PATH':<48} {'STATUS':<8} NOTE")
    print("-" * 90)

    health_ok = False
    for path, method, flag in CHECKS:
        status, data = fetch(base, path, method)
        note = ""
        if status == 200:
            note = "ok"
            if path.endswith("/health"):
                health_ok = True
                if isinstance(data, dict):
                    note = f"ok status={data.get('status', data)}"
        elif status == 503:
            note = f"disabled → set {flag}=true" if flag else "503"
        elif status == 0:
            note = f"connection error: {data}"
        else:
            note = f"{data!r}"[:60]

        print(f"{path:<48} {status:<8} {note}")

    print()
    if not health_ok:
        print(
            "FAIL: /api/health not reachable. Start backend first, e.g.\n"
            "  cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000",
        )
        return 1

    print(
        "Hints:\n"
        "  - Enable Kernel + engines: KERNEL_PREDICTION_ENABLED, "
        "FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED, ...\n"
        "  - Edge/Recs/Settlement: PHASE7_EDGE_DETECTOR_ENABLED, "
        "PHASE7_SPORT_RECOMMENDATION_ENABLED, "
        "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED\n"
        "  - Realtime WS: PHASE10_REALTIME_PUSH_ENABLED "
        "(dev UI uses ws://localhost:8000)\n"
        "  - Phase 9: PHASE9_ACCURACY_SPRINT_ENABLED + "
        "scripts/run_phase9_optimize.py after ingest\n",
    )
    print("DONE (health ok). Review 503 rows above and enable flags as needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
