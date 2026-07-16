"""SportRecommendationService — stateless recommendation engine.

Consumes Subproject B's persisted edges (kernel_sport_edges) and produces
SportActionableRecommendation per match. Read-only: never writes to the DB.

Direction (YES/NO/AVOID/WAIT) from raw_edge sign + staleness + risk.
Decision (act/provisional_act/watch/skip) from diagnosis_service.decide()
using B's adjusted_edge (0-1 → 0-100 pp conversion).

Zero-invasion: does not modify B's code, event pipeline's
ActionableRecommendation, diagnosis_service, or decision_quality_service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core import config
from app.kernel.edge_store import EdgeStore
from app.kernel.kernel_db import get_calibration, get_latest_prediction
from app.services.diagnosis_service import decide


@dataclass(frozen=True)
class SportActionableRecommendation:
    """Per-match actionable recommendation derived from B's edge data.

    Parallel to the event pipeline's ActionableRecommendation but with
    sports-specific fields (mapped_outcome, decision, engine_name, competition).
    Never persisted — computed on-demand from kernel_sport_edges.
    """
    match_id: str
    mapped_outcome: str
    direction: str
    decision: str
    confidence: str
    risk_level: str
    edge_pct: float
    raw_edge_pct: float
    trust: float
    liquidity_factor: float
    stale: bool
    suggested_allocation_pct: float
    calibration_status: str
    rationale: str
    engine_name: str | None
    competition: str | None
    prediction_timestamp: datetime | None
    model_prob: float
    market_prob: float
    sources_count: int
    captured_at: datetime


# ---------------------------------------------------------------------------
# Pure helper functions (testable without DB)
# ---------------------------------------------------------------------------

def _select_primary_outcome(edges: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the edge with the largest |adjusted_edge|."""
    if not edges:
        return None
    return max(edges, key=lambda e: abs(e.get("adjusted_edge", 0.0)))


def _derive_direction(raw_edge: float, stale: bool, risk_level: str) -> str:
    """Derive YES/NO/WAIT/AVOID from edge sign, staleness, and risk."""
    if stale:
        return "WAIT"
    if risk_level == "high":
        return "AVOID"
    if raw_edge > 0:
        return "YES"
    if raw_edge < 0:
        return "NO"
    return "WAIT"


def _compute_risk_level(liquidity_factor: float, trust: float, stale: bool) -> str:
    """High risk when liquidity or trust is low, or data is stale."""
    if stale or liquidity_factor < 0.2 or trust < 0.2:
        return "high"
    if liquidity_factor < 0.5 or trust < 0.5:
        return "medium"
    return "low"


def _compute_confidence(adjusted_edge_pct: float, trust: float) -> str:
    """Confidence scales with both edge magnitude and trust."""
    score = abs(adjusted_edge_pct) * trust
    if score >= 4.0:
        return "high"
    if score >= 2.0:
        return "medium"
    return "low"


def _compute_allocation(adjusted_edge_pct: float, risk_level: str, decision: str) -> float:
    """Kelly-inspired fractional allocation, capped at 2% of bankroll.

    Returns value in 0-2 scale (capped at 2% of bankroll).
    Zero when decision is skip or risk is high.
    """
    if decision == "skip" or risk_level == "high":
        return 0.0
    base = min(abs(adjusted_edge_pct) / config.settings.DECISION_ACT_EDGE, 2.0)
    if risk_level == "medium":
        base *= 0.5
    return round(base, 2)


def _build_rationale(
    direction: str,
    mapped_outcome: str,
    edge_pct: float,
    trust: float,
    liquidity_factor: float,
    stale: bool,
    decision: str,
) -> str:
    """Deterministic Chinese rationale (no LLM)."""
    outcome_zh = {"home_win": "主胜", "draw": "平局", "away_win": "客胜"}
    outcome_label = outcome_zh.get(mapped_outcome, mapped_outcome)

    if stale:
        return "数据过期，建议等待最新市场快照后再决策。"

    if direction == "AVOID":
        return "市场流动性不足或模型可信度低，建议规避。"

    if direction == "WAIT":
        return "模型与市场概率接近，无明显边际优势，建议观望。"

    action = "看好" if direction == "YES" else "看淡"
    confidence_desc = "高置信" if trust >= 0.7 else "中等置信" if trust >= 0.5 else "低置信"
    liquidity_desc = (
        "流动性充足" if liquidity_factor >= 0.5
        else "流动性一般" if liquidity_factor >= 0.2
        else "流动性不足"
    )

    return (
        f"模型{action}{outcome_label}，"
        f"调整后边际 {edge_pct:+.2f}pp，"
        f"{confidence_desc}（trust={trust:.2f}），"
        f"{liquidity_desc}。"
        f"决策建议：{decision}。"
        f"本分析仅供参考，不构成投资建议。"
    )


# ---------------------------------------------------------------------------
# Service class (DB-integrated, stateless)
# ---------------------------------------------------------------------------

class SportRecommendationService:
    """Stateless service that computes SportActionableRecommendation from B's edges.

    Reads-only: never writes to the database. All data comes from
    EdgeStore (B's persisted edges) + KernelPrediction + KernelCalibration.
    """

    def __init__(self) -> None:
        self._edge_store = EdgeStore()

    def get_recommendation(self, match_id: str) -> SportActionableRecommendation | None:
        """Compute recommendation for a single match.

        Returns None when no persisted edges exist for this match.
        """
        edges = self._edge_store.get_latest_edges(match_id)
        if not edges:
            return None
        primary = _select_primary_outcome(edges)
        if primary is None:
            return None
        return self._edge_dict_to_recommendation(primary, match_id)

    def get_open_decisions(
        self,
        limit: int = 20,
        decision: str | None = None,
    ) -> list[SportActionableRecommendation]:
        """List matches with open decisions (act/provisional_act/watch).

        Over-fetches 3x limit to account for multiple outcomes per match,
        then deduplicates by match_id keeping the primary outcome.
        """
        fetch_limit = limit * 3
        rows = self._edge_store.get_top_discrepancies(limit=fetch_limit, min_abs_edge=0.0)

        # Deduplicate by match_id, keeping the one with max |adjusted_edge|
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            mid = row.get("match_id", "")
            if mid not in seen or abs(row.get("adjusted_edge", 0.0)) > abs(seen[mid].get("adjusted_edge", 0.0)):
                seen[mid] = row

        # Build recommendations
        recs: list[SportActionableRecommendation] = []
        for row in seen.values():
            rec = self._edge_dict_to_recommendation(row, row.get("match_id", ""))
            if rec is None:
                continue
            # Filter: open decisions exclude "skip"
            if rec.decision == "skip":
                continue
            # Filter by specific decision if requested
            if decision is not None and rec.decision != decision:
                continue
            recs.append(rec)
            if len(recs) >= limit:
                break
        return recs

    def get_top_picks(
        self,
        limit: int = 20,
        min_abs_edge_pct: float = 0.0,
    ) -> list[SportActionableRecommendation]:
        """List top edge picks (largest |adjusted_edge|), regardless of decision.

        min_abs_edge_pct is on 0-100 pp scale.
        """
        # Convert pp to 0-1 for EdgeStore's API
        min_abs_edge_01 = min_abs_edge_pct / 100.0
        fetch_limit = limit * 3
        rows = self._edge_store.get_top_discrepancies(
            limit=fetch_limit, min_abs_edge=min_abs_edge_01
        )

        # Deduplicate by match_id, keeping the one with max |adjusted_edge|
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            mid = row.get("match_id", "")
            if mid not in seen or abs(row.get("adjusted_edge", 0.0)) > abs(seen[mid].get("adjusted_edge", 0.0)):
                seen[mid] = row

        recs: list[SportActionableRecommendation] = []
        for row in seen.values():
            rec = self._edge_dict_to_recommendation(row, row.get("match_id", ""))
            if rec is None:
                continue
            recs.append(rec)
            if len(recs) >= limit:
                break
        return recs

    def _edge_dict_to_recommendation(
        self, edge: dict[str, Any], match_id: str
    ) -> SportActionableRecommendation | None:
        """Convert a persisted edge row dict to SportActionableRecommendation."""
        raw_edge = float(edge.get("raw_edge", 0.0))
        adjusted_edge = float(edge.get("adjusted_edge", 0.0))
        trust = float(edge.get("trust", 0.5))
        liquidity_factor = float(edge.get("liquidity_factor", 1.0))
        stale = bool(edge.get("stale", False))

        # Convert B's 0-1 scale to 0-100 pp
        adjusted_edge_pct = adjusted_edge * 100
        raw_edge_pct = raw_edge * 100

        # Determine qualified from calibration
        qualified, engine_name, competition, prediction_timestamp = self._get_metadata(match_id)

        # Risk level
        risk_level = _compute_risk_level(liquidity_factor, trust, stale)

        # Direction
        direction = _derive_direction(raw_edge, stale, risk_level)

        # Decision gate (reuse diagnosis_service.decide)
        decision = decide(
            adjusted_edge=adjusted_edge_pct,
            qualified=qualified,
            act_edge=config.settings.DECISION_ACT_EDGE,
            watch_edge=config.settings.DECISION_WATCH_EDGE,
            cold_start_bypass_enabled=config.settings.COLD_START_BYPASS_ENABLED,
        )

        # Confidence
        confidence = _compute_confidence(adjusted_edge_pct, trust)

        # Allocation
        allocation = _compute_allocation(adjusted_edge_pct, risk_level, decision)

        # Calibration status
        calibration_status = "calibrated" if qualified else "uncalibrated_provisional"

        # Rationale
        rationale = _build_rationale(
            direction=direction,
            mapped_outcome=edge.get("mapped_outcome", ""),
            edge_pct=adjusted_edge_pct,
            trust=trust,
            liquidity_factor=liquidity_factor,
            stale=stale,
            decision=decision,
        )

        captured_at = edge.get("captured_at")
        if captured_at is None:
            captured_at = datetime.now(timezone.utc)

        return SportActionableRecommendation(
            match_id=match_id,
            mapped_outcome=edge.get("mapped_outcome", ""),
            direction=direction,
            decision=decision,
            confidence=confidence,
            risk_level=risk_level,
            edge_pct=round(adjusted_edge_pct, 2),
            raw_edge_pct=round(raw_edge_pct, 2),
            trust=trust,
            liquidity_factor=liquidity_factor,
            stale=stale,
            suggested_allocation_pct=allocation,
            calibration_status=calibration_status,
            rationale=rationale,
            engine_name=engine_name,
            competition=competition,
            prediction_timestamp=prediction_timestamp,
            model_prob=float(edge.get("model_prob", 0.0)),
            market_prob=float(edge.get("market_prob", 0.0)),
            sources_count=int(edge.get("sources_count", 0)),
            captured_at=captured_at,
        )

    def _get_metadata(
        self, match_id: str
    ) -> tuple[bool, str | None, str | None, datetime | None]:
        """Query KernelPrediction + KernelCalibration for metadata.

        Returns (qualified, engine_name, competition, prediction_timestamp).
        When prediction is gone, returns (False, None, None, None).
        """
        pred = get_latest_prediction(match_id)
        if pred is None:
            return (False, None, None, None)

        calibration = get_calibration(pred.engine, pred.competition)
        if calibration is None:
            return (False, pred.engine, pred.competition, pred.created_at)

        qualified = calibration.sample_count >= config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
        return (qualified, pred.engine, pred.competition, pred.created_at)
