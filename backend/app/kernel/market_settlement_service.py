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
    KernelPredictionHistory,
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
) -> int | None:
    """Did the edge direction match the market resolution? ``None`` if there was none.

    Edge direction: sign(raw_edge). Market resolution direction:
    sign(settlement_implied_prob - market_prob). Correct if both non-zero and match.

    A zero ``raw_edge`` is **no directional call, not a wrong one**: the model
    landed on the market price, so there is no direction to be right or wrong
    about. Scoring it 0 said the engine had been mistaken, and since
    ``_update_market_calibration`` divided by every processed row, each such row
    pulled ``direction_accuracy`` down — a number
    ``calibration_fusion_service._compute_market_trust`` reads straight back as
    engine trust. ``raw_edge`` is ``model_prob - market_prob`` with no threshold
    (``edge_detector_service.py:170``), so a market-echoing engine produces these
    rows in bulk.

    ``None`` is what the rest of the repo already means by a non-directional row
    (``quality_metrics_report_service.slice_metrics``,
    ``prediction_store.get_calibration_buckets``,
    ``calibration_drift_service._cell_metrics`` all skip it in the mean),
    ``kernel_market_settlements.direction_correct`` is already nullable, and
    ``SettlementHistoryTable`` already renders it as "—" rather than "✗".

    A zero *market* move deliberately stays 0: an unmoved closing line is not a
    confirmation of the edge, which is the ordinary CLV reading. That is a
    definition rather than an oversight — do not fold it into the ``None`` branch
    without settling that question first.
    """
    edge_sign = 1 if raw_edge > 0 else (-1 if raw_edge < 0 else 0)
    if edge_sign == 0:
        return None
    market_move = settlement_implied_prob - market_prob
    market_sign = 1 if market_move > 0 else (-1 if market_move < 0 else 0)
    return 1 if edge_sign == market_sign else 0


def _update_market_calibration(
    store: MarketSettlementStore, engine: str, competition: str
) -> None:
    """Fit linear regression on recent settlements and upsert calibration.

    x = model_prob, y = settlement_implied_prob
    slope clamped to [0.0, 2.0], intercept clamped to [-0.5, 0.5].

    ``direction_accuracy`` is averaged over the rows that actually made a
    directional call, so ``sample_count`` (the regression's n) can exceed the
    count behind the accuracy. Publishing both numbers would need a
    ``directional_count`` column, and kernel tables have no ALTER TABLE path in
    this repo — so when *no* row is directional the calibration is not written at
    all rather than reported as 0.0 accuracy, which
    ``_compute_market_trust`` would read as a measured total failure.
    ``kernel_market_calibrations.direction_accuracy`` is ``nullable=False``, so
    there is no in-schema way to say "not measured".
    """
    settlements = store.get_settlements_for_calibration(
        engine, competition, limit=config.settings.MARKET_CALIBRATION_WINDOW_SIZE
    )
    if len(settlements) < config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION:
        return

    directional = [
        s["direction_correct"] for s in settlements if s["direction_correct"] is not None
    ]
    if not directional:
        logger.info(
            "Market calibration for %s/%s not written: none of %d settlements made a "
            "directional call (raw_edge == 0 throughout).",
            engine, competition, len(settlements),
        )
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
    direction_accuracy = sum(directional) / len(directional)

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

    Returns None only when no such snapshot exists. A query failure is raised,
    NOT swallowed, for the same reason as _find_verified_link_for_outcome: the
    caller writes a permanent ``skipped_no_snapshot`` settlement row on None,
    and that row excludes the match from every later scan.
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
    finally:
        session.close()


def _find_verified_link_for_outcome(
    match_id: str, mapped_outcome: str
) -> dict[str, Any] | None:
    """Find the best verified market link for a match's outcome.

    Picks the link with highest link_confidence among verified links.

    Returns None only when no such link exists. A query failure is raised, NOT
    swallowed: the caller writes a permanent ``skipped_no_links`` settlement
    row on None, and ``_find_finished_matches_without_settlements`` excludes
    matches that already have settlement rows — so a transient DB error would
    be recorded as a final verdict and the match would never be rescanned.
    Letting it raise leaves no row; ``scan_and_process`` counts the match as an
    error and the next scan retries it.
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
    finally:
        session.close()


def _resolve_engine_at(match_id: str, when: datetime) -> str | None:
    """Which engine was live for ``match_id`` at ``when``.

    Returns the ``engine`` of the newest ``kernel_prediction_history`` row with
    ``created_at <= when``, or ``None`` when the history does not reach back
    that far.

    ``kernel_predictions.engine`` cannot answer this. That table is keyed on
    ``match_id`` alone and ``learning_service.record_prediction`` overwrites the
    column in place, so it holds whichever engine predicted *last* — read after
    ``finished_at``, that is not necessarily the engine whose probability the
    frozen edge froze. ``kernel_prediction_history`` is the record of what was
    actually published: ``record_prediction`` appends one row per prediction in
    the same transaction as the overwrite, and nothing in this repo prunes the
    table. Carrying the label on the edge row instead is not available —
    ``kernel_sport_edges`` has no engine column and kernel tables have no
    ALTER TABLE path here.

    Ordered by ``created_at`` then ``id`` so two rows sharing a timestamp
    resolve to the later insert rather than to whatever the DB returns first.

    A query failure is raised, NOT swallowed, for the same reason as the two
    helpers above: ``None`` makes the caller skip the match, so a swallowed
    error would read as "history does not go back that far".
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelPredictionHistory)
            .filter(
                KernelPredictionHistory.match_id == match_id,
                KernelPredictionHistory.created_at <= when,
            )
            .order_by(
                KernelPredictionHistory.created_at.desc(),
                KernelPredictionHistory.id.desc(),
            )
            .first()
        )
        return None if row is None else row.engine
    finally:
        session.close()


def _find_finished_matches_without_settlements(limit: int) -> list[dict[str, Any]]:
    """Find finished matches that don't have settlement rows yet.

    Returns an empty list only when no finished match awaits settlement. A query
    failure is raised, NOT swallowed, for the same reason as the two helpers
    above — one level up: this list is the entire work queue, so ``[]`` reads as
    "there is nothing to settle". ``scan_and_process`` then reports
    ``scanned=0`` with ``errors=0``, ``_job_process_market_settlements`` records
    a ``success`` run carrying those counts, and the CLI prints
    ``[OK] scanned=0`` and exits 0. A degraded kernel DB would therefore stop
    the entire settlement feedback channel while every entry point kept
    reporting a clean, idle run — the failure is invisible precisely because
    "nothing to do" is the normal state of this queue.
    """
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

        # Read prediction for competition metadata. ``engine`` is deliberately
        # NOT read from here — see _resolve_engine_at. ``competition`` is safe:
        # nothing in the repo assigns KernelPrediction.competition after the
        # insert, so it still describes the match this edge was detected on.
        prediction = get_latest_prediction(match_id)
        if prediction is None:
            return SettlementResult(
                match_id=match_id, status="skipped_no_edges",
                settlements_count=0, skip_reason="No prediction found for match.",
            )
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
        engines_touched: set[str] = set()
        for edge in edges:
            mapped_outcome = edge["mapped_outcome"]
            # Attribute the row to the engine that was live when this edge froze
            # its model_prob, not to whichever engine predicted last.
            engine = _resolve_engine_at(match_id, edge["captured_at"])
            if engine is None:
                # No published prediction precedes this edge, so the engine
                # behind ``model_prob`` cannot be established. Grading it under
                # the current ``kernel_predictions.engine`` would credit or
                # blame an engine on evidence that it may not have produced, and
                # a wrong attribution is worse than a missing one: it pollutes
                # the innocent engine's market calibration *and* hides the
                # responsible one's. So the row is recorded and not graded —
                # ``get_settlements_for_calibration`` filters on
                # ``status == "processed"``, so it never reaches a regression.
                # ``engine`` is ``nullable=False``, hence the current read is
                # still stored; ``status``/``skip_reason`` say it is unverified.
                self._store.append_settlement(
                    match_id=match_id, mapped_outcome=mapped_outcome,
                    engine=prediction.engine,
                    competition=competition, settlement_implied_prob=None,
                    settlement_captured_at=None, link_id=None,
                    model_prob=edge["model_prob"], market_prob_at_detection=edge["market_prob"],
                    raw_edge=edge["raw_edge"], adjusted_edge=edge["adjusted_edge"],
                    brier_score=None, signed_error=None, direction_correct=None,
                    status="skipped_unknown_engine",
                    skip_reason=(
                        f"No prediction history at or before {edge['captured_at']}; "
                        f"engine shown is the current kernel_predictions read and is "
                        f"not verified for this edge."
                    ),
                    match_finished_at=finished_at,
                )
                settlements_count += 1
                continue
            engines_touched.add(engine)
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

        # Update calibration for every engine this match's edges were attributed
        # to. Edges are one row per outcome and all carry the same ``captured_at``
        # (``detect_edges`` stamps one ``now`` per match), so in practice this is
        # one engine — but the set is derived from the rows actually written
        # rather than assumed, because a re-detection between two scans can leave
        # ``get_latest_edges`` holding outcomes from two different runs.
        for touched in sorted(engines_touched):
            _update_market_calibration(self._store, touched, competition)

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
