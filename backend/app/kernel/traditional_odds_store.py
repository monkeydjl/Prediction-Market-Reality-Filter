"""Persistence for traditional sportsbook odds snapshots (append-only time-series).

Separate from MarketSnapshotStore because the field semantics differ:
- Polymarket snapshots have link_id, liquidity, volume
- Traditional odds have decimal_odds, bookmaker, bookmakers_count

Follows the existing Store pattern: keyword-only args, session-per-call,
_row_to_dict converter. Reads propagate a query failure instead of returning an
empty value (audit 53.180): "no snapshots" is a real answer that callers act on,
so it cannot double as the failure signal. The route owns the translation into
an HTTP body, where the failure can be named.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelTraditionalOddsSnapshot,
    get_kernel_session,
)
from app.utils.helpers import as_utc


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelTraditionalOddsSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "mapped_outcome": row.mapped_outcome,
        "competition": row.competition,
        "implied_prob": row.implied_prob,
        "decimal_odds": row.decimal_odds,
        "bookmaker": row.bookmaker,
        "bookmakers_count": row.bookmakers_count,
        "captured_at": as_utc(row.captured_at),
    }


class TraditionalOddsStore:
    """Append-only traditional odds snapshot store."""

    def append_snapshot(
        self,
        *,
        match_id: str,
        mapped_outcome: str,
        competition: str,
        implied_prob: float,
        decimal_odds: float,
        bookmaker: str | None = None,
        bookmakers_count: int = 0,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a snapshot. Returns the inserted row as dict.

        Idempotent via unique constraint (match_id, mapped_outcome, captured_at).
        Raises IntegrityError on duplicate.
        """
        when = captured_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelTraditionalOddsSnapshot(
                match_id=match_id,
                mapped_outcome=mapped_outcome,
                competition=competition,
                implied_prob=implied_prob,
                decimal_odds=decimal_odds,
                bookmaker=bookmaker,
                bookmakers_count=bookmakers_count,
                captured_at=when,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_latest_snapshot(
        self, *, match_id: str, mapped_outcome: str | None = None
    ) -> dict[str, Any] | None:
        """Most recent snapshot for a match, optionally filtered by outcome.

        ``None`` means "this match has no snapshot" and nothing else: a failed
        query propagates. Both are reachable states on a healthy database, and
        the caller cannot tell them apart from the return value alone, so the
        store must not try to speak for a failure it cannot describe
        (audit 53.180; same rule as ``edge_store``'s module docstring).
        """
        session = get_kernel_session()
        try:
            q = session.query(KernelTraditionalOddsSnapshot).filter_by(match_id=match_id)
            if mapped_outcome is not None:
                q = q.filter(KernelTraditionalOddsSnapshot.mapped_outcome == mapped_outcome)
            row = q.order_by(KernelTraditionalOddsSnapshot.captured_at.desc()).first()
            return _row_to_dict(row) if row is not None else None
        finally:
            session.close()

    def get_snapshots(
        self, *, match_id: str, mapped_outcome: str | None = None
    ) -> list[dict[str, Any]]:
        """All snapshots for a match (oldest first), optionally filtered by outcome.

        An empty list means "no snapshots" and nothing else; a failed query
        propagates (audit 53.180).
        """
        session = get_kernel_session()
        try:
            q = session.query(KernelTraditionalOddsSnapshot).filter_by(match_id=match_id)
            if mapped_outcome is not None:
                q = q.filter(KernelTraditionalOddsSnapshot.mapped_outcome == mapped_outcome)
            rows = q.order_by(KernelTraditionalOddsSnapshot.captured_at.asc()).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()
