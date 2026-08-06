"""Persistent daily LLM spend tracker (P0 cost-cap guardrail).

Single-table store that accumulates USD spend per UTC date. The gateway reads
this before every LLM call to enforce ``LLM_DAILY_COST_CAP_USD``. When the cap
is exceeded, the gateway refuses to call any provider and raises immediately.

Why date-keyed instead of rolling-window:
- Simpler operator reasoning: "I spent $X today" vs "in the last 24.3 hours"
- Aligns with billing cycles and monthly budgets
- No cleanup needed (old rows are harmless and can be purged manually)

The spend counter is updated AFTER a successful LLM call returns usage tokens.
Failed calls (degraded mode) are not counted — we only charge for real API spend.
This means the counter slightly lags reality during a burst, but the next call
will see the updated total and refuse if it crosses the cap.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.utils import sqlite_db
from app.utils.sqlite_db import reading, writing

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}


def _ensure_schema(path: str) -> None:
    with writing(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_daily_spend (
                date TEXT PRIMARY KEY,
                spend_usd REAL NOT NULL DEFAULT 0.0
            )
            """
        )
        sqlite_db.apply_migrations(conn, "llm_daily_spend", _SCHEMA_VERSION, _MIGRATIONS)
        sqlite_db.record_schema_version(conn, "llm_daily_spend", _SCHEMA_VERSION)


def get_spend_today() -> float:
    """Return the accumulated USD spend for today (UTC date).

    Returns 0.0 when no row exists for today yet.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    today = datetime.now(timezone.utc).date().isoformat()
    with reading(path) as conn:
        row = conn.execute(
            "SELECT spend_usd FROM llm_daily_spend WHERE date = ?",
            (today,),
        ).fetchone()
    return float(row["spend_usd"]) if row else 0.0


def add_spend(amount_usd: float) -> None:
    """Increment today's spend by the given USD amount.

    Creates today's row if it doesn't exist yet. Thread-safe via the write lock.
    """
    if amount_usd <= 0:
        return
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    today = datetime.now(timezone.utc).date().isoformat()
    with writing(path) as conn:
        conn.execute(
            """
            INSERT INTO llm_daily_spend (date, spend_usd)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET spend_usd = spend_usd + excluded.spend_usd
            """,
            (today, amount_usd),
        )
