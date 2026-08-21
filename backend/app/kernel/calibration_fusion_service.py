"""Calibration Fusion Service — combines Phase 3 and Phase 7 D calibration signals.

Reads both calibration tables:
- KernelCalibration (Phase 3 match-outcome calibration): avg_accuracy
- KernelMarketCalibration (Phase 7 D market-settlement calibration): direction_accuracy

Computes a composite trust weighted by the sample counts of the sources that are
**qualified** (at or above their MIN). A source below its MIN reports the dormant
sentinel, which carries no estimate and therefore no weight. When
PHASE8_CALIBRATION_FUSION_ENABLED is false, EdgeDetectorService._compute_trust
bypasses this service entirely (zero-invasion).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core import config
from app.kernel.kernel_db import get_calibration
from app.kernel.market_settlement_store import MarketSettlementStore


@dataclass(frozen=True)
class CompositeTrust:
    """Result of fusing Phase 3 and market calibration signals."""
    trust: float                     # fused trust value (the one B uses)
    phase3_trust: float              # Phase 3 calibration trust
    market_trust: float              # D market calibration trust
    phase3_weight: float             # w1 (0.0 to 1.0)
    market_weight: float             # w2 (0.0 to 1.0)
    phase3_sample_count: int
    market_sample_count: int
    source: str                      # "dormant" / "phase3_only" / "market_only" / "fusion"


def _clamp_trust(value: float) -> float:
    """Clamp trust to [DIAGNOSIS_TRUST_FLOOR, 1.0]."""
    return max(
        config.settings.DIAGNOSIS_TRUST_FLOOR,
        min(value, 1.0),
    )


def _source_trust(
    value: float, sample_count: int, min_samples: int
) -> tuple[float, bool]:
    """One source's trust, plus whether that source is *qualified*.

    Below ``min_samples`` the trust is DIAGNOSIS_DORMANT_TRUST and the flag is
    False. The flag is the point: the dormant value is a **sentinel meaning "no
    usable estimate"**, not a measurement of 0.5, so a caller fusing sources
    must give it zero weight rather than weighting it by samples that carry no
    estimate. One rule for both sources, so the qualification threshold and the
    trust value cannot drift apart.
    """
    if sample_count < min_samples:
        return config.settings.DIAGNOSIS_DORMANT_TRUST, False
    return _clamp_trust(value), True


def _compute_phase3_trust(avg_accuracy: float, sample_count: int) -> float:
    """Phase 3 trust: dormant if below MIN, else clamped avg_accuracy.

    Kept as the single-value form for callers that only need the number —
    ``tests/test_learning_calibration.py`` uses it as the reference for whether
    trust separates two engines, and ``learning_service`` names it in its
    docstring. ``compute_trust`` calls ``_source_trust`` directly instead,
    because fusion also needs the qualified flag.
    """
    return _source_trust(
        avg_accuracy, sample_count, config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
    )[0]


def _compute_market_trust(direction_accuracy: float, sample_count: int) -> float:
    """Market trust: dormant if below MIN, else clamped direction_accuracy."""
    return _source_trust(
        direction_accuracy,
        sample_count,
        config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION,
    )[0]


class CalibrationFusionService:
    """Fuses Phase 3 and Phase 7 D calibration signals into a composite trust."""

    def __init__(self) -> None:
        self._settlement_store = MarketSettlementStore()

    def compute_trust(self, engine: str, competition: str) -> CompositeTrust:
        """Compute composite trust by sample-count-weighted fusion.

        Rules:
        1. Both tables have no data → DIAGNOSIS_DORMANT_TRUST (0.5), source="dormant"
        2. Only Phase 3 has data → phase3_trust, source="phase3_only"
        3. Only market has data → market_trust, source="market_only"
        4. Both have data → fusion weighted by the sample counts of the
           **qualified** sources only:
           w1 = phase3_count / total, w2 = market_count / total, where a source
           below its MIN contributes 0 to `total` and 0 weight.
           composite = w1 * phase3_trust + w2 * market_trust
           If exactly one source qualifies, `source` reports that source rather
           than "fusion" — only one channel informed the number.
           If neither qualifies → dormant.
        """
        # Read Phase 3 calibration
        phase3_cal = get_calibration(engine, competition)
        phase3_has_data = phase3_cal is not None

        # Read market calibration (D)
        market_cals = self._settlement_store.get_calibrations(
            engine=engine, competition=competition
        )
        market_cal = market_cals[0] if market_cals else None
        market_has_data = market_cal is not None

        dormant = config.settings.DIAGNOSIS_DORMANT_TRUST

        # Case 1: both empty
        if not phase3_has_data and not market_has_data:
            return CompositeTrust(
                trust=dormant,
                phase3_trust=dormant,
                market_trust=dormant,
                phase3_weight=0.0,
                market_weight=0.0,
                phase3_sample_count=0,
                market_sample_count=0,
                source="dormant",
            )

        # Compute per-source trust values. Tested against the row rather than the
        # `*_has_data` flag: a bool assigned from `is not None` reads the same but
        # does not narrow the Optional for the attribute access below.
        if phase3_cal is not None:
            phase3_trust, phase3_qualified = _source_trust(
                phase3_cal.avg_accuracy,
                phase3_cal.sample_count,
                config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES,
            )
            phase3_count = phase3_cal.sample_count
        else:
            phase3_trust = dormant
            phase3_qualified = False
            phase3_count = 0

        if market_cal is not None:
            market_trust, market_qualified = _source_trust(
                market_cal["direction_accuracy"],
                market_cal["sample_count"],
                config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION,
            )
            market_count = market_cal["sample_count"]
        else:
            market_trust = dormant
            market_qualified = False
            market_count = 0

        # Case 2: only Phase 3
        if phase3_has_data and not market_has_data:
            return CompositeTrust(
                trust=phase3_trust,
                phase3_trust=phase3_trust,
                market_trust=dormant,
                phase3_weight=1.0,
                market_weight=0.0,
                phase3_sample_count=phase3_count,
                market_sample_count=0,
                source="phase3_only",
            )

        # Case 3: only market
        if market_has_data and not phase3_has_data:
            return CompositeTrust(
                trust=market_trust,
                phase3_trust=dormant,
                market_trust=market_trust,
                phase3_weight=0.0,
                market_weight=1.0,
                phase3_sample_count=0,
                market_sample_count=market_count,
                source="market_only",
            )

        # Case 4: fusion (both have rows)
        #
        # Weights count only *qualified* sources. A dormant source's trust is
        # the sentinel 0.5 meaning "no usable estimate"; weighting that sentinel
        # by sample_count treats it as an estimate backed by samples that hold
        # no estimate at all. Measured against the previous arithmetic, with
        # Phase 3 at accuracy 0.72 over 20 samples and a market channel whose
        # real direction accuracy is 0.95:
        #   market n= 0 (no row)      composite 0.7200
        #   market n= 1 (dormant)     composite 0.7095
        #   market n= 9 (dormant)     composite 0.6517   <- more evidence, less trust
        #   market n=10 (qualified)   composite 0.7967   <- 0.145 jump at the threshold
        # so accumulating evidence about a *good* channel lowered composite
        # trust right up to the threshold. That cannot be read as shrinkage
        # toward a prior: under shrinkage more data means *less* pull to the
        # prior, here it meant more. The same arithmetic flattered a bad engine
        # upward — 0.20 over 20 samples became 0.2931 next to a 9-sample dormant
        # row. And a row that says "I don't know" moved the answer at all, while
        # the *absence* of that row (case 3) does not.
        w_phase3 = phase3_count if phase3_qualified else 0
        w_market = market_count if market_qualified else 0
        total = w_phase3 + w_market
        if total == 0:
            # Neither source qualifies (no samples, or both below their MIN) —
            # there is no usable signal to fuse, so report dormant rather than
            # an average of two sentinels.
            return CompositeTrust(
                trust=dormant,
                phase3_trust=phase3_trust,
                market_trust=market_trust,
                phase3_weight=0.0,
                market_weight=0.0,
                phase3_sample_count=phase3_count,
                market_sample_count=market_count,
                source="dormant",
            )

        w1 = w_phase3 / total
        w2 = w_market / total
        composite = w1 * phase3_trust + w2 * market_trust

        # Name what actually informed the number. Reporting "fusion" when only
        # one channel qualified would claim corroboration that never happened.
        if not market_qualified:
            source = "phase3_only"
        elif not phase3_qualified:
            source = "market_only"
        else:
            source = "fusion"

        return CompositeTrust(
            trust=composite,
            phase3_trust=phase3_trust,
            market_trust=market_trust,
            phase3_weight=w1,
            market_weight=w2,
            phase3_sample_count=phase3_count,
            market_sample_count=market_count,
            source=source,
        )
