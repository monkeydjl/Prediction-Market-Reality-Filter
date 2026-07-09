"""Backfill missing/unknown event categories in the local event store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.event_category_service import backfill_event_categories


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist category updates. Default is dry-run.",
    )
    parser.add_argument(
        "--limit-samples",
        type=int,
        default=20,
        help="Number of update samples to print.",
    )
    args = parser.parse_args()

    result = backfill_event_categories(dry_run=not args.write)
    summary = {
        key: result[key]
        for key in (
            "status",
            "dry_run",
            "checked_count",
            "updated_count",
            "skipped_known_count",
            "skipped_still_unknown_count",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(result["updates"][: max(args.limit_samples, 0)], ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
