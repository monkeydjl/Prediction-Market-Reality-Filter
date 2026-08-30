"""Read-only census of fixtures that kicked off and never got a result (E15).

``fetch_outcome`` joins ``kernel_match_results``, so a fixture with no result row
can never settle a prediction: no calibration, no ``direction_accuracy``, no
engine score. P1-E9 (#73) fixed the *writer* for football, but nothing reports
the other way this state arises -- **a kickoff passes and the result never
lands**, because the schedule sync stalled, the feed dropped the game, or the
fixture reached ``status="finished"`` with no score at all (in which case the
backfill, which filters on scores present, skips it forever).

Measured on the live kernel DB on 2026-08-30, before this module existed:
**584** fixtures were past kickoff with no result row -- mlb 511, laliga 25,
ligue1 17, epl 15, seriea 15, nhl 1 -- of which **96 were more than 30 days
overdue** and the oldest was 699 days (``mlb-746577``, ``status="finished"``,
no score). One real prediction was riding on one of them.

Aggregate counts only, so this is safe on the same read-only footing as the rest
of ``quality_metrics``: no match ids, no team names, no kickoff timestamps.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

#: Age buckets, in whole days overdue. Ordered, and the last one is open-ended.
#: Read as "a fixture this many days past kickoff with still no result".
AGE_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("under_1d", 1),
    ("1_2d", 3),
    ("3_7d", 8),
    ("8_30d", 31),
    ("over_30d", None),
)

#: Status a competition gets when it has no fixtures in the window at all.
#: Distinct from ``ok`` on purpose: "we never had anything to check" is not
#: "we checked and it was clean". Same absence convention the optional
#: discovery sources use.
STATUS_NO_DATA = "no_data"
STATUS_OK = "ok"
STATUS_STALE = "stale"


def _bucket_for(days: int) -> str:
    for name, upper in AGE_BUCKETS:
        if upper is None or days < upper:
            return name
    raise AssertionError("AGE_BUCKETS must end with an open-ended bucket")


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def fixture_freshness_summary(now: dt.datetime | None = None) -> dict[str, Any]:
    """Count past-due-unsettled fixtures per competition.

    Args:
        now: reference instant, defaulting to UTC now. Injected so a test can
            place fixtures at known ages instead of sleeping.

    Returns:
        ``{"generated_at": iso, "total_fixtures": int, "past_due_unsettled":
        int, "oldest_overdue_days": int | None, "buckets": {...},
        "competitions": {code: {...}}}``. A competition present in the kernel
        with nothing past due reports ``status="ok"``; one with no fixtures at
        all reports ``status="no_data"``.
    """
    from app.kernel import kernel_db
    from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult
    from app.services.historical_data_ingestor import BACKFILLABLE_COMPETITIONS

    reference = _as_utc(now) or dt.datetime.now(dt.timezone.utc)

    # Closed in a finally, like kernel_db.get_latest_prediction: the factory
    # hands out a new Session per call and close_kernel_db only disposes the
    # engine, so an unclosed session keeps a connection checked out.
    session = kernel_db.get_kernel_session()
    try:
        settled_ids = {
            row[0] for row in session.query(KernelMatchResult.match_id).all()
        }
        rows = session.query(
            KernelMatchFixture.match_id,
            KernelMatchFixture.competition,
            KernelMatchFixture.status,
            KernelMatchFixture.kickoff_utc,
            KernelMatchFixture.home_score,
            KernelMatchFixture.away_score,
        ).all()
    finally:
        session.close()

    # Every competition the backfill knows about is reported even when it holds
    # no fixtures, so a silently-empty competition is visible as no_data rather
    # than absent from the response.
    per: dict[str, dict[str, Any]] = {
        code: {
            "total": 0,
            "future": 0,
            "settled": 0,
            "past_due_unsettled": 0,
            "scored_but_unsettled": 0,
            "no_kickoff": 0,
            "oldest_overdue_days": None,
            "status": STATUS_NO_DATA,
        }
        for code in sorted(BACKFILLABLE_COMPETITIONS)
    }
    buckets: dict[str, int] = {name: 0 for name, _ in AGE_BUCKETS}
    total_past_due = 0
    oldest_overall: int | None = None

    for match_id, competition, _status, kickoff, home_score, away_score in rows:
        entry = per.setdefault(
            competition,
            {
                "total": 0,
                "future": 0,
                "settled": 0,
                "past_due_unsettled": 0,
                "scored_but_unsettled": 0,
                "no_kickoff": 0,
                "oldest_overdue_days": None,
                "status": STATUS_NO_DATA,
            },
        )
        entry["total"] += 1
        kickoff_utc = _as_utc(kickoff)
        if kickoff_utc is None:
            entry["no_kickoff"] += 1
            continue
        if kickoff_utc >= reference:
            entry["future"] += 1
            continue
        if match_id in settled_ids:
            entry["settled"] += 1
            continue

        overdue_days = (reference - kickoff_utc).days
        entry["past_due_unsettled"] += 1
        if home_score is not None and away_score is not None:
            # The score arrived but no result row did: the backfill can fix
            # this one, which is a different operator action from a stalled feed.
            entry["scored_but_unsettled"] += 1
        previous = entry["oldest_overdue_days"]
        if previous is None or overdue_days > previous:
            entry["oldest_overdue_days"] = overdue_days
        buckets[_bucket_for(overdue_days)] += 1
        total_past_due += 1
        if oldest_overall is None or overdue_days > oldest_overall:
            oldest_overall = overdue_days

    for entry in per.values():
        if entry["total"] == 0:
            entry["status"] = STATUS_NO_DATA
        elif entry["past_due_unsettled"] > 0:
            entry["status"] = STATUS_STALE
        else:
            entry["status"] = STATUS_OK

    return {
        "generated_at": reference.isoformat(),
        "total_fixtures": len(rows),
        "past_due_unsettled": total_past_due,
        "oldest_overdue_days": oldest_overall,
        "buckets": buckets,
        "competitions": per,
    }
