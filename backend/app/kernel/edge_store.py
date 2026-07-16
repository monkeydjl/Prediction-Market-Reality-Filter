"""Persistence for kernel_sport_edges table (append-only time-series).

Each detect_edges() call appends one row per outcome. Read methods support
latest-per-outcome and full history queries. Mirrors the
sport_market_link_store / market_snapshot_store pattern.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, desc

from app.kernel.kernel_db import (
    KernelSportEdge,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelSportEdge) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "mapped_outcome": row.mapped_outcome,
        "model_prob": row.model_prob,
        "market_prob": row.market_prob,
        "raw_edge": row.raw_edge,
        "trust": row.trust,
        "liquidity_factor": row.liquidity_factor,
        "adjusted_edge": row.adjusted_edge,
        "spread": row.spread,
        "sources_count": row.sources_count,
        "stale": bool(row.stale),
        "captured_at": row.captured_at,
    }


class EdgeStore:
    """Append-only persistence for edge snapshots.

    Writes one row per (match_id, mapped_outcome, captured_at). Reads support
    latest-per-outcome, full history, and top-discrepancy queries.
    """

    def append_edge(
        self,
        *,
        match_id: str,
        mapped_outcome: str,
        model_prob: float,
        market_prob: float,
        raw_edge: float,
        trust: float,
        liquidity_factor: float,
        adjusted_edge: float,
        spread: float | None,
        sources_count: int,
        stale: bool,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append one edge snapshot row. Returns the inserted row as dict."""
        when = captured_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelSportEdge(
                match_id=match_id,
                mapped_outcome=mapped_outcome,
                model_prob=model_prob,
                market_prob=market_prob,
                raw_edge=raw_edge,
                trust=trust,
                liquidity_factor=liquidity_factor,
                adjusted_edge=adjusted_edge,
                spread=spread,
                sources_count=sources_count,
                stale=1 if stale else 0,
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

    def get_latest_edges(self, match_id: str) -> list[dict[str, Any]]:
        """Latest edge per mapped_outcome for a match.

        Uses a subquery to find max(captured_at) per (match_id, mapped_outcome),
        then joins back to get the full row.
        """
        session = get_kernel_session()
        try:
            subq = (
                session.query(
                    KernelSportEdge.mapped_outcome,
                    func.max(KernelSportEdge.captured_at).label("max_ts"),
                )
                .filter(KernelSportEdge.match_id == match_id)
                .group_by(KernelSportEdge.mapped_outcome)
                .subquery()
            )
            rows = (
                session.query(KernelSportEdge)
                .join(
                    subq,
                    (KernelSportEdge.mapped_outcome == subq.c.mapped_outcome)
                    & (KernelSportEdge.captured_at == subq.c.max_ts),
                )
                .filter(KernelSportEdge.match_id == match_id)
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_edge_history(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[dict[str, Any]]:
        """Full time-series, optionally filtered by outcome. Ordered by captured_at ASC."""
        session = get_kernel_session()
        try:
            q = session.query(KernelSportEdge).filter(
                KernelSportEdge.match_id == match_id
            )
            if mapped_outcome is not None:
                q = q.filter(KernelSportEdge.mapped_outcome == mapped_outcome)
            rows = q.order_by(KernelSportEdge.captured_at.asc()).all()
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_top_discrepancies(
        self, limit: int = 20, min_abs_edge: float = 0.0
    ) -> list[dict[str, Any]]:
        """Top matches by |adjusted_edge| (latest snapshot per match+outcome).

        Ordered by |adjusted_edge| DESC. Filters out edges where
        |adjusted_edge| < min_abs_edge.
        """
        session = get_kernel_session()
        try:
            # Subquery: latest edge per (match_id, mapped_outcome)
            subq = (
                session.query(
                    KernelSportEdge.match_id,
                    KernelSportEdge.mapped_outcome,
                    func.max(KernelSportEdge.captured_at).label("max_ts"),
                )
                .group_by(
                    KernelSportEdge.match_id, KernelSportEdge.mapped_outcome
                )
                .subquery()
            )
            rows = (
                session.query(KernelSportEdge)
                .join(
                    subq,
                    (KernelSportEdge.match_id == subq.c.match_id)
                    & (KernelSportEdge.mapped_outcome == subq.c.mapped_outcome)
                    & (KernelSportEdge.captured_at == subq.c.max_ts),
                )
                .filter(
                    func.abs(KernelSportEdge.adjusted_edge) >= min_abs_edge
                )
                .order_by(desc(func.abs(KernelSportEdge.adjusted_edge)))
                .limit(limit)
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()
