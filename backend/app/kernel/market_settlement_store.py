"""Persistence for kernel_market_settlements + kernel_market_calibrations tables.

Mirrors the edge_store / sport_market_link_store pattern: append-only writes,
dict returns, session-per-call. D writes only to these 2 tables.

Query failures propagate. Every read here has an empty result as its *normal*
answer — no settlement yet, no calibration yet — so returning empty on failure
made a degraded DB indistinguishable from a cold start at every caller, and each
caller reads empty as a fact about the world rather than about the query. The
two writers already ``rollback(); raise``, and the four query helpers in
``market_settlement_service`` already document the same rule; these reads were
the remaining exception.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelMarketSettlement,
    KernelMarketCalibration,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _settlement_to_dict(row: KernelMarketSettlement) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "mapped_outcome": row.mapped_outcome,
        "engine": row.engine,
        "competition": row.competition,
        "settlement_implied_prob": row.settlement_implied_prob,
        "settlement_captured_at": row.settlement_captured_at,
        "link_id": row.link_id,
        "model_prob": row.model_prob,
        "market_prob_at_detection": row.market_prob_at_detection,
        "raw_edge": row.raw_edge,
        "adjusted_edge": row.adjusted_edge,
        "brier_score": row.brier_score,
        "signed_error": row.signed_error,
        "direction_correct": row.direction_correct,
        "status": row.status,
        "skip_reason": row.skip_reason,
        "match_finished_at": row.match_finished_at,
        "processed_at": row.processed_at,
    }


def _calibration_to_dict(row: KernelMarketCalibration) -> dict[str, Any]:
    return {
        "id": row.id,
        "engine": row.engine,
        "competition": row.competition,
        "slope": row.slope,
        "intercept": row.intercept,
        "sample_count": row.sample_count,
        "avg_brier": row.avg_brier,
        "avg_signed_error": row.avg_signed_error,
        "direction_accuracy": row.direction_accuracy,
        "last_updated": row.last_updated,
    }


class MarketSettlementStore:
    """Persistence for settlement records and market calibrations."""

    def append_settlement(
        self,
        *,
        match_id: str,
        mapped_outcome: str,
        engine: str,
        competition: str,
        settlement_implied_prob: float | None,
        settlement_captured_at: datetime | None,
        link_id: int | None,
        model_prob: float | None,
        market_prob_at_detection: float | None,
        raw_edge: float | None,
        adjusted_edge: float | None,
        brier_score: float | None,
        signed_error: float | None,
        direction_correct: int | None,
        status: str,
        skip_reason: str | None,
        match_finished_at: datetime,
        processed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a settlement row. Returns the inserted row as dict.

        Caller is responsible for idempotency check (get_settlement before append).
        Unique constraint on (match_id, mapped_outcome) provides DB-level safety.
        """
        when = processed_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelMarketSettlement(
                match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                competition=competition,
                settlement_implied_prob=settlement_implied_prob,
                settlement_captured_at=settlement_captured_at, link_id=link_id,
                model_prob=model_prob, market_prob_at_detection=market_prob_at_detection,
                raw_edge=raw_edge, adjusted_edge=adjusted_edge, brier_score=brier_score,
                signed_error=signed_error, direction_correct=direction_correct,
                status=status, skip_reason=skip_reason,
                match_finished_at=match_finished_at, processed_at=when,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _settlement_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_settlement(self, match_id: str) -> list[dict[str, Any]]:
        """All settlement rows for a match.

        ``[]`` means the match has no settlement rows. A query failure is
        raised, NOT swallowed: ``MarketSettlementService.process_settlement``
        uses this as its idempotency check, so an empty list is what tells it
        the match has never been settled. Measured on a DB whose settlement
        table had drifted, a match already holding a settlement row read as 0
        rows while raw SQL showed 1, so ``process_settlement`` walked past its
        idempotency check and attempted the re-write; the INSERT then raised
        ``OperationalError`` from inside the *writer*. No mechanism was found
        that fails this read and then lets the write land — WAL readers do not
        block on a writer, so the drift that breaks the read breaks the INSERT
        too — which means the swallow never produced duplicate rows. What it
        produced was the error surfacing one layer too late, as a write failure
        on a match that needed no write, with the read that actually failed
        reported as a fact about the world. The route also maps ``[]`` to 404
        "No settlements found for match.", which states as fact something the
        query never established.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSettlement)
                .filter_by(match_id=match_id)
                .order_by(KernelMarketSettlement.mapped_outcome.asc())
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_settlements_for_calibration(
        self, engine: str, competition: str, limit: int
    ) -> list[dict[str, Any]]:
        """Recent processed settlements for (engine, competition), most recent first.

        Only returns rows where status='processed' and brier_score IS NOT NULL.

        A query failure is raised, NOT swallowed: ``[]`` is below
        MIN_SAMPLES_FOR_MARKET_CALIBRATION, so ``_update_market_calibration``
        returns without writing and without logging. Measured against a DB
        holding 12 processed rows and a calibration fitted from them, breaking
        this read left the previous calibration in place with its old
        ``last_updated`` — the stale fit stays live and reads as current.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSettlement)
                .filter(
                    KernelMarketSettlement.engine == engine,
                    KernelMarketSettlement.competition == competition,
                    KernelMarketSettlement.status == "processed",
                    KernelMarketSettlement.brier_score.isnot(None),
                )
                .order_by(KernelMarketSettlement.processed_at.desc())
                .limit(limit)
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        finally:
            session.close()

    def upsert_calibration(
        self,
        *,
        engine: str,
        competition: str,
        slope: float,
        intercept: float,
        sample_count: int,
        avg_brier: float,
        avg_signed_error: float,
        direction_accuracy: float,
        last_updated: datetime,
    ) -> dict[str, Any]:
        """Upsert a market calibration row keyed by (engine, competition)."""
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelMarketCalibration)
                .filter_by(engine=engine, competition=competition)
                .one_or_none()
            )
            if row is not None:
                row.slope = slope
                row.intercept = intercept
                row.sample_count = sample_count
                row.avg_brier = avg_brier
                row.avg_signed_error = avg_signed_error
                row.direction_accuracy = direction_accuracy
                row.last_updated = last_updated
            else:
                row = KernelMarketCalibration(
                    engine=engine, competition=competition, slope=slope,
                    intercept=intercept, sample_count=sample_count, avg_brier=avg_brier,
                    avg_signed_error=avg_signed_error, direction_accuracy=direction_accuracy,
                    last_updated=last_updated,
                )
                session.add(row)
            session.commit()
            session.refresh(row)
            return _calibration_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_calibrations(
        self, engine: str | None = None, competition: str | None = None
    ) -> list[dict[str, Any]]:
        """List calibrations, optionally filtered.

        A query failure is raised, NOT swallowed. ``calibration_fusion_service``
        turns ``[]`` into ``market_has_data = False`` and reports
        DIAGNOSIS_DORMANT_TRUST — a sentinel its own ``_source_trust`` docstring
        defines as "no usable estimate", here standing in for an estimate that
        exists and was simply unreadable. Measured on a market channel with a
        real ``direction_accuracy`` and 12 samples, with Phase 3 below its MIN:
        a channel measured at 1.00 dropped to 0.50 trust, and one measured at
        0.167 rose to 0.50 — so ``adjusted_edge = raw_edge * trust * liquidity``
        halved for the good engine and tripled for the bad one, and ``source``
        still named a channel as the basis. The swallow was also silent, unlike
        ``kernel_db.get_calibration``, which logs before returning ``None``.
        """
        session = get_kernel_session()
        try:
            q = session.query(KernelMarketCalibration)
            if engine is not None:
                q = q.filter(KernelMarketCalibration.engine == engine)
            if competition is not None:
                q = q.filter(KernelMarketCalibration.competition == competition)
            rows = q.order_by(KernelMarketCalibration.last_updated.desc()).all()
            return [_calibration_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_history(self, limit: int, engine: str | None = None) -> list[dict[str, Any]]:
        """Recent settlements, most recent first.

        A query failure is raised, NOT swallowed: the route returns
        ``{"items": [], "total": 0}`` and the CLI prints ``[INFO] no settlements
        found`` and exits 0, both of which read as "the channel has produced
        nothing yet" — the normal state of this table.
        """
        session = get_kernel_session()
        try:
            q = session.query(KernelMarketSettlement)
            if engine is not None:
                q = q.filter(KernelMarketSettlement.engine == engine)
            rows = (
                q.order_by(KernelMarketSettlement.processed_at.desc())
                .limit(limit)
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        finally:
            session.close()

    def get_processed_match_ids(self) -> set[str]:
        """Set of match_ids that already have settlement rows (for scan dedup).

        A query failure is raised, NOT swallowed, for consistency with the other
        reads here. This method currently has no callers —
        ``_find_finished_matches_without_settlements`` does the same dedup with a
        subquery — so the rule is stated rather than measured.
        """
        session = get_kernel_session()
        try:
            rows = session.query(KernelMarketSettlement.match_id).distinct().all()
            return {r[0] for r in rows}
        finally:
            session.close()
