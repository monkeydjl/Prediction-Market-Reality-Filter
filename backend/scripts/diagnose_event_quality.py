"""Quality Diagnosis CLI (Spec §4.3, P2 #25).

Single-event debugging CLI: decomposes an event into its 6 quality layers
(Decision Quality, Market Quality, Prediction Calibration, Source
Reliability, LLM Telemetry, Execution Quality) plus guardrail state and
final direction. Optional --json and --replay flags.

Pure read-only: no writes, no LLM calls, no network fetches.

Usage:
    python -m scripts.diagnose_event_quality EVENT_ID [--json] [--replay]
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import sys
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# source_trust_registry_cli.py / review_queue_cli.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _load_event(event_id: str) -> dict[str, Any] | None:
    """Load event from event_store. Returns the store entry (which contains
    a ``record`` key) or None if not found. Caller must extract
    ``entry.get("record")`` to get the event record dict."""
    from app.memory.event_store import get_event
    return get_event(event_id)


def _extract_phase_data(record: dict[str, Any]) -> dict[str, Any]:
    """Extract 6 phases + guardrail + final direction from a record.

    Returns a dict with keys:
      - event_id, event_title
      - phases: {decision_quality, market_quality, prediction_calibration,
                 source_reliability, llm_telemetry, execution_quality}
        (each value is a dict of fields, or None if overlay absent)
      - guardrails: {fired_rules: list}
      - final_direction: str | None

    Field rename per §8.1: ``max_safe_position_size`` → ``max_safe_size``
    (display-only; reads the original key, emits the renamed key).
    """
    phases: dict[str, dict[str, Any] | None] = {}

    # Phase 1: Decision Quality
    dq = record.get("decision_quality")
    if isinstance(dq, dict):
        phases["decision_quality"] = {
            "evidence_strength": dq.get("evidence_strength"),
            "conflict_score": dq.get("conflict_score"),
            "downgrade_reason": dq.get("downgrade_reason"),
            "displayed_direction": dq.get("displayed_direction"),
        }
    else:
        phases["decision_quality"] = None

    # Phase 2: Market Quality
    mq = record.get("market_quality")
    if isinstance(mq, dict):
        phases["market_quality"] = {
            "degraded": mq.get("degraded"),
            "degrade_reason": mq.get("degrade_reason"),
            "wide_spread_flag": mq.get("wide_spread_flag"),
            "low_liquidity_flag": mq.get("low_liquidity_flag"),
        }
    else:
        phases["market_quality"] = None

    # Phase 3: Prediction Calibration (derived from actionable_recommendation
    # + probability, not a top-level overlay key)
    rec = record.get("actionable_recommendation")
    if isinstance(rec, dict):
        cal_status = rec.get("calibration_status")
        edge = rec.get("edge")
        # edge_bucket derived from edge value (e.g. 12.0 → "10-20")
        edge_bucket = None
        if isinstance(edge, (int, float)):
            edge_bucket = f"{int(edge // 10 * 10)}-{int(edge // 10 * 10 + 10)}"
        # direction_correct: True if recommendation direction matches
        # final_displayed_direction, False otherwise. None if no final.
        final_dir = record.get("final_displayed_direction")
        rec_dir = rec.get("direction")
        direction_correct = (rec_dir == final_dir) if final_dir else None
        phases["prediction_calibration"] = {
            "snapshot_recommendation": rec_dir,
            "calibration_status": cal_status,
            "edge_bucket": edge_bucket,
            "direction_correct": direction_correct,
        }
    else:
        phases["prediction_calibration"] = None

    # Phase 4: Source Reliability
    sr = record.get("source_reliability")
    if isinstance(sr, dict):
        phases["source_reliability"] = {
            "overall_score": sr.get("overall_score"),
            "source_count": sr.get("source_count"),
            "domain_diversity": sr.get("domain_diversity"),
        }
    else:
        phases["source_reliability"] = None

    # Phase 5: LLM Telemetry
    lt = record.get("llm_telemetry")
    if isinstance(lt, dict):
        phases["llm_telemetry"] = {
            "degraded_mode": lt.get("degraded_mode"),
            "total_tokens": lt.get("total_tokens"),
            "estimated_token_cost": lt.get("estimated_token_cost"),
            "analysis_quality": lt.get("analysis_quality"),
        }
    else:
        phases["llm_telemetry"] = None

    # Phase 6: Execution Quality
    eq = record.get("execution_quality")
    if isinstance(eq, dict):
        # max_safe_position_size → max_safe_size (§8.1 vocabulary lock)
        phases["execution_quality"] = {
            "executable": eq.get("executable"),
            "estimated_slippage_pct": eq.get("estimated_slippage_pct"),
            "stale_price_flag": eq.get("stale_price_flag"),
            "max_safe_size": eq.get("max_safe_position_size"),
        }
    else:
        phases["execution_quality"] = None

    # Guardrails
    fired = record.get("guardrail_fired")
    guardrails = {"fired_rules": list(fired) if isinstance(fired, list) else []}

    return {
        "event_id": record.get("event_id"),
        "event_title": record.get("event_title"),
        "phases": phases,
        "guardrails": guardrails,
        "final_direction": record.get("final_displayed_direction"),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Diagnose event quality (Spec §4.3). Single-event "
                    "6-layer decomposition + guardrail + final direction."
    )
    parser.add_argument("event_id", help="Event ID to diagnose")
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    parser.add_argument(
        "--replay", action="store_true",
        help="Additionally run replay (all_on vs all_off) direction comparison",
    )
    args = parser.parse_args(argv)

    entry = _load_event(args.event_id)
    if entry is None:
        print(f"Error: event '{args.event_id}' not found in event_store",
              file=sys.stderr)
        return 1

    record = entry.get("record")
    if not isinstance(record, dict):
        print(f"Error: event '{args.event_id}' has no valid record",
              file=sys.stderr)
        return 2

    data = _extract_phase_data(record)

    # Rendering + replay added in later tasks
    _print(f"[diagnose_event_quality] event_id={data['event_id']} "
           f"(rendering not yet implemented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
