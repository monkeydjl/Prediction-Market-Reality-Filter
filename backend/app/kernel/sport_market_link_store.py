"""Persistence for sport market links (match_id <-> contract_id).

Fail-closed: get_verified_links returns only verified=True rows. Mirrors the
event_market_link_store pattern but uses the kernel_ SQLAlchemy ORM
(KernelBase) so the data lives in kernel_predictions.db alongside the
prediction tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelSportMarketLink,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelSportMarketLink) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "contract_id": row.contract_id,
        "source": row.source,
        "outcome_label": row.outcome_label,
        "mapped_outcome": row.mapped_outcome,
        "link_method": row.link_method,
        "link_confidence": row.link_confidence,
        "verified": bool(row.verified),
        "market_question": row.market_question,
        "implied_prob": row.implied_prob,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class SportMarketLinkStore:
    """CRUD facade over KernelSportMarketLink.

    All methods open a short session and close it in finally, inheriting the
    kernel_db query pattern.
    """

    def upsert_link(
        self,
        *,
        match_id: str,
        contract_id: str,
        source: str,
        outcome_label: str,
        mapped_outcome: str,
        link_method: str,
        link_confidence: float,
        verified: bool,
        market_question: str | None,
        implied_prob: float,
    ) -> dict[str, Any]:
        """Insert or update by (match_id, contract_id, outcome_label)."""
        now = _utcnow()
        session = get_kernel_session()
        try:
            existing = (
                session.query(KernelSportMarketLink)
                .filter_by(
                    match_id=match_id,
                    contract_id=contract_id,
                    outcome_label=outcome_label,
                )
                .one_or_none()
            )
            if existing is not None:
                existing.source = source
                existing.mapped_outcome = mapped_outcome
                existing.link_method = link_method
                existing.link_confidence = link_confidence
                existing.verified = 1 if verified else 0
                existing.market_question = market_question
                existing.implied_prob = implied_prob
                existing.updated_at = now
                session.commit()
                session.refresh(existing)
                return _row_to_dict(existing)
            row = KernelSportMarketLink(
                match_id=match_id,
                contract_id=contract_id,
                source=source,
                outcome_label=outcome_label,
                mapped_outcome=mapped_outcome,
                link_method=link_method,
                link_confidence=link_confidence,
                verified=1 if verified else 0,
                market_question=market_question,
                implied_prob=implied_prob,
                created_at=now,
                updated_at=now,
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

    def get_links(self, *, match_id: str) -> list[dict[str, Any]]:
        """All links for a match (verified and unverified), newest first.

        Lets a query failure escape. ``[]`` is this read's normal answer for an
        unlinked match, so swallowing into it made an unreadable table report
        ``200 {"items": [], "total": 0}`` at ``GET /links/{match_id}`` and
        ``404 "Link not found"`` at ``POST /links/{m}/{c}/verify`` for a link
        that was there.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(match_id=match_id)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_link(self, *, link_id: int) -> dict[str, Any] | None:
        """One link by row id, or None when it does not exist.

        Used by the /links/{link_id}/audit route to attach link metadata to the
        price-path summary. ``None`` means the row is absent; a query failure
        escapes instead, because the route cannot tell the two apart -- it
        answered ``200`` with the snapshot audit and the ``match_id`` /
        ``source`` / ``verified`` keys silently missing.
        """
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelSportMarketLink)
                .filter_by(id=link_id)
                .one_or_none()
            )
            return _row_to_dict(row) if row is not None else None
        finally:
            session.close()

    def get_verified_links(self, *, match_id: str) -> list[dict[str, Any]]:
        """Fail-closed: only verified=True links for a match.

        Fail-closed means unverified rows are excluded, not that a broken query
        is reported as "no verified links" -- ``EdgeDetectorService.detect_edges``
        answered ``skipped=true skip_reason="no_verified_links"``, a stated fact
        about this table on the strength of a query that never reached it.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(match_id=match_id, verified=1)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_pending_links(self) -> list[dict[str, Any]]:
        """All unverified links (the human review queue).

        An empty queue is the reviewer's "nothing to do" signal, so a failed
        query must not produce it: ``GET /pending`` answered
        ``200 {"items": [], "total": 0}`` and ``POST /pending/auto-verify``
        reported ``pending_total: 0 candidates: 0``.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(verified=0)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_all_verified_links(self) -> list[dict[str, Any]]:
        """All verified links across all matches."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(verified=1)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()

    def set_verified(self, *, link_id: int, verified: bool = True) -> bool:
        """Promote/demote a link. Returns True if a row was updated.

        ``False`` means "no such row" only. A failed UPDATE re-raises after the
        rollback, like ``upsert_link``: the route maps ``False`` to
        ``500 "Verify failed"`` either way, but ``auto_verify_high_confidence``
        counts on the return value and reported ``candidates: 1
        auto_verified: 0`` over writes that had all failed.
        """
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelSportMarketLink)
                .filter_by(id=link_id)
                .one_or_none()
            )
            if row is None:
                return False
            row.verified = 1 if verified else 0
            row.updated_at = _utcnow()
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_links(
        self,
        *,
        source: str | None = None,
        verified: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List all links, optionally filtered by source/verified.

        Backs the operator's whole links board, where ``[]`` reads as "no links
        exist yet"; a query failure escapes so the panel's error branch runs.
        """
        session = get_kernel_session()
        try:
            q = session.query(KernelSportMarketLink)
            if source is not None:
                q = q.filter(KernelSportMarketLink.source == source)
            if verified is not None:
                q = q.filter(KernelSportMarketLink.verified == (1 if verified else 0))
            rows = q.order_by(KernelSportMarketLink.updated_at.desc()).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_matches_with_verified_links(self) -> list[str]:
        """Return distinct match_ids that have at least one verified=True link.

        Used by the edge detector scheduler job to enumerate matches that
        need edge recomputation. Additive — does not modify existing methods.

        The three scheduler jobs that enumerate from here treat ``[]`` as "no
        linked matches" and wrote ``status="success" matches_total=0`` into the
        run ledger; measured with the table dropped, that even *upgraded*
        ``sport_market_odds_fetch`` from its healthy ``status="failed"`` (which
        carried a real diagnostic) to a green run. Each job's outer handler
        turns an escaping failure into ``status="failed"`` with the error.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink.match_id)
                .filter(KernelSportMarketLink.verified == 1)
                .distinct()
                .all()
            )
            return [r[0] for r in rows]
        finally:
            session.close()

    def auto_verify_high_confidence(
        self,
        *,
        min_confidence: float = 0.95,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Auto-verify pending links with confidence >= threshold (P1-V2).

        Fail-closed default: only promotes already-persisted high-confidence
        pending rows; does not invent links. Returns counts + ids.
        """
        pending = self.get_pending_links()
        candidates: list[dict[str, Any]] = []
        for link in pending:
            try:
                conf = float(link.get("link_confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf >= float(min_confidence):
                candidates.append(link)

        verified_ids: list[int] = []
        if not dry_run:
            for link in candidates:
                lid = link.get("id")
                if lid is None:
                    continue
                ok = self.set_verified(link_id=int(lid), verified=True)
                if ok:
                    verified_ids.append(int(lid))
        else:
            verified_ids = [
                int(link["id"]) for link in candidates if link.get("id") is not None
            ]

        return {
            "pending_total": len(pending),
            "candidates": len(candidates),
            "auto_verified": 0 if dry_run else len(verified_ids),
            "would_verify": len(verified_ids) if dry_run else len(verified_ids),
            "threshold": float(min_confidence),
            "dry_run": dry_run,
            "link_ids": verified_ids,
        }


