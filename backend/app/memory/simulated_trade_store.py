"""simulated_trade_store.py
=======================
Paper-trading database for evaluating system prediction quality.

Stores simulated trades opened from decision reports and closed when events
resolve.  Provides win-rate, PnL, and expected-value statistics for feedback.

Schema lives in v2_loop.db alongside predictions (same connection).
"""

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.utils import sqlite_db
from app.utils.sqlite_db import loop_db_path, reading, writing

logger = logging.getLogger(__name__)

# ── Schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS simulated_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT NOT NULL UNIQUE,
    event_id        TEXT NOT NULL,
    event_title     TEXT NOT NULL DEFAULT '',
    direction       TEXT NOT NULL CHECK (direction IN ('YES','NO')),
    entry_prob      REAL NOT NULL,          -- system probability estimate at entry (0-100)
    market_prob     REAL NOT NULL,          -- market implied probability at entry (0-100)
    entry_edge      REAL NOT NULL,          -- entry_prob - market_prob
    entry_time      TEXT NOT NULL,          -- ISO 8601
    position_pct    REAL NOT NULL DEFAULT 2.0,  -- suggested allocation %
    confidence      REAL,                   -- credibility score at entry (0-100)
    trust_weight    REAL,                   -- calibration trust at entry (0-1)
    decision        TEXT NOT NULL DEFAULT 'watch',  -- act | provisional_act | watch

    exit_prob       REAL,                   -- system prob at exit
    exit_market     REAL,                   -- market prob at exit
    exit_time       TEXT,                   -- ISO 8601
    exit_reason     TEXT,                   -- resolved_yes | resolved_no | manual

    actual_outcome  REAL,                   -- 100=YES 0=NO
    pnl_pct         REAL,                   -- profit/loss as %-points of position
    is_win          INTEGER,               -- 1=direction won 0=direction lost

    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sim_trades_event   ON simulated_trades(event_id);
CREATE INDEX IF NOT EXISTS idx_sim_trades_status  ON simulated_trades(status);
CREATE INDEX IF NOT EXISTS idx_sim_trades_wins    ON simulated_trades(is_win) WHERE is_win IS NOT NULL;
"""

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _ensure_schema(path: str) -> None:
    """Create the table on first use of a given DB path (idempotent)."""
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with writing(path) as conn:
            conn.executescript(SCHEMA_SQL)
            sqlite_db.apply_migrations(
                conn, "simulated_trades", _SCHEMA_VERSION, _MIGRATIONS
            )
        _INITIALIZED.add(path)


# ── CRUD ────────────────────────────────────────────────────────────

def open_trade(
    event_id: str,
    event_title: str = "",
    *,
    direction: str,
    entry_prob: float,
    market_prob: float,
    confidence: float | None = None,
    trust_weight: float | None = None,
    decision: str = "watch",
    position_pct: float = 2.0,
) -> dict[str, Any]:
    """Create a new open simulated trade.  Idempotent: returns existing open
    trade for the same event_id instead of creating a duplicate."""
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with writing(db_path) as conn:
        # Idempotent: one open trade per event. The write lock is held for the
        # whole scope, so the check and the insert cannot interleave with a
        # concurrent open_trade for the same event.
        existing = conn.execute(
            "SELECT * FROM simulated_trades WHERE event_id=? AND status='open'",
            (event_id,),
        ).fetchone()
        if existing:
            return _row_to_dict(existing)

        now = datetime.now(timezone.utc).isoformat()
        trade_id = f"sim-{uuid.uuid4().hex[:12]}"
        edge = round(entry_prob - market_prob, 2)
        conn.execute(
            """INSERT INTO simulated_trades
               (trade_id, event_id, event_title, direction, entry_prob, market_prob,
                entry_edge, entry_time, position_pct, confidence, trust_weight, decision)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, event_id, event_title, direction, entry_prob, market_prob,
             edge, now, position_pct, confidence, trust_weight, decision),
        )
        row = conn.execute(
            "SELECT * FROM simulated_trades WHERE trade_id=?", (trade_id,)
        ).fetchone()
        logger.info(
            "Opened simulated trade %s: %s %s @ %.1f%% (edge=%.1f)",
            trade_id, event_id[:12], direction, entry_prob, edge,
        )
        return _row_to_dict(row)


def close_trade(
    event_id: str,
    *,
    actual_outcome: float,
    exit_prob: float | None = None,
    exit_market: float | None = None,
    exit_reason: str | None = None,
) -> dict[str, Any] | None:
    """Close the open simulated trade for event_id with its resolution outcome."""
    if exit_reason is None:
        exit_reason = _resolution_exit_reason(actual_outcome)
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with writing(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM simulated_trades WHERE event_id=? AND status='open'",
            (event_id,),
        ).fetchone()
        if not row:
            return None

        trade = _row_to_dict(row)
        direction = trade["direction"]
        market_prob = trade["market_prob"]
        position_pct = trade["position_pct"]

        pnl = _settlement_pnl_pct(
            direction=direction,
            market_prob=market_prob,
            actual_outcome=actual_outcome,
            position_pct=position_pct,
        )
        is_win = 1 if pnl > 0 else 0

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE simulated_trades SET
               exit_prob=?, exit_market=?, exit_time=?, exit_reason=?,
               actual_outcome=?, pnl_pct=?, is_win=?,
               status='closed', updated_at=?
               WHERE trade_id=?""",
            (exit_prob, exit_market, now, exit_reason,
             actual_outcome, pnl, is_win,
             now, trade["trade_id"]),
        )
        logger.info(
            "Closed simulated trade %s: %s=%d, pnl=%.1f%%, win=%d",
            trade["trade_id"], direction, int(actual_outcome), pnl, is_win,
        )
        return _row_to_dict(
            conn.execute(
                "SELECT * FROM simulated_trades WHERE trade_id=?",
                (trade["trade_id"],),
            ).fetchone()
        )


def _settlement_pnl_pct(
    *,
    direction: str,
    market_prob: float,
    actual_outcome: float,
    position_pct: float,
) -> float:
    """Return paper-trade PnL for a 0-100 settlement value.

    ``market_prob`` is the tradable YES price at entry. ``entry_prob`` is the
    system's estimate and is useful for edge sizing, but it is not the fill
    price. Partial market resolutions can settle anywhere in [0, 100],
    so win/loss must be based on payout minus entry price, not binary
    YES>=99/NO<=1 thresholds.
    """
    actual = max(0.0, min(100.0, float(actual_outcome)))
    yes_price = max(0.0, min(100.0, float(market_prob)))
    position = float(position_pct)

    if direction == "YES":
        cost = max(yes_price, 1.0)
        return round(((actual - yes_price) / cost) * position, 2)

    no_price = max(100.0 - yes_price, 1.0)
    no_payout = 100.0 - actual
    return round(((no_payout - no_price) / no_price) * position, 2)


def _resolution_exit_reason(actual_outcome: float) -> str:
    """Classify a 0-100 settlement for display/audit metadata."""
    actual = max(0.0, min(100.0, float(actual_outcome)))
    if actual >= 99:
        return "resolved_yes"
    if actual <= 1:
        return "resolved_no"
    return "resolved_partial"


def _count_trades(status: str) -> int:
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with reading(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM simulated_trades WHERE status=?",
            (status,),
        ).fetchone()
        return int(row[0] if row else 0)


def count_open_trades() -> int:
    """Return the total number of open simulated trades."""
    return _count_trades("open")


def count_closed_trades() -> int:
    """Return the total number of closed simulated trades."""
    return _count_trades("closed")


def list_open_trades(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Return open simulated trades, newest first."""
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with reading(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM simulated_trades
               WHERE status='open'
               ORDER BY entry_time DESC, trade_id DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_closed_trades(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Return recently closed simulated trades."""
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with reading(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM simulated_trades
               WHERE status='closed'
               ORDER BY exit_time DESC, trade_id DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def recompute_closed_trades() -> dict[str, Any]:
    """Recalculate settlement PnL/win flags for already closed trades.

    This is primarily a repair path for legacy rows that were closed with the
    old binary-only settlement logic.  It is safe to run repeatedly because the
    recalculated values are deterministic from direction, entry market price,
    position size, and actual_outcome.
    """
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with writing(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM simulated_trades "
            "WHERE status='closed' AND actual_outcome IS NOT NULL"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        total_pnl = 0.0
        wins = 0

        for row in rows:
            trade = _row_to_dict(row)
            pnl = _settlement_pnl_pct(
                direction=trade["direction"],
                market_prob=trade["market_prob"],
                actual_outcome=trade["actual_outcome"],
                position_pct=trade["position_pct"],
            )
            is_win = 1 if pnl > 0 else 0
            exit_reason = trade.get("exit_reason")
            if exit_reason in (None, "", "resolved_yes", "resolved_no"):
                exit_reason = _resolution_exit_reason(trade["actual_outcome"])

            conn.execute(
                """UPDATE simulated_trades
                   SET pnl_pct=?, is_win=?, exit_reason=?, updated_at=?
                   WHERE trade_id=?""",
                (pnl, is_win, exit_reason, now, trade["trade_id"]),
            )
            total_pnl += pnl
            wins += is_win

        updated = len(rows)
        return {
            "updated": updated,
            "wins": wins,
            "win_rate": round(wins / updated, 3) if updated else None,
            "total_pnl_pct": round(total_pnl, 2),
            "avg_pnl_pct": round(total_pnl / updated, 2) if updated else None,
        }


# Documents how the edge statistics in trade_stats() are computed, so API
# consumers know the raw formula and the probability scale (0-100, not 0-1).
_EDGE_DEFINITION: dict[str, str] = {
    "raw_edge": "entry_prob - market_prob",
    "scale": "0-100",
    "directional": "raw_edge for YES, -raw_edge for NO",
}


def trade_stats() -> dict[str, Any]:
    """Aggregate statistics for all closed simulated trades."""
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with reading(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) as n FROM simulated_trades WHERE status='closed'"
        ).fetchone()["n"]
        if total == 0:
            return {
                "total_closed": 0,
                "win_rate": None,
                "total_pnl_pct": 0,
                "avg_pnl_pct": None,
                "avg_edge_at_entry": None,
                "avg_directional_edge_at_entry": None,
                "edge_definition": _EDGE_DEFINITION,
                "by_direction": {},
                "by_decision": {},
            }

        wins = conn.execute(
            "SELECT COUNT(*) as n FROM simulated_trades WHERE status='closed' AND is_win=1"
        ).fetchone()["n"]

        pnl_row = conn.execute(
            "SELECT SUM(pnl_pct) as total, AVG(pnl_pct) as avg FROM simulated_trades WHERE status='closed'"
        ).fetchone()

        edge_row = conn.execute(
            "SELECT AVG(ABS(entry_edge)) as avg FROM simulated_trades WHERE status='closed'"
        ).fetchone()

        # Directional edge: positive when the picked direction was right on
        # average. entry_edge = entry_prob - market_prob, so a NO trade flips
        # sign (a NO pick wants the market prob to be ABOVE the entry prob).
        dir_edge_row = conn.execute(
            """SELECT AVG(CASE WHEN direction='YES' THEN entry_edge
                               ELSE -entry_edge END) as avg
               FROM simulated_trades WHERE status='closed'"""
        ).fetchone()

        by_dir = {}
        for d in ("YES", "NO"):
            r = conn.execute(
                """SELECT COUNT(*) as total, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) as wins,
                   AVG(pnl_pct) as avg_pnl, SUM(pnl_pct) as total_pnl
                   FROM simulated_trades WHERE status='closed' AND direction=?""",
                (d,),
            ).fetchone()
            if r["total"] > 0:
                by_dir[d] = {
                    "total": r["total"],
                    "wins": r["wins"],
                    "win_rate": round(r["wins"] / r["total"], 3),
                    "avg_pnl": round(r["avg_pnl"], 2),
                    "total_pnl": round(r["total_pnl"], 2),
                }

        by_decision = {}
        for dec in ("act", "provisional_act", "watch"):
            r = conn.execute(
                """SELECT COUNT(*) as total, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) as wins,
                   AVG(pnl_pct) as avg_pnl, SUM(pnl_pct) as total_pnl
                   FROM simulated_trades WHERE status='closed' AND decision=?""",
                (dec,),
            ).fetchone()
            if r["total"] > 0:
                by_decision[dec] = {
                    "total": r["total"],
                    "wins": r["wins"],
                    "win_rate": round(r["wins"] / r["total"], 3),
                    "avg_pnl": round(r["avg_pnl"], 2),
                }

        return {
            "total_closed": total,
            "win_rate": round(wins / total, 3),
            "total_pnl_pct": round(pnl_row["total"], 2),
            "avg_pnl_pct": round(pnl_row["avg"], 2),
            "avg_edge_at_entry": round(edge_row["avg"], 2) if edge_row["avg"] else None,
            "avg_directional_edge_at_entry": (
                round(dir_edge_row["avg"], 2) if dir_edge_row["avg"] else None
            ),
            "edge_definition": _EDGE_DEFINITION,
            "by_direction": by_dir,
            "by_decision": by_decision,
        }


def has_open_trade(event_id: str) -> bool:
    """Check if an event already has an open simulated trade."""
    db_path = loop_db_path()
    _ensure_schema(db_path)
    with reading(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM simulated_trades WHERE event_id=? AND status='open'",
            (event_id,),
        ).fetchone()
        return row is not None


# ── Helpers ──────────────────────────────────────────────────────────

def directional_edge(*, direction: str, entry_edge: float) -> float:
    """Edge in favor of the trade direction (pp, 0-100 scale).

    ``entry_edge`` is the EIP raw edge: AI% − market% (same as predictions.raw_edge).
    For YES, positive raw edge favors the position; for NO, negative raw edge does.
    """
    raw = float(entry_edge)
    if direction == "NO":
        return round(-raw, 2)
    return round(raw, 2)


def _row_to_dict(row: sqlite3.Row | tuple) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        d = dict(row)
    else:
        cols = [
            "id", "trade_id", "event_id", "event_title", "direction",
            "entry_prob", "market_prob", "entry_edge", "entry_time", "position_pct",
            "confidence", "trust_weight", "decision",
            "exit_prob", "exit_market", "exit_time", "exit_reason",
            "actual_outcome", "pnl_pct", "is_win",
            "status", "created_at", "updated_at",
        ]
        d = dict(zip(cols, row))
    # Derived fields for UI / API consumers (not stored columns).
    try:
        d["raw_edge"] = d.get("entry_edge")
        d["directional_edge"] = directional_edge(
            direction=str(d.get("direction") or "YES"),
            entry_edge=float(d.get("entry_edge") or 0.0),
        )
        d["edge_definition"] = "raw_edge = ai_probability - market_probability (0-100 pp)"
    except (TypeError, ValueError):
        d.setdefault("raw_edge", d.get("entry_edge"))
        d.setdefault("directional_edge", None)
        d.setdefault(
            "edge_definition",
            "raw_edge = ai_probability - market_probability (0-100 pp)",
        )
    return d
