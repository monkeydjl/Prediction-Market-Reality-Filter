"""Report stored prediction probabilities that look like they are on the 0-1 scale.

Why this exists
---------------
PMRF stores every probability as a **0-100 percentage point**. The write path
enforces only ``_clamp(value, 0, 100)`` (``ai_analysis_service.py``,
``probability_engine_service.py``), which does **not** catch a fractional value:
``0.2`` is a legal 0.2%, so a caller passing a fraction is stored silently and
every edge is then computed against it.

The one live example found by the 2026-09-26 audit: prediction
``0779bde4dcd63e08`` (Kalshi ``KXBRUVSEAT-35``) stored
``market_probability=0.2`` instead of 20 -- giving ``raw_edge=29.97`` instead of
10.17 and ``adjusted_edge=14.98`` instead of 5.09, which cleared the ACT
threshold the correct arithmetic does not. A ``watch`` was recorded as
``provisional_act``, with a mirrored ``simulated_trades`` row.

The current Kalshi **event** adapter cannot produce that value:
``kalshi_event_source._baseline_and_quote`` returns ``last * 100`` /
``(bid + ask) / 2 * 100`` / ``50.0``, all 0-100. The value can only arrive
through the API's ``baseline_probability`` (``ge=0.0, le=100.0`` -- so a fraction
is accepted), which reaches the record unchanged via
``events.py`` -> ``analyze_event`` -> ``event_intelligence_service``.

There is deliberately **no write-time guard** for this, and this script is not a
step towards one: a value-only threshold cannot tell 0.2% from a 0.2 fraction,
and the analyze request carries no second probability to compare against, so any
such threshold would reject a real 0.2% market. What is available is a census.

Read-only
---------
The loop DB is opened with SQLite ``mode=ro``, so nothing can be written.

Usage
-----
    cd backend && python scripts/report_probability_scale_outliers.py
    cd backend && python scripts/report_probability_scale_outliers.py --event-id <id>
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.utils import sqlite_db  # noqa: E402


def _connect() -> sqlite3.Connection:
    path = pathlib.Path(sqlite_db.loop_db_path())
    if not path.is_file():
        raise SystemExit(f"loop DB not found: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _distribution(conn: sqlite3.Connection) -> None:
    buckets = (
        ("total rows", "SELECT COUNT(*) FROM predictions"),
        ("market_probability NULL", "SELECT COUNT(*) FROM predictions "
                                    "WHERE market_probability IS NULL"),
        ("0 < mp < 1   (0-1 scale suspects)", "SELECT COUNT(*) FROM predictions "
                                              "WHERE market_probability > 0 "
                                              "AND market_probability < 1"),
        ("mp = 0", "SELECT COUNT(*) FROM predictions WHERE market_probability = 0"),
        ("1 <= mp <= 100", "SELECT COUNT(*) FROM predictions "
                           "WHERE market_probability >= 1 "
                           "AND market_probability <= 100"),
        ("mp > 100", "SELECT COUNT(*) FROM predictions WHERE market_probability > 100"),
    )
    print("=== predictions.market_probability distribution ===")
    for label, sql in buckets:
        print(f"  {label:36s} {conn.execute(sql).fetchone()[0]}")
    lo, hi = conn.execute(
        "SELECT MIN(market_probability), MAX(market_probability) FROM predictions"
    ).fetchone()[:2]
    print(f"  {'min / max':36s} {lo} / {hi}")


def _suspects(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT event_id, contract_id, platform, ai_probability, market_probability,"
        " raw_edge, adjusted_edge, decision, created_at"
        " FROM predictions WHERE market_probability > 0 AND market_probability < 1"
        " ORDER BY created_at"
    ).fetchall()
    print(f"\n=== rows with 0 < market_probability < 1 ({len(rows)}) ===")
    if not rows:
        print("  none")
        return
    for row in rows:
        record = dict(row)
        market = record["market_probability"]
        ai = record["ai_probability"]
        print(
            f"  {record['event_id']}  {record['platform']}/{record['contract_id']}"
            f"  created={record['created_at']}"
        )
        print(
            f"      ai={ai} market={market} raw_edge={record['raw_edge']}"
            f" adjusted_edge={record['adjusted_edge']} decision={record['decision']}"
        )
        if isinstance(ai, (int, float)):
            corrected = ai - market * 100
            print(
                f"      if the market value were {market * 100}:"
                f" raw_edge={round(corrected, 2)}"
            )


def _one_event(conn: sqlite3.Connection, event_id: str) -> None:
    for table in ("predictions", "simulated_trades", "event_market_links"):
        print(f"\n=== {table} WHERE event_id = {event_id} ===")
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE event_id = ?", (event_id,)
        ).fetchall()
        if not rows:
            print("  (no row)")
        for row in rows:
            for key in row.keys():
                value = row[key]
                if isinstance(value, str) and len(value) > 200:
                    value = value[:200] + "..."
                print(f"  {key:28s} = {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-id",
        help="also dump every row for this event id, across the three tables",
    )
    args = parser.parse_args()

    conn = _connect()
    try:
        _distribution(conn)
        _suspects(conn)
        if args.event_id:
            _one_event(conn, args.event_id)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
