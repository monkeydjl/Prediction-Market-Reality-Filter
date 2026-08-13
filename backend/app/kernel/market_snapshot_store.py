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

    def audit_summary(self, *, link_id: int) -> dict[str, Any]:
        """Price-path audit for a market link (P1-V1).

        Returns latest/earliest prices, Δpp, max drawdown of YES price,
        capture span, and quality flags (stale gap, sparse samples).
        """
        snaps = self.get_snapshots(link_id=link_id)
        if not snaps:
            return {
                "link_id": link_id,
                "available": False,
                "snapshot_count": 0,
                "flags": ["no_snapshots"],
            }

        def _price(s: dict) -> float | None:
            for k in ("implied_prob", "yes_price", "price", "market_prob", "prob"):
                v = s.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            return None

        def _ts(s: dict):
            return s.get("captured_at") or s.get("created_at")

        # Build the filtered pairs under one name: rebinding after a
        # `p is not None` comprehension keeps the element type `float | None`
        # from the first assignment, and every arithmetic use below (peak,
        # drawdown, delta, round) needs a real float.
        priced: list[tuple[dict[str, Any], float]] = []
        for s in snaps:
            p = _price(s)
            if p is not None:
                priced.append((s, p))
        if not priced:
            return {
                "link_id": link_id,
                "available": False,
                "snapshot_count": len(snaps),
                "flags": ["no_prices"],
            }

        # assume snaps ordered by captured_at asc or desc — normalize
        def sort_key(item):
            s, _ = item
            t = _ts(s)
            return t or ""

        priced_sorted = sorted(priced, key=sort_key)
        first_s, first_p = priced_sorted[0]
        last_s, last_p = priced_sorted[-1]
        prices = [p for _, p in priced_sorted]
        peak = prices[0]
        max_dd = 0.0
        for p in prices:
            peak = max(peak, p)
            max_dd = max(max_dd, peak - p)

        delta = last_p - first_p
        flags: list[str] = []
        if len(priced_sorted) < 3:
            flags.append("sparse")
        if abs(delta) >= 0.10:
            flags.append("large_move")
        if max_dd >= 0.15:
            flags.append("high_drawdown")

        return {
            "link_id": link_id,
            "available": True,
            "snapshot_count": len(priced_sorted),
            "first_price": round(first_p, 4),
            "last_price": round(last_p, 4),
            "delta_pp": round(delta * 100, 2),
            "max_drawdown_pp": round(max_dd * 100, 2),
            "min_price": round(min(prices), 4),
            "max_price": round(max(prices), 4),
            "first_captured_at": _ts(first_s),
            "last_captured_at": _ts(last_s),
            "flags": flags,
        }

