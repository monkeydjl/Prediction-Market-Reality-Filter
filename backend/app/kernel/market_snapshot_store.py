"""Persistence for market price snapshots (append-only time-series).

Snapshots are written by the scheduler capture job and read by the
MarketSnapshotChart frontend. Separated from links to avoid rewriting link
rows on every price tick.

Raw reads (:meth:`MarketSnapshotStore.get_snapshots`,
:meth:`MarketSnapshotStore.get_latest_snapshot`) propagate a query failure
rather than answering an empty value (audit 53.180). ``[]`` and ``None`` are
reachable answers on a healthy database -- every link starts with no rows --
so a store that returns them on failure has said "this market has no prices"
on the strength of a query that never reached the table. Containing that
swallow here is what made ``market_liquidity.compute_match_liquidity_factor``'s
promise ("a failed read escapes") unimplementable, and it silently re-armed the
inverted alarm documented there: no reading -> no damp -> *higher* confidence.

The layer that can name the failure names it, and :meth:`audit_summary` is such
a layer because its body already carries an ``available`` switch; the routes
whose bodies have no switch let the error out as a 500, which is what
``tests/test_sport_market_routes.py`` already demands of a degraded links table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelMarketSnapshot,
    get_kernel_session,
)
from app.utils.failure_policy import READ_FAILED
from app.utils.helpers import as_utc

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelMarketSnapshot) -> dict[str, Any]:
    """One snapshot row as the dict every reader and route consumes.

    ``captured_at`` goes through :func:`app.utils.helpers.as_utc` because this
    dict is where the value stops being a row and starts being a payload. No
    caller formats it: ``/snapshots`` declares ``datetime`` in its
    ``response_model``, ``/links/{id}/latest`` has no model at all and goes
    through ``jsonable_encoder``, and ``audit_summary`` republishes the value as
    ``first_captured_at``/``last_captured_at``. All three serialize a naive
    datetime without an offset, so the offset has to be attached *here*, before
    any of them see it -- see audit 53.183 for the measurement.
    """
    return {
        "id": row.id,
        "link_id": row.link_id,
        "implied_prob": row.implied_prob,
        "price": row.price,
        "liquidity": row.liquidity,
        "volume": row.volume,
        "captured_at": as_utc(row.captured_at),
    }


def _ts(row: dict[str, Any]) -> Any:
    return row.get("captured_at") or row.get("created_at")


def is_timestamped(row: dict[str, Any]) -> bool:
    """Whether a snapshot row is a point on a *time* axis.

    ``kernel_market_snapshots.captured_at`` is nullable, so a row can carry no
    timestamp at all -- legal per the model even though this store's writer
    always fills the column in. Such a row cannot be placed on an axis: it is
    neither the first nor the last sample, and it has no x value to be drawn at.
    Both readers that draw a path consult this one function instead of repeating
    the test, so the two doors cannot drift apart (audit section 53.48).

    The store's raw read (``get_snapshots``) still returns those rows; they are
    dropped and *counted* by the readers, never silently re-ordered.
    """
    return _ts(row) is not None


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
        """All snapshots for a link, oldest first (chart x-axis order).

        ``id`` is the tiebreaker, and it is load-bearing rather than
        cosmetic: rows sharing a ``captured_at`` have no defined relative
        order otherwise, and this series is drawn as a line chart, where a
        reordered pair inside a tie is a visible artefact. Ascending id keeps
        a tie in insertion order.

        An empty list means "this link has no snapshots" and nothing else; a
        failed query propagates (audit 53.180).
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSnapshot)
                .filter_by(link_id=link_id)
                .order_by(
                    KernelMarketSnapshot.captured_at.asc(),
                    KernelMarketSnapshot.id.asc(),
                )
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_latest_snapshot(self, *, link_id: int) -> dict[str, Any] | None:
        """Most recent snapshot for a link, or None.

        ``id`` is the tiebreaker, and it is load-bearing rather than
        cosmetic: on a tie the relative order of the two rows is undefined,
        and the tiebreaker-less form returned the *first*-inserted row, so a
        tie silently reported the *older* capture as the newest. Descending
        id keeps "latest" meaning "most recently written".

        ``None`` means "this link has no snapshot" and nothing else; a failed
        query propagates (audit 53.180).
        """
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelMarketSnapshot)
                .filter_by(link_id=link_id)
                .order_by(
                    KernelMarketSnapshot.captured_at.desc(),
                    KernelMarketSnapshot.id.desc(),
                )
                .first()
            )
            return _row_to_dict(row) if row is not None else None
        finally:
            session.close()

    def get_price_path(self, *, link_id: int) -> tuple[list[dict[str, Any]], int]:
        """The link's snapshots as a drawable path, plus how many were dropped.

        Returns ``(points, untimestamped_count)``. A row that ``is_timestamped``
        rejects has no x value, so it is not part of the path; it is reported
        rather than silently re-ordered, because SQLite would otherwise sort it
        *first* -- i.e. present an undatable row as the earliest price. The audit
        door reads the same predicate out of ``audit_summary``, so both doors
        answer this question the same way.
        """
        rows = self.get_snapshots(link_id=link_id)
        points = [row for row in rows if is_timestamped(row)]
        return points, len(rows) - len(points)

    def audit_summary(self, *, link_id: int) -> dict[str, Any]:
        """Price-path audit for a market link (P1-V1).

        Returns latest/earliest prices, Δpp, max drawdown of YES price,
        capture span, and quality flags (stale gap, sparse samples).

        A row with no timestamp at all is *not* a point on the price path: it
        cannot be the first or the last sample, so it is kept out of the
        endpoint readings and reported through ``untimestamped_count`` /
        ``flags`` instead. ``kernel_market_snapshots.captured_at`` is a nullable
        column, so this state is reachable even though the store's writer always
        fills it in; see section 53.47 of the audit.

        ``read_failed`` is the third zero (audit 53.180). ``available: false``
        alone could not tell a market with no snapshots from a snapshot table
        that could not be read, and only the second of those is the server's
        fault -- so this body is the one place in the family that can name it,
        because it already speaks in switches. Every branch carries the key
        explicitly: an absent key would read as "not asked", which is a fourth
        meaning nobody needs.
        """
        try:
            snaps = self.get_snapshots(link_id=link_id)
        except Exception as exc:
            logger.warning(
                "market_snapshot_store audit read failed [link_id=%s]: %s",
                link_id,
                type(exc).__name__,
                exc_info=True,
            )
            return {
                "link_id": link_id,
                "available": False,
                "read_failed": True,
                "snapshot_count": 0,
                "flags": [READ_FAILED],
            }
        if not snaps:
            return {
                "link_id": link_id,
                "available": False,
                "read_failed": False,
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
                "read_failed": False,
                "snapshot_count": len(snaps),
                "flags": ["no_prices"],
            }

        # A row with no timestamp cannot be placed on a time axis. It is
        # neither the first nor the last point, and it cannot even be *compared*
        # against the rows that do carry one. Keep such rows out of the
        # endpoints and declare them in `flags`, rather than letting a missing
        # timestamp silently become "the earliest price".
        timestamped: list[tuple[dict[str, Any], float]] = [
            (s, p) for s, p in priced if is_timestamped(s)
        ]
        untimestamped_count = len(priced) - len(timestamped)
        if not timestamped:
            return {
                "link_id": link_id,
                "available": False,
                "read_failed": False,
                "snapshot_count": len(priced),
                "untimestamped_count": untimestamped_count,
                "flags": ["untimestamped_snapshots"],
            }

        # One comparable type for every sort key. A real read yields `datetime`
        # while older fixtures supplied ISO strings; a key whose type depends on
        # the input raised `TypeError` inside `sorted` -- a 500 on both audit
        # routes, since neither call site catches.
        def sort_key(item: tuple[dict[str, Any], float]) -> str:
            t = _ts(item[0])
            return t.isoformat() if isinstance(t, datetime) else str(t)

        timestamped.sort(key=sort_key)
        first_s, first_p = timestamped[0]
        last_s, last_p = timestamped[-1]
        prices = [p for _, p in timestamped]
        peak = prices[0]
        max_dd = 0.0
        for p in prices:
            peak = max(peak, p)
            max_dd = max(max_dd, peak - p)

        delta = last_p - first_p
        flags: list[str] = []
        if len(timestamped) < 3:
            flags.append("sparse")
        if abs(delta) >= 0.10:
            flags.append("large_move")
        if max_dd >= 0.15:
            flags.append("high_drawdown")
        if untimestamped_count:
            flags.append("untimestamped_snapshots")

        return {
            "link_id": link_id,
            "available": True,
            "read_failed": False,
            "snapshot_count": len(priced),
            "untimestamped_count": untimestamped_count,
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

