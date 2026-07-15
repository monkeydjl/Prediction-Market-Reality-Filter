"""Persistence for market price snapshots (append-only time-series).

Snapshots are written by the scheduler capture job and read by the
MarketSnapshotChart frontend. Separated from links to avoid rewriting link
rows on every price tick.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelMarketSnapshot,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelMarketSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "link_id": row.link_id,
        "implied_prob": row.implied_prob,
        "price": row.price,
        "liquidity": row.liquidity,
        "volume": row.volume,
        "captured_at": row.captured_at,
    }


class MarketSnapshotStore:
    """Append-only snapshot store."""

    def append_snapshot(
        self,
        *,
        link_id: int,
        implied_prob: float,
        price: float | None = None,
        liquidity: float | None = None,
        volume: float | None = None,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        when = captured_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelMarketSnapshot(
                link_id=link_id,
                implied_prob=implied_prob,
                price=price,
                liquidity=liquidity,
                volume=volume,
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

    def get_snapshots(self, *, link_id: int) -> list[dict[str, Any]]:
        """All snapshots for a link, oldest first (chart x-axis order)."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSnapshot)
                .filter_by(link_id=link_id)
                .order_by(KernelMarketSnapshot.captured_at.asc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_latest_snapshot(self, *, link_id: int) -> dict[str, Any] | None:
        """Most recent snapshot for a link, or None."""
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelMarketSnapshot)
                .filter_by(link_id=link_id)
                .order_by(KernelMarketSnapshot.captured_at.desc())
                .first()
            )
            return _row_to_dict(row) if row is not None else None
        except Exception:
            return None
        finally:
            session.close()
