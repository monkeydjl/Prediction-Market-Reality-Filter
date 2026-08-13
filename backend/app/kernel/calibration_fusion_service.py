"""Calibration Fusion Service — combines Phase 3 and Phase 7 D calibration signals.

Reads both calibration tables:
- KernelCalibration (Phase 3 match-outcome calibration): avg_accuracy
- KernelMarketCalibration (Phase 7 D market-settlement calibration): direction_accuracy

Computes a sample-count-weighted composite trust. When PHASE8_CALIBRATION_FUSION_ENABLED
is false, EdgeDetectorService._compute_trust bypasses this service entirely (zero-invasion).
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


def _compute_phase3_trust(avg_accuracy: float, sample_count: int) -> float:
    """Phase 3 trust: dormant if below MIN, else clamped avg_accuracy."""
    if sample_count < config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES:
        return config.settings.DIAGNOSIS_DORMANT_TRUST
    return _clamp_trust(avg_accuracy)


def _compute_market_trust(direction_accuracy: float, sample_count: int) -> float:
    """Market trust: dormant if below MIN, else clamped direction_accuracy."""
    if sample_count < config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION:
        return config.settings.DIAGNOSIS_DORMANT_TRUST
    return _clamp_trust(direction_accuracy)


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
        4. Both have data → weighted fusion, source="fusion"
           w1 = phase3_count / (phase3_count + market_count)
           w2 = market_count / (phase3_count + market_count)
           composite = w1 * phase3_trust + w2 * market_trust
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
            phase3_trust = _compute_phase3_trust(
                phase3_cal.avg_accuracy, phase3_cal.sample_count
            )
            phase3_count = phase3_cal.sample_count
        else:
            phase3_trust = dormant
            phase3_count = 0

        if market_cal is not None:
            market_trust = _compute_market_trust(
                market_cal["direction_accuracy"], market_cal["sample_count"]
            )
            market_count = market_cal["sample_count"]
        else:
            market_trust = dormant
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

        # Case 4: fusion (both have data)
        total = phase3_count + market_count
        if total == 0:
            # Both have rows but zero sample_count — treat as dormant
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

        w1 = phase3_count / total
        w2 = market_count / total
        composite = w1 * phase3_trust + w2 * market_trust

        return CompositeTrust(
            trust=composite,
            phase3_trust=phase3_trust,
            market_trust=market_trust,
            phase3_weight=w1,
            market_weight=w2,
            phase3_sample_count=phase3_count,
            market_sample_count=market_count,
            source="fusion",
        )
