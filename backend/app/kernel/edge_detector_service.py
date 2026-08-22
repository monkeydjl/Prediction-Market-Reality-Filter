"""EdgeDetectorService — computes model-vs-market edge for sports matches.

Read-only on the Prediction Kernel (uses get_latest_prediction, never
PredictionKernel.predict). Consumes verified market links (Subproject A)
and persisted kernel predictions. Produces per-outcome edge snapshots
persisted to kernel_sport_edges via EdgeStore.

Trust from KernelCalibration.avg_accuracy (sports calibration, not event
segment_skill). Liquidity-weighted multi-source market probability
aggregation. Staleness based on EDGE_STALE_HOURS.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core import config
from app.kernel.edge_store import EdgeStore
from app.kernel.kernel_db import (
    get_calibration,
    get_conditional_calibration_row,
    get_latest_prediction,
)
from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.factor_attribution import (
    extract_factor_drivers,
    format_factor_attribution,
)


# Liquidity ramp floor for edge scoring, deliberately decoupled from the shared
# settings.DIAGNOSIS_LIQUIDITY_FLOOR (which the diagnosis / market-liquidity
# pipeline may set to 10k). The edge detector's ramp is calibrated to a 5000
# floor (2500 -> 0.5, 5000 -> 1.0). Coupling them let a config change in the
# diagnosis pipeline silently flatten every edge's liquidity factor.
_EDGE_LIQUIDITY_FLOOR = 5000.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EdgeSource:
    """Contribution from one verified link to an aggregated edge."""
    link_id: int
    source: str
    contract_id: str
    implied_prob: float
    liquidity: float | None
    volume: float | None
    weight: float
    link_confidence: float


@dataclass(frozen=True)
class EdgeResult:
    """Per-outcome edge computation result."""
    match_id: str
    mapped_outcome: str
    model_prob: float
    market_prob: float
    raw_edge: float
    trust: float
    liquidity_factor: float
    adjusted_edge: float
    spread: float | None
    sources: list[EdgeSource]
    sources_count: int
    stale: bool
    captured_at: datetime
    review_priority: str = "normal"  # low | normal | high | critical
    factor_drivers: list | None = None
    factor_attribution: str | None = None


@dataclass(frozen=True)
class EdgeDetectionSummary:
    """Result of detect_edges(match_id) — all outcomes for one match."""
    match_id: str
    outcomes: list[EdgeResult]
    engine_name: str | None
    competition: str | None
    prediction_timestamp: datetime | None
    skipped: bool
    skip_reason: str | None


class EdgeDetectorService:
    """Computes model-vs-market edge for sports matches.

    Read-only on the kernel. Produces per-outcome edge snapshots persisted
    to kernel_sport_edges. Trust from KernelCalibration. Liquidity-weighted
    multi-source aggregation.
    """

    def __init__(self) -> None:
        self._link_store = SportMarketLinkStore()
        self._snap_store = MarketSnapshotStore()
        self._edge_store = EdgeStore()

    def detect_edges(self, match_id: str) -> EdgeDetectionSummary:
        """Compute and persist edge snapshots for all outcomes of a match.

        Steps:
        1. Fetch KernelPrediction. If None -> skipped (no_prediction).
        2. Fetch verified links. If empty -> skipped (no_verified_links).
        3. Fetch latest snapshot for each link.
        4. Group links by mapped_outcome.
        5. For each outcome in prediction.outcome_probabilities:
           a. Aggregate market_prob (liquidity-weighted average).
           b. raw_edge = model_prob - market_prob.
           c. trust = _compute_trust(engine, competition).
           d. liquidity_factor = _compute_liquidity_factor(links).
           e. adjusted_edge = raw_edge * trust * liquidity_factor.
           f. stale = _is_stale(prediction_ts, snapshot timestamps).
           g. Build EdgeResult, persist to kernel_sport_edges.
        6. Return EdgeDetectionSummary.
        """
        pred = get_latest_prediction(match_id)
        if pred is None:
            return EdgeDetectionSummary(
                match_id=match_id, outcomes=[],
                engine_name=None, competition=None,
                prediction_timestamp=None,
                skipped=True, skip_reason="no_prediction",
            )

        verified_links = self._link_store.get_verified_links(match_id=match_id)
        if not verified_links:
            return EdgeDetectionSummary(
                match_id=match_id, outcomes=[],
                engine_name=pred.engine, competition=pred.competition,
                prediction_timestamp=pred.created_at,
                skipped=True, skip_reason="no_verified_links",
            )

        # Fetch latest snapshot for each link
        links_with_snaps: list[tuple[dict, dict | None]] = []
        for link in verified_links:
            snap = self._snap_store.get_latest_snapshot(link_id=link["id"])
            links_with_snaps.append((link, snap))

        # Group by mapped_outcome
        by_outcome: dict[str, list[tuple[dict, dict | None]]] = {}
        for link, snap in links_with_snaps:
            outcome = link["mapped_outcome"]
            by_outcome.setdefault(outcome, []).append((link, snap))

        # Compute trust once per match (engine + competition are match-level).
        # Prefer confidence-bucket calibration when available (P1-V5).
        trust = self._compute_trust(
            pred.engine,
            pred.competition,
            confidence=getattr(pred, "confidence", None),
        )

        outcomes: list[EdgeResult] = []
        now = _utcnow()
        explanation = getattr(pred, "explanation", None) or []
        for outcome, model_prob in pred.outcome_probabilities.items():
            group = by_outcome.get(outcome)
            if not group:
                continue  # no market data for this outcome — skip

            market_prob, spread, sources = self._aggregate_market_prob(group)
            liquidity_factor = self._compute_liquidity_factor(group)
            raw_edge = model_prob - market_prob
            adjusted_edge = raw_edge * trust * liquidity_factor
            snap_timestamps = [
                snap["captured_at"] if snap else None
                for _, snap in group
            ]
            stale = self._is_stale(pred.created_at, snap_timestamps)

            drivers = extract_factor_drivers(explanation, outcome, top_n=3)
            attribution = format_factor_attribution(
                drivers, model_higher=(raw_edge > 0),
            )
            priority = self._review_priority(
                adjusted_edge=adjusted_edge,
                stale=stale,
                liquidity_factor=liquidity_factor,
                sources_count=len(sources),
                trust=trust,
            )

            edge_result = EdgeResult(
                match_id=match_id,
                mapped_outcome=outcome,
                model_prob=model_prob,
                market_prob=market_prob,
                raw_edge=raw_edge,
                trust=trust,
                liquidity_factor=liquidity_factor,
                adjusted_edge=adjusted_edge,
                spread=spread,
                sources=sources,
                sources_count=len(sources),
                stale=stale,
                captured_at=now,
                review_priority=priority,
                factor_drivers=drivers or None,
                factor_attribution=attribution,
            )
            outcomes.append(edge_result)

            # Persist to kernel_sport_edges
            self._edge_store.append_edge(
                match_id=match_id,
                mapped_outcome=outcome,
                model_prob=model_prob,
                market_prob=market_prob,
                raw_edge=raw_edge,
                trust=trust,
                liquidity_factor=liquidity_factor,
                adjusted_edge=adjusted_edge,
                spread=spread,
                sources_count=len(sources),
                stale=stale,
                captured_at=now,
            )

        return EdgeDetectionSummary(
            match_id=match_id,
            outcomes=outcomes,
            engine_name=pred.engine,
            competition=pred.competition,
            prediction_timestamp=pred.created_at,
            skipped=False,
            skip_reason=None,
        )

    def get_latest_edges(self, match_id: str) -> list[EdgeResult]:
        """Read the most recent edge snapshot per outcome for a match."""
        rows = self._edge_store.get_latest_edges(match_id)
        results = [self._row_to_edge_result(r) for r in rows]
        # Attach live factor drivers from current prediction (not persisted)
        pred = get_latest_prediction(match_id)
        explanation = getattr(pred, "explanation", None) if pred else None
        if explanation:
            enriched: list[EdgeResult] = []
            for er in results:
                drivers = extract_factor_drivers(
                    explanation, er.mapped_outcome, top_n=3,
                )
                attribution = format_factor_attribution(
                    drivers, model_higher=(er.raw_edge > 0),
                )
                # dataclass frozen — rebuild
                enriched.append(EdgeResult(
                    match_id=er.match_id,
                    mapped_outcome=er.mapped_outcome,
                    model_prob=er.model_prob,
                    market_prob=er.market_prob,
                    raw_edge=er.raw_edge,
                    trust=er.trust,
                    liquidity_factor=er.liquidity_factor,
                    adjusted_edge=er.adjusted_edge,
                    spread=er.spread,
                    sources=er.sources,
                    sources_count=er.sources_count,
                    stale=er.stale,
                    captured_at=er.captured_at,
                    review_priority=er.review_priority,
                    factor_drivers=drivers or None,
                    factor_attribution=attribution,
                ))
            return enriched
        return results

    def get_edge_history(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[EdgeResult]:
        """Read full edge time-series for a match, optionally filtered by outcome."""
        rows = self._edge_store.get_edge_history(match_id, mapped_outcome)
        return [self._row_to_edge_result(r) for r in rows]

    def get_top_discrepancies(
        self, limit: int = 20, min_abs_edge: float = 0.0
    ) -> list[EdgeResult]:
        """Read matches with the largest |adjusted_edge| across all matches."""
        rows = self._edge_store.get_top_discrepancies(limit=limit, min_abs_edge=min_abs_edge)
        return [self._row_to_edge_result(r) for r in rows]

    def _row_to_edge_result(self, row: dict) -> EdgeResult:
        """Convert a persisted edge row dict back to EdgeResult.

        Note: sources list is not persisted (it's per-link metadata); we
        return an empty list here since the persisted row only stores
        sources_count. This is acceptable for read endpoints that don't
        need per-link breakdown.
        """
        stale = bool(row["stale"])
        return EdgeResult(
            match_id=row["match_id"],
            mapped_outcome=row["mapped_outcome"],
            model_prob=row["model_prob"],
            market_prob=row["market_prob"],
            raw_edge=row["raw_edge"],
            trust=row["trust"],
            liquidity_factor=row["liquidity_factor"],
            adjusted_edge=row["adjusted_edge"],
            spread=row["spread"],
            sources=[],  # not persisted per-row
            sources_count=row["sources_count"],
            stale=stale,
            captured_at=row["captured_at"],
            review_priority=self._review_priority(
                adjusted_edge=float(row["adjusted_edge"]),
                stale=stale,
                liquidity_factor=float(row["liquidity_factor"] or 1.0),
                sources_count=int(row["sources_count"] or 0),
                trust=float(row["trust"] or 1.0),
            ),
        )

    def _aggregate_market_prob(
        self, links_with_snaps: list[tuple[dict, dict | None]]
    ) -> tuple[float, float | None, list[EdgeSource]]:
        """Returns (market_prob, spread, sources).

        market_prob = Σ(implied_prob × weight) / Σ(weight)
        where weight = max(liquidity, 1) for a venue that publishes depth, and
        the *median of the published weights* for one that does not.

        Why the median rather than 1.0, which is what this did before: a venue
        with no published depth has an **unknown** weight, not a weight of one
        dollar. Spending the sentinel as a measurement made it the most
        distrusted venue in the group by a factor of thousands, which is the
        opposite of the policy stated at every other liquidity site in this repo
        ("do not penalize what we cannot measure" — diagnosis_service, and
        market_quality_service excludes a missing sub-score from its average).
        Measured on the old rule, a sportsbook quoting 0.50 with no published
        depth beside a $100 market quoting 0.20 produced a consensus of 0.2030 —
        the book held 0.99% of it. Three such books all quoting 0.50 held 2.91%
        between them. That inflated raw_edge from 0.30 to 0.4470 (+49%), and the
        inflation is in the dangerous direction: it manufactures edge.

        The median says "assume this venue is typical of the venues that do
        publish" — the minimum-assumption reading, and the only one that neither
        penalizes nor favours the unmeasured venue. It preserves the real depth
        ordering among published venues, which an unweighted mean would discard.
        With a single published venue the median is that venue's weight, so the
        two-venue mixed case becomes an equal-weight average of two prices we
        have no basis to rank. Note this differs from _compute_liquidity_factor,
        which maps the same sentinel to "no penalty" — that function has a
        pre-existing documented all-unmeasured branch fixing the meaning there,
        and this one has no such prior commitment.

        Reduces exactly to the previous arithmetic when every venue publishes
        depth (no imputation) and when none does (no published weights to take a
        median of, so all weights stay 1.0 and this is an unweighted mean). Only
        the mixed case moves.

        spread is always None (known limitation: requires both YES and NO
        prices on separate links).
        """
        # First pass: read each link's price and its published depth, if any.
        rows: list[tuple[dict, float, float | None, float | None]] = []
        for link, snap in links_with_snaps:
            implied = snap["implied_prob"] if snap else link["implied_prob"]
            liquidity = snap["liquidity"] if snap else None
            volume = snap["volume"] if snap else None
            rows.append((link, implied, liquidity, volume))

        published = [
            max(float(liq), 1.0)
            for _, _, liq, _ in rows
            if liq is not None and liq > 0
        ]
        # No published depth anywhere -> every weight is 1.0 and the result is an
        # unweighted mean, which is what the previous code also produced.
        imputed_weight = statistics.median(published) if published else 1.0

        total_weight = 0.0
        weighted_sum = 0.0
        sources: list[EdgeSource] = []

        for link, implied, liquidity, volume in rows:
            if liquidity is not None and liquidity > 0:
                weight = max(float(liquidity), 1.0)
            else:
                weight = imputed_weight

            weighted_sum += implied * weight
            total_weight += weight

            sources.append(EdgeSource(
                link_id=link["id"],
                source=link["source"],
                contract_id=link["contract_id"],
                implied_prob=implied,
                liquidity=liquidity,
                volume=volume,
                weight=weight,
                link_confidence=link["link_confidence"],
            ))

        market_prob = weighted_sum / total_weight if total_weight > 0 else 0.0
        # Spread is None — known limitation (requires both YES and NO prices)
        spread = None
        return market_prob, spread, sources

    def _compute_trust(
        self,
        engine_name: str,
        competition: str,
        confidence: float | None = None,
    ) -> float:
        """Trust computation — Phase 8 adds calibration fusion.

        When PHASE8_CALIBRATION_FUSION_ENABLED is true, delegates to
        CalibrationFusionService which reads both Phase 3's
        KernelCalibration and Phase 7 D's KernelMarketCalibration to
        compute a sample-count-weighted composite trust. When false
        (default), falls back to Phase 7 Phase-3-only behavior —
        zero-invasion.

        ``confidence`` (optional) selects P1-V5 conditional bucket rows first.
        """
        if not config.settings.PHASE8_CALIBRATION_FUSION_ENABLED:
            return self._compute_trust_phase3(
                engine_name, competition, confidence=confidence,
            )

        from app.kernel.calibration_fusion_service import CalibrationFusionService
        fusion = CalibrationFusionService()
        composite = fusion.compute_trust(engine_name, competition)
        # Prefer conditional Phase-3 accuracy when bucket has enough samples
        if confidence is not None:
            cond = get_conditional_calibration_row(
                engine_name, competition, confidence,
            )
            min_n = config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
            if (
                cond is not None
                and getattr(cond, "competition", "") != competition
                and int(getattr(cond, "sample_count", 0) or 0) >= max(5, min_n // 2)
            ):
                return max(
                    config.settings.DIAGNOSIS_TRUST_FLOOR,
                    min(float(cond.avg_accuracy), 1.0),
                )
        return composite.trust

    def _compute_trust_phase3(
        self,
        engine_name: str,
        competition: str,
        confidence: float | None = None,
    ) -> float:
        """Phase 7 behavior — Phase 3 KernelCalibration only.

        - Prefer confidence-bucket row when present and sample-rich (P1-V5)
        - No calibration row (cold start) -> DIAGNOSIS_DORMANT_TRUST (0.5)
        - sample_count < CALIBRATION_FEEDBACK_MIN_SAMPLES (dormant) -> 0.5
        - Qualified -> clamp(avg_accuracy, DIAGNOSIS_TRUST_FLOOR, 1.0)
        """
        calibration = None
        if confidence is not None:
            calibration = get_conditional_calibration_row(
                engine_name, competition, confidence,
            )
        if calibration is None:
            calibration = get_calibration(engine_name, competition)
        if calibration is None:
            return config.settings.DIAGNOSIS_DORMANT_TRUST

        is_bucket = (
            isinstance(getattr(calibration, "competition", None), str)
            and "#c_" in str(calibration.competition)
        )
        min_samples = config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
        if is_bucket:
            min_samples = max(5, min_samples // 2)

        if calibration.sample_count < min_samples:
            return config.settings.DIAGNOSIS_DORMANT_TRUST

        trust = max(
            config.settings.DIAGNOSIS_TRUST_FLOOR,
            min(calibration.avg_accuracy, 1.0),
        )
        return trust

    def _compute_liquidity_factor(
        self, links_with_snaps: list[tuple[dict, dict | None]]
    ) -> float:
        """Liquidity factor from the max liquidity among all links.

        Uses the latest snapshot's liquidity. A link whose venue publishes no
        depth is **not penalized**, so the factor is 1.0 whenever any link is
        unmeasured — including a mix of measured and unmeasured links.
        Ramps against the edge detector's own _EDGE_LIQUIDITY_FLOOR (5000),
        decoupled from settings.DIAGNOSIS_LIQUIDITY_FLOOR so the diagnosis
        pipeline's floor cannot flatten edge scores. Uses max (most liquid
        source dominates).

        The mixed case is the fix. This function's two stated rules are "an
        unmeasured venue is not penalized" (the all-unmeasured branch returned
        1.0) and "the most liquid source dominates". Taking the max over only the
        *measured* subset honoured neither: it penalized a venue precisely
        because its depth could not be measured, and it let the group's fate be
        decided by a member that is not the max once the unpenalized member is
        counted. Measured on the old rule, one unmeasured venue alone gave 1.0
        while that same venue beside a $100 market gave 0.02 — learning that some
        *other* venue is thin cut the factor 50x. Nothing was learned about the
        first venue, and its absence and presence are not interchangeable, so
        this was not the "unknown row equals no row" case.

        The honest limit: a venue that publishes no depth has an *unknowable*
        factor, and arithmetic cannot supply one. 1.0 is a policy choice — the
        one this function already made for the all-unmeasured case — not a
        measurement. It moves adjusted_edge up in the mixed case.

        The rule itself lives in ``market_liquidity.group_liquidity_factor`` so
        this and the FeatureSet feed cannot drift; only the rendering of "do not
        penalize" differs, since this factor is multiplied and that one is a key
        the caller omits. The floor stays local on purpose — see
        _EDGE_LIQUIDITY_FLOOR above.
        """
        from app.kernel.market_liquidity import group_liquidity_factor

        factor = group_liquidity_factor(
            (snap.get("liquidity") if snap else None for _, snap in links_with_snaps),
            floor=_EDGE_LIQUIDITY_FLOOR,
        )
        # None means "unmeasured, do not penalize"; this factor is multiplied
        # into adjusted_edge, so that renders as 1.0.
        return 1.0 if factor is None else factor

    def _is_stale(
        self,
        prediction_ts: datetime | None,
        snapshot_timestamps: list[datetime | None],
    ) -> bool:
        """True if prediction is stale OR ALL market snapshots are stale.

        A fresh snapshot (captured_at within EDGE_STALE_HOURS) is enough
        to mark the edge as not stale — even if other snapshots are old.
        Uses the NEWEST snapshot (max timestamp).
        """
        threshold = config.settings.EDGE_STALE_HOURS  # 72.0 hours
        now = _utcnow()

        # Normalize tz-naive datetimes (from SQLite) to tz-aware UTC so
        # subtraction against `now` (tz-aware) does not raise.
        def _as_aware(ts: datetime) -> datetime:
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts

        if prediction_ts is not None:
            # Handle both tz-aware and tz-naive datetimes
            pred_age = (now - _as_aware(prediction_ts)).total_seconds() / 3600
            if pred_age > threshold:
                return True

        valid_snaps = [ts for ts in snapshot_timestamps if ts is not None]
        if not valid_snaps:
            return True  # no snapshots at all — definitely stale

        # Use the NEWEST snapshot (max timestamp). If the newest is still
        # old, then ALL snapshots are old -> stale. One fresh is enough.
        newest_snap = max(valid_snaps)
        snap_age = (now - _as_aware(newest_snap)).total_seconds() / 3600
        return snap_age > threshold

    def _review_priority(
        self,
        *,
        adjusted_edge: float,
        stale: bool,
        liquidity_factor: float,
        sources_count: int,
        trust: float,
    ) -> str:
        """Human-ops priority for dashboard queues (P1-O3 / review hygiene).

        critical — large edge + stale or thin/low-trust market (do not auto-act)
        high     — large edge, or medium edge with stale/thin data
        normal   — typical
        low      — small edge with healthy data
        """
        abs_e = abs(float(adjusted_edge))
        thin = liquidity_factor < 0.4 or sources_count < 1
        low_trust = trust < 0.55

        if abs_e >= 0.12 and (stale or thin or low_trust):
            return "critical"
        if abs_e >= 0.10 or (abs_e >= 0.06 and (stale or thin)):
            return "high"
        if abs_e < 0.03 and not stale and not thin:
            return "low"
        return "normal"
