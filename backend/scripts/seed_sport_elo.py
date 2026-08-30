#!/usr/bin/env python3
"""Backfill KernelMatchResult from fixtures and seed KernelEloRating.

Usage (from backend/):
  python scripts/seed_sport_elo.py --all
  python scripts/seed_sport_elo.py --sport nba --backfill-only
  python scripts/seed_sport_elo.py --sport mlb --seed-only
  python scripts/seed_sport_elo.py --sport epl --backfill-only

Requires kernel DB with fixtures already synced (schedule sync).

Football competitions (epl / laliga / seriea / bundesliga / ligue1 / ucl) are
backfill-only: they were added in P1-E9 because their fixtures carried scores
that no kernel_match_results row ever received. Elo seeding stays binary-only --
the replay scores a game as home_score > away_score, and football Elo already
comes from ClubElo as a measured source.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill match results + seed self-computed Elo",
    )
    # Choices come from the ingestor's declaration so the CLI cannot drift from
    # what the backfill actually accepts. Football codes are backfill-only:
    # --seed-only against one of them is refused by seed_elo_ratings.
    from app.services.historical_data_ingestor import BACKFILLABLE_COMPETITIONS

    parser.add_argument(
        "--sport",
        default="all",
        choices=[*sorted(BACKFILLABLE_COMPETITIONS), "all"],
        help=(
            "Competition scope (default: all). Football codes support "
            "--backfill-only; Elo seeding stays binary-only."
        ),
    )
    parser.add_argument(
        "--backfill-only",
        action="store_true",
        help="Only copy fixture scores → kernel_match_results",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only seed kernel_elo_ratings from existing results",
    )
    args = parser.parse_args()

    from app.kernel.kernel_db import init_kernel_db
    from app.services.historical_data_ingestor import HistoricalDataIngestor

    init_kernel_db()
    ingestor = HistoricalDataIngestor()
    sport = None if args.sport == "all" else args.sport
    summary: dict = {}

    if not args.seed_only:
        bf = ingestor.backfill_results_from_fixtures(sport=sport)
        summary["backfill"] = bf
        print(f"[backfill] new={bf.get('results')} updated={bf.get('updated')}")
        print(json.dumps(bf.get("sports") or {}, indent=2, default=str))
        if bf.get("errors"):
            print(f"[backfill] errors: {bf['errors']}")

    if not args.backfill_only:
        seed = ingestor.seed_elo_ratings(sport=sport)
        summary["seed"] = seed
        print(f"[seed] teams={seed.get('teams')}")
        print(json.dumps(seed.get("sports") or {}, indent=2, default=str))
        if seed.get("errors"):
            print(f"[seed] errors: {seed['errors']}")

    # Quick verification counts
    from app.kernel.kernel_db import (
        KernelEloRating,
        KernelMatchResult,
        get_kernel_session,
    )
    from sqlalchemy import func

    session = get_kernel_session()
    try:
        results_n = session.query(func.count(KernelMatchResult.match_id)).scalar()
        elo_n = session.query(func.count(KernelEloRating.team_name)).scalar()
        summary["verify"] = {"results": results_n, "elo_rows": elo_n}
        print(f"[verify] kernel_match_results={results_n} kernel_elo_ratings={elo_n}")
    finally:
        session.close()

    print("--- summary ---")
    print(json.dumps(summary, indent=2, default=str))
    errors = []
    if summary.get("backfill", {}).get("errors"):
        errors.extend(summary["backfill"]["errors"])
    if summary.get("seed", {}).get("errors"):
        errors.extend(summary["seed"]["errors"])
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
