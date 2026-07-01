"""A/B feature-flag impact CLI (Plan 5 §1.5).

Quantifies how much each Phase overlay flips the final direction when
toggled on. Reuses ``replay_record`` from the existing replay harness —
no new replay logic.

Usage:
    python -m scripts.analyze_feature_flag_impact [--sample-size N]
        [--event-ids id1,id2] [--compare all_off all_on]
        [--json report.json]

Output: an ASCII matrix of direction transitions (e.g. "YES -> WAIT: 17%")
showing the direction-change rate when the chosen phase is enabled vs
disabled.

The default comparison is ``all_off`` vs ``all_on`` (total system
impact). Use ``--compare`` to swap either side, e.g.
``--compare all_off current`` to measure against live settings.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# source_trust_registry_cli.py / review_queue_cli.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

from app.replay.config import ReplayConfig
from app.replay.runner import replay_record

_DIRECTIONS = ("YES", "NO", "WAIT", "AVOID")


def _load_records(
    event_ids: list[str] | None,
    sample_size: int | None,
) -> list[dict[str, Any]]:
    """Load event records from event_store. Unwraps the {event_id, record}
    envelope that event_store.list_all_events returns."""
    from app.memory.event_store import list_all_events
    entries = list_all_events()
    records = [e["record"] for e in entries if isinstance(e.get("record"), dict)]
    if event_ids:
        wanted = set(event_ids)
        records = [r for r in records if r.get("event_id") in wanted]
    if sample_size and len(records) > sample_size:
        random.seed(42)  # deterministic sampling for reproducibility
        records = random.sample(records, sample_size)
    return records


def _config_by_name(name: str) -> ReplayConfig:
    if name == "all_off":
        return ReplayConfig.preset_all_off()
    if name == "all_on":
        return ReplayConfig.preset_all_on()
    if name == "current":
        return ReplayConfig.preset_all_on()  # all None → use live settings
    if name == "llm_degraded":
        return ReplayConfig.preset_llm_degraded()
    raise ValueError(f"unknown config preset: {name!r}")


def _effective_direction(record: dict[str, Any]) -> str | None:
    return record.get("final_displayed_direction")


def _compute_direction_matrix(
    records: list[dict[str, Any]],
    cfg_a: ReplayConfig,
    cfg_b: ReplayConfig,
) -> dict[str, dict[str, int]]:
    """Run each record under cfg_a (off) and cfg_b (on), tally direction
    transitions into a matrix[prev_dir][cur_dir] = count."""
    matrix: dict[str, dict[str, int]] = {
        a: {b: 0 for b in _DIRECTIONS} for a in _DIRECTIONS
    }
    for record in records:
        replayed_a = replay_record(record, cfg_a)
        replayed_b = replay_record(record, cfg_b)
        dir_a = _effective_direction(replayed_a) or "WAIT"
        dir_b = _effective_direction(replayed_b) or "WAIT"
        if dir_a in matrix and dir_b in matrix[dir_a]:
            matrix[dir_a][dir_b] += 1
    return matrix


def _format_matrix(matrix: dict[str, dict[str, int]], total: int) -> str:
    """Render the matrix as an ASCII table with a change-rate summary."""
    lines: list[str] = []
    lines.append("[INFO] Direction transition matrix (rows = off, cols = on):")
    header = "        " + "  ".join(f"{d:>6}" for d in _DIRECTIONS)
    lines.append(header)
    for a in _DIRECTIONS:
        row = f"  {a:<4} " + "  ".join(f"{matrix[a][b]:>6}" for b in _DIRECTIONS)
        lines.append(row)
    # Change rate = (total - diagonal) / total.
    diagonal = sum(matrix[a][a] for a in _DIRECTIONS)
    changed = total - diagonal
    rate = (changed / total * 100.0) if total > 0 else 0.0
    lines.append("")
    lines.append(f"[INFO] Total events: {total}")
    lines.append(f"[INFO] Direction changed: {changed} ({rate:.1f}%)")
    lines.append(f"[INFO] Direction unchanged: {diagonal} ({100.0 - rate:.1f}%)")
    lines.append(f"[INFO] Change rate: {rate:.1f}% ({changed} of {total})")
    # Top transitions (excluding diagonal).
    transitions: list[tuple[str, str, int]] = []
    for a in _DIRECTIONS:
        for b in _DIRECTIONS:
            if a != b and matrix[a][b] > 0:
                transitions.append((a, b, matrix[a][b]))
    transitions.sort(key=lambda t: t[2], reverse=True)
    if transitions:
        lines.append("[INFO] Top transitions:")
        for a, b, n in transitions:
            pct = (n / total * 100.0) if total > 0 else 0.0
            lines.append(f"       {a} -> {b}: {n} ({pct:.1f}%)")
    return "\n".join(lines)


def _print(text: str) -> None:
    print(text, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analyze_feature_flag_impact",
        description="Quantify how much each Phase overlay flips the final "
                    "direction when toggled on (Plan 5 §1.5).",
    )
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Random sample N records (deterministic seed).")
    parser.add_argument("--event-ids", type=str, default=None,
                        help="Comma-separated event ids to restrict the run.")
    parser.add_argument("--compare", nargs=2,
                        default=["all_off", "all_on"],
                        metavar=("CONFIG_A", "CONFIG_B"),
                        help="Two config presets to compare "
                             "(all_off / all_on / current / llm_degraded). "
                             "Default: all_off all_on.")
    parser.add_argument("--json", type=str, default=None,
                        metavar="PATH",
                        help="Write a JSON report to this path.")
    args = parser.parse_args(argv)

    event_ids = None
    if args.event_ids:
        event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]

    records = _load_records(event_ids, args.sample_size)
    if not records:
        _print("[WARN] No records found. Exiting.")
        return 0

    _print(f"[INFO] Loaded {len(records)} records.")
    try:
        cfg_a = _config_by_name(args.compare[0])
        cfg_b = _config_by_name(args.compare[1])
    except ValueError as e:
        _print(f"[FAIL] {e}")
        return 2

    _print(f"[INFO] Comparing {args.compare[0]} vs {args.compare[1]}...")
    matrix = _compute_direction_matrix(records, cfg_a, cfg_b)
    report = _format_matrix(matrix, total=len(records))
    _print(report)

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "compare": [args.compare[0], args.compare[1]],
            "total": len(records),
            "matrix": matrix,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        _print(f"[OK] JSON report written to {out_path}")

    _print("[OK] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
