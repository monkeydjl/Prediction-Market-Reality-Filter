"""Domain reliability CLI (LATER #2).

Query and rebuild per-domain reliability statistics from resolved events.

Usage:
    python -m scripts.domain_reliability_cli list
    python -m scripts.domain_reliability_cli list --json
    python -m scripts.domain_reliability_cli rebuild
    python -m scripts.domain_reliability_cli rebuild --dry-run
    python -m scripts.domain_reliability_cli rebuild --limit 50
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# UTF-8 stdout for Windows GBK console safety.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):
    pass

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _render_text(stats: list[dict]) -> str:
    lines: list[str] = []
    domains = {s["domain"] for s in stats}
    lines.append(f"Domain Reliability Report - {len(domains)} domains")
    lines.append("")
    lines.append(
        f"{'Domain':<20} {'Category':<20} {'Samples':>7} {'Correct':>7} "
        f"{'Wrong':>5} {'Reliability':>11} {'Avg Cred':>8}"
    )
    for s in sorted(stats, key=lambda x: (x["domain"], x["category"])):
        score = f"{s['reliability_score']:.1%}" if s["reliability_score"] is not None else "N/A"
        cred = f"{s['credibility_avg']:.2f}" if s["credibility_avg"] is not None else "N/A"
        lines.append(
            f"{s['domain']:<20} {s['category']:<20} {s['sample_count']:>7} "
            f"{s['correct_count']:>7} {s['wrong_count']:>5} {score:>11} {cred:>8}"
        )
    lines.append("")
    total_samples = sum(s["sample_count"] for s in stats)
    avg_rel = sum(s["reliability_score"] for s in stats if s["reliability_score"] is not None)
    n_rel = sum(1 for s in stats if s["reliability_score"] is not None)
    avg_str = f"{avg_rel / n_rel:.1%}" if n_rel > 0 else "N/A"
    lines.append(f"Summary: {len(domains)} domains, {total_samples} total samples, {avg_str} avg reliability.")
    return "\n".join(lines)


def _summarize_records(records: list[dict]) -> dict[str, int]:
    from app.services.domain_reliability_service import (
        attribute_evidence,
        compute_reliability_stats,
    )

    attributions: list[dict] = []
    valid_events = 0
    for record in records:
        attrs = attribute_evidence(record)
        if attrs:
            valid_events += 1
            attributions.extend(attrs)

    stats = compute_reliability_stats(attributions)
    domains = {domain for domain, _category in stats}
    return {
        "processed_events": len(records),
        "valid_events": valid_events,
        "rows": len(stats) + len(domains),
        "domains": len(domains),
    }


def _render_rebuild_summary(summary: dict[str, int], *, wrote: bool) -> str:
    action = "Wrote" if wrote else "Would write"
    return "\n".join([
        "Rebuilding domain reliability from event_store...",
        (
            f"Processed {summary['processed_events']} resolved events, "
            f"{summary['valid_events']} with valid attribution."
        ),
        (
            f"{action} {summary['rows']} domain/category rows "
            f"({summary['domains']} domains)."
        ),
        "Done." if wrote else "Dry run.",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="domain_reliability")
    subparsers = parser.add_subparsers(dest="command")

    sp_list = subparsers.add_parser("list")
    sp_list.add_argument("--domain", type=str, default=None)
    sp_list.add_argument("--category", type=str, default=None)
    sp_list.add_argument("--min-samples", type=int, default=0)
    sp_list.add_argument("--json", action="store_true")

    sp_rebuild = subparsers.add_parser("rebuild")
    sp_rebuild.add_argument("--limit", type=int, default=None,
                            help="Preview only: process first N events without writing to DB")
    sp_rebuild.add_argument("--dry-run", action="store_true",
                            help="Compute and print stats without writing to DB")

    args = parser.parse_args(argv)

    from app.memory.domain_reliability_store import get_stats, rebuild_from_records
    from app.memory import event_store

    if args.command == "list":
        stats = get_stats(domain=args.domain, category=args.category,
                          min_samples=args.min_samples)
        if args.json:
            domains = {s["domain"] for s in stats}
            payload = {
                "domains": stats,
                "total_domains": len(domains),
                "total_rows": len(stats),
            }
            _print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        else:
            _print(_render_text(stats))
        return 0

    elif args.command == "rebuild":
        entries = event_store.list_resolved_events()
        records = [e.get("record", {}) for e in entries if isinstance(e.get("record"), dict)]
        selected_records = records[:args.limit] if args.limit is not None else records
        summary = _summarize_records(selected_records)

        if args.limit is not None:
            records = records[:args.limit]
            _print(_render_rebuild_summary(summary, wrote=False))
            # Preview only — do NOT write
            return 0

        if args.dry_run:
            _print(_render_rebuild_summary(summary, wrote=False))
            return 0

        rebuild_from_records(records)
        _print(_render_rebuild_summary(summary, wrote=True))
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
