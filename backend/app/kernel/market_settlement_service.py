"""Market settlement feedback service.

Reads B's persisted edges + A's market snapshots + Phase 3's match outcomes
(all read-only), computes market-settlement-based error signals, and writes to
kernel_market_settlements + kernel_market_calibrations (D's own tables only).

Parallel channel: Phase 3's match-outcome learning continues unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core import config
from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelSportMarketLink,
    KernelMarketSnapshot,
    KernelMarketSettlement,
    get_kernel_session,
    get_latest_prediction,
)
from app.kernel.edge_store import EdgeStore
from app.kernel.market_settlement_store import MarketSettlementStore

logger = logging.getLogger(__name__)

_CALIBRATION_SLOPE_MIN = 0.0
_CALIBRATION_SLOPE_MAX = 2.0
_CALIBRATION_INTERCEPT_MIN = -0.5
_CALIBRATION_INTERCEPT_MAX = 0.5


@dataclass(frozen=True)
class SettlementResult:
    """Result of processing a single match's settlement."""
    match_id: str
    status: str
    settlements_count: int
    skip_reason: str | None


@dataclass(frozen=True)
class ScanResult:
    """Result of a batch scan."""
    scanned: int
    processed: int
    skipped: int
    already_processed: int
    errors: int
    error_details: list[str]


def _compute_brier(model_prob: float, settlement_implied_prob: float) -> float:
    """Brier-style score: (model_prob - settlement_implied_prob)^2."""
    return round((model_prob - settlement_implied_prob) ** 2, 6)


def _compute_signed_error(model_prob: float, settlement_implied_prob: float) -> float:
    """Signed error: model_prob - settlement_implied_prob."""
    return round(model_prob - settlement_implied_prob, 6)


def _compute_direction_correct(
    raw_edge: float, market_prob: float, settlement_implied_prob: float
) -> int:
    """Did the edge direction match the market resolution?

    Edge direction: sign(raw_edge). Market resolution direction:
    sign(settlement_implied_prob - market_prob). Correct if both non-zero and match.
    """
    edge_sign = 1 if raw_edge > 0 else (-1 if raw_edge < 0 else 0)
    market_move = settlement_implied_prob - market_prob
    market_sign = 1 if market_move > 0 else (-1 if market_move < 0 else 0)
    return 1 if edge_sign == market_sign and edge_sign != 0 else 0


def _update_market_calibration(
    store: MarketSettlementStore, engine: str, competition: str
) -> None:
    """Fit linear regression on recent settlements and upsert calibration.

    x = model_prob, y = settlement_implied_prob
    slope clamped to [0.0, 2.0], intercept clamped to [-0.5, 0.5].
    """
    settlements = store.get_settlements_for_calibration(
        engine, competition, limit=config.settings.MARKET_CALIBRATION_WINDOW_SIZE
    )
    if len(settlements) < config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION:
        return

    xs = [s["model_prob"] for s in settlements]
    ys = [s["settlement_implied_prob"] for s in settlements]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den != 0 else 1.0
    intercept = mean_y - slope * mean_x

    slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
    intercept = max(_CALIBRATION_INTERCEPT_MIN, min(_CALIBRATION_INTERCEPT_MAX, intercept))

    avg_brier = sum(s["brier_score"] for s in settlements) / n
    avg_signed_error = sum(s["signed_error"] for s in settlements) / n
    direction_accuracy = sum(s["direction_correct"] for s in settlements) / n

    store.upsert_calibration(
        engine=engine, competition=competition, slope=round(slope, 4),
        intercept=round(intercept, 4), sample_count=n,
        avg_brier=round(avg_brier, 6), avg_signed_error=round(avg_signed_error, 6),
        direction_accuracy=round(direction_accuracy, 4),
        last_updated=datetime.now(timezone.utc),
    )


def _find_settlement_snapshot(link_id: int, finished_at: datetime) -> dict[str, Any] | None:
    """Find the last market snapshot before the match finished.

    Queries kernel_market_snapshots directly (read-only).
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelMarketSnapshot)
            .filter(
                KernelMarketSnapshot.link_id == link_id,
                KernelMarketSnapshot.captured_at <= finished_at,
            )
            .order_by(KernelMarketSnapshot.captured_at.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "id": row.id, "link_id": row.link_id, "implied_prob": row.implied_prob,
            "price": row.price, "liquidity": row.liquidity, "volume": row.volume,
            "captured_at": row.captured_at,
        }
    except Exception:
        return None
    finally:
        session.close()


def _find_verified_link_for_outcome(
    match_id: str, mapped_outcome: str
) -> dict[str, Any] | None:
    """Find the best verified market link for a match's outcome.

    Picks the link with highest link_confidence among verified links.
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelSportMarketLink)
            .filter(
                KernelSportMarketLink.match_id == match_id,
                KernelSportMarketLink.mapped_outcome == mapped_outcome,
                KernelSportMarketLink.verified == 1,
            )
            .order_by(KernelSportMarketLink.link_confidence.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "id": row.id, "match_id": row.match_id, "contract_id": row.contract_id,
            "source": row.source, "outcome_label": row.outcome_label,
            "mapped_outcome": row.mapped_outcome, "link_method": row.link_method,
            "link_confidence": row.link_confidence, "verified": row.verified,
            "market_question": row.market_question, "implied_prob": row.implied_prob,
            "created_at": row.created_at, "updated_at": row.updated_at,
        }
    except Exception:
        return None
    finally:
        session.close()


def _find_finished_matches_without_settlements(limit: int) -> list[dict[str, Any]]:
    """Find finished matches that don't have settlement rows yet."""
    session = get_kernel_session()
    try:
        processed_select = select(KernelMarketSettlement.match_id).distinct()
        rows = (
            session.query(KernelMatchOutcome)
            .filter(
                KernelMatchOutcome.finished_at.isnot(None),
                ~KernelMatchOutcome.match_id.in_(processed_select),
            )
            .order_by(KernelMatchOutcome.finished_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {"match_id": r.match_id, "outcome": r.outcome, "finished_at": r.finished_at}
            for r in rows
        ]
    except Exception:
        return []
    finally:
        session.close()


class MarketSettlementService:
    """Market settlement feedback service."""

    def __init__(self) -> None:
        self._edge_store = EdgeStore()
        self._store = MarketSettlementStore()

    def process_settlement(self, match_id: str) -> SettlementResult:
        """Process a single match's market settlement. Idempotent."""
        # Check if already processed
        existing = self._store.get_settlement(match_id)
        if existing:
            return SettlementResult(
                match_id=match_id, status="already_processed",
                settlements_count=0, skip_reason=None,
            )

        # Read match outcome
        session = get_kernel_session()
        try:
            outcome_row = (
                session.query(KernelMatchOutcome)
                .filter_by(match_id=match_id)
                .one_or_none()
            )
        finally:
            session.close()

        if outcome_row is None or outcome_row.finished_at is None:
            return SettlementResult(
                match_id=match_id, status="skipped_not_finished",
                settlements_count=0, skip_reason="Match not finished or no outcome recorded.",
            )

        finished_at = outcome_row.finished_at

        # Read prediction for engine/competition metadata
        prediction = get_latest_prediction(match_id)
        if prediction is None:
            return SettlementResult(
                match_id=match_id, status="skipped_no_edges",
                settlements_count=0, skip_reason="No prediction found for match.",
            )
        engine = prediction.engine
        competition = prediction.competition

        # Read B's edges
        edges = self._edge_store.get_latest_edges(match_id)
        if not edges:
            return SettlementResult(
                match_id=match_id, status="skipped_no_edges",
                settlements_count=0, skip_reason="No edges found for match.",
            )

        # Process each edge's mapped_outcome
        settlements_count = 0
        for edge in edges:
            mapped_outcome = edge["mapped_outcome"]
            # Find verified link for this outcome
            link = _find_verified_link_for_outcome(match_id, mapped_outcome)
            if link is None:
                # Skip: no verified link — insert a skipped settlement row
                self._store.append_settlement(
                    match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                    competition=competition, settlement_implied_prob=None,
                    settlement_captured_at=None, link_id=None,
                    model_prob=edge["model_prob"], market_prob_at_detection=edge["market_prob"],
                    raw_edge=edge["raw_edge"], adjusted_edge=edge["adjusted_edge"],
                    brier_score=None, signed_error=None, direction_correct=None,
                    status="skipped_no_links",
                    skip_reason=f"No verified link for outcome {mapped_outcome}.",
                    match_finished_at=finished_at,
                )
                settlements_count += 1
                continue

            # Find last snapshot before finished_at
            snapshot = _find_settlement_snapshot(link["id"], finished_at)
            if snapshot is None:
                self._store.append_settlement(
                    match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                    competition=competition, settlement_implied_prob=None,
                    settlement_captured_at=None, link_id=link["id"],
                    model_prob=edge["model_prob"], market_prob_at_detection=edge["market_prob"],
                    raw_edge=edge["raw_edge"], adjusted_edge=edge["adjusted_edge"],
                    brier_score=None, signed_error=None, direction_correct=None,
                    status="skipped_no_snapshot",
                    skip_reason=f"No snapshot before {finished_at} for link {link['id']}.",
                    match_finished_at=finished_at,
                )
                settlements_count += 1
                continue

            # Compute error signals
            settlement_prob = snapshot["implied_prob"]
            model_prob = edge["model_prob"]
            market_prob = edge["market_prob"]
            raw_edge = edge["raw_edge"]
            adjusted_edge = edge["adjusted_edge"]

            brier = _compute_brier(model_prob, settlement_prob)
            signed_err = _compute_signed_error(model_prob, settlement_prob)
            dir_correct = _compute_direction_correct(raw_edge, market_prob, settlement_prob)

            self._store.append_settlement(
                match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                competition=competition, settlement_implied_prob=settlement_prob,
                settlement_captured_at=snapshot["captured_at"], link_id=link["id"],
                model_prob=model_prob, market_prob_at_detection=market_prob,
                raw_edge=raw_edge, adjusted_edge=adjusted_edge,
                brier_score=brier, signed_error=signed_err, direction_correct=dir_correct,
                status="processed", skip_reason=None,
                match_finished_at=finished_at,
            )
            settlements_count += 1

        # Update calibration for this engine/competition
        _update_market_calibration(self._store, engine, competition)

        return SettlementResult(
            match_id=match_id, status="processed",
            settlements_count=settlements_count, skip_reason=None,
        )

    def scan_and_process(self, limit: int = 50) -> ScanResult:
        """Scan for finished matches without settlements, process them in batch."""
        matches = _find_finished_matches_without_settlements(limit)
        scanned = len(matches)
        processed = 0
        skipped = 0
        already = 0
        errors = 0
        error_details: list[str] = []

        for m in matches:
            try:
                result = self.process_settlement(m["match_id"])
                if result.status == "processed":
                    processed += 1
                elif result.status == "already_processed":
                    already += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors += 1
                if len(error_details) < 10:
                    error_details.append(f"{m['match_id']}: {exc}")
                logger.error(f"Settlement processing failed for {m['match_id']}: {exc}")

        return ScanResult(
            scanned=scanned, processed=processed, skipped=skipped,
            already_processed=already, errors=errors, error_details=error_details,
        )

    def get_settlement(self, match_id: str) -> list[dict[str, Any]]:
        """Get all settlement records for a match."""
        return self._store.get_settlement(match_id)

    def get_calibrations(
        self, engine: str | None = None, competition: str | None = None
    ) -> list[dict[str, Any]]:
        """Get market calibrations, optionally filtered."""
        return self._store.get_calibrations(engine=engine, competition=competition)

    def get_history(
        self, limit: int = 20, engine: str | None = None
    ) -> list[dict[str, Any]]:
        """Get recent settlements (most recent first)."""
        return self._store.get_history(limit=limit, engine=engine)
