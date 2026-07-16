"""Persistence for kernel_market_settlements + kernel_market_calibrations tables.

Mirrors the edge_store / sport_market_link_store pattern: append-only writes,
dict returns, session-per-call. D writes only to these 2 tables.
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
        """All settlement rows for a match."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSettlement)
                .filter_by(match_id=match_id)
                .order_by(KernelMarketSettlement.mapped_outcome.asc())
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_settlements_for_calibration(
        self, engine: str, competition: str, limit: int
    ) -> list[dict[str, Any]]:
        """Recent processed settlements for (engine, competition), most recent first.

        Only returns rows where status='processed' and brier_score IS NOT NULL.
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
        except Exception:
            return []
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
        """List calibrations, optionally filtered."""
        session = get_kernel_session()
        try:
            q = session.query(KernelMarketCalibration)
            if engine is not None:
                q = q.filter(KernelMarketCalibration.engine == engine)
            if competition is not None:
                q = q.filter(KernelMarketCalibration.competition == competition)
            rows = q.order_by(KernelMarketCalibration.last_updated.desc()).all()
            return [_calibration_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_history(self, limit: int, engine: str | None = None) -> list[dict[str, Any]]:
        """Recent settlements, most recent first."""
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
        except Exception:
            return []
        finally:
            session.close()

    def get_processed_match_ids(self) -> set[str]:
        """Set of match_ids that already have settlement rows (for scan dedup)."""
        session = get_kernel_session()
        try:
            rows = session.query(KernelMarketSettlement.match_id).distinct().all()
            return {r[0] for r in rows}
        except Exception:
            return set()
        finally:
            session.close()
