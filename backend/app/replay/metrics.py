"""ReplayMetrics: accumulate pairwise (original, replayed) statistics.

5 metric classes (spec §4.5):
1. direction_matrix — YES->WAIT / YES->AVOID / WAIT->AVOID counts
2. brier — original vs replayed mean on resolved samples
3. direction_correct — resolved-sample direction accuracy
4. brier_by_quality — LLM vs deterministic_fallback split (spec §4.5)
5. phase_contributions + conflict_cases — per-phase marginal + conflicts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _BrierBucket:
    n: int = 0
    brier_sum: float = 0.0


@dataclass
class _PhaseContribution:
    downgrades_caused: int = 0
    directions_changed: int = 0
    conflicts_with_final: int = 0


_STRONG_DIRECTIONS = {"YES", "NO"}
_WEAK_DIRECTIONS = {"WAIT", "AVOID"}


class ReplayMetrics:
    """Accumulates pairwise (original, replayed) comparisons and per-phase
    marginal-contribution data. Pure: no IO, no LLM."""

    def __init__(self) -> None:
        self.total: int = 0
        self.direction_matrix: dict[tuple[str, str], int] = {}
        self.resolved_count: int = 0
        self.brier_original_sum: float = 0.0
        self.brier_replayed_sum: float = 0.0
        self.direction_correct_original: int = 0
        self.direction_correct_replayed: int = 0
        self.direction_correct_resolved_count: int = 0
        self.brier_by_quality: dict[str, _BrierBucket] = {}
        self.phase_contributions: dict[str, _PhaseContribution] = {}
        self.conflict_cases: list[dict[str, Any]] = []

    def add_pair(self, original: dict[str, Any], replayed: dict[str, Any]) -> None:
        """Accumulate one (original, replayed) record pair for direction
        matrix + brier + direction_correct + LLM/fallback split."""
        orig_dir = original.get("final_displayed_direction")
        replay_dir = replayed.get("final_displayed_direction")
        if orig_dir is not None and replay_dir is not None:
            self.total += 1
            key = (orig_dir, replay_dir)
            self.direction_matrix[key] = self.direction_matrix.get(key, 0) + 1

        # Brier + direction_correct on resolved samples. The record carries
        # brier_score + direction_correct after score_prediction ran; the
        # replayed record may have a different direction but the same
        # actual_outcome, so we re-derive direction_correct for the replay.
        orig_brier = original.get("brier_score")
        replay_brier = replayed.get("brier_score")
        actual = original.get("actual_outcome")
        if actual is not None and orig_brier is not None and replay_brier is not None:
            self.resolved_count += 1
            self.brier_original_sum += orig_brier
            self.brier_replayed_sum += replay_brier

            orig_dc = original.get("direction_correct")
            if orig_dc is not None:
                self.direction_correct_resolved_count += 1
                if orig_dc:
                    self.direction_correct_original += 1
            # Re-derive direction_correct for the replayed direction.
            replay_dc = _derive_direction_correct(replay_dir, actual)
            if replay_dc is not None:
                if replay_dc:
                    self.direction_correct_replayed += 1

            # LLM vs fallback split (spec §4.5).
            quality = _analysis_quality_of(replayed)
            if quality is not None:
                bucket = self.brier_by_quality.setdefault(
                    quality, _BrierBucket()
                )
                bucket.n += 1
                bucket.brier_sum += replay_brier

    def add_phase_result(
        self,
        event_id: str,
        phase: str,
        base_dir: str | None,
        phase_dir: str | None,
        final_dir: str | None,
    ) -> None:
        """Accumulate per-phase marginal contribution + conflict detection.

        Called N times per event (once per phase) during the N+1 replay
        loop. ``base_dir`` is the all-off baseline; ``phase_dir`` is the
        direction when only this phase is on; ``final_dir`` is the all-on
        direction.
        """
        if phase not in self.phase_contributions:
            self.phase_contributions[phase] = _PhaseContribution()
        pc = self.phase_contributions[phase]

        # downgrades_caused: phase turned a strong dir into a weak one.
        if base_dir in _STRONG_DIRECTIONS and phase_dir in _WEAK_DIRECTIONS:
            pc.downgrades_caused += 1
        # directions_changed: phase produced any direction different from base.
        if base_dir is not None and phase_dir is not None and base_dir != phase_dir:
            pc.directions_changed += 1
        # conflicts_with_final: phase disagrees with the final merged direction.
        if (
            phase_dir is not None
            and final_dir is not None
            and phase_dir != final_dir
            and phase_dir in _STRONG_DIRECTIONS
            and final_dir in _WEAK_DIRECTIONS
        ):
            pc.conflicts_with_final += 1
            self.conflict_cases.append({
                "event_id": event_id,
                "phase": phase,
                "phase_dir": phase_dir,
                "final_dir": final_dir,
                "base_dir": base_dir,
            })

    def brier_delta(self) -> float:
        """Returns replayed_mean - original_mean. Negative = improved."""
        if self.resolved_count == 0:
            return 0.0
        orig_mean = self.brier_original_sum / self.resolved_count
        replay_mean = self.brier_replayed_sum / self.resolved_count
        return replay_mean - orig_mean

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for report rendering."""
        return {
            "total": self.total,
            "direction_matrix": {
                f"{k[0]}->{k[1]}": v for k, v in self.direction_matrix.items()
            },
            "resolved_count": self.resolved_count,
            "brier_original_mean": (
                self.brier_original_sum / self.resolved_count
                if self.resolved_count
                else None
            ),
            "brier_replayed_mean": (
                self.brier_replayed_sum / self.resolved_count
                if self.resolved_count
                else None
            ),
            "brier_delta": self.brier_delta(),
            "direction_correct_original": self.direction_correct_original,
            "direction_correct_replayed": self.direction_correct_replayed,
            "direction_correct_resolved_count": self.direction_correct_resolved_count,
            "brier_by_quality": {
                k: {"n": v.n, "brier_mean": (v.brier_sum / v.n if v.n else None)}
                for k, v in self.brier_by_quality.items()
            },
            "phase_contributions": {
                k: {
                    "downgrades_caused": v.downgrades_caused,
                    "directions_changed": v.directions_changed,
                    "conflicts_with_final": v.conflicts_with_final,
                }
                for k, v in self.phase_contributions.items()
            },
            "conflict_cases": self.conflict_cases[:20],  # cap for report
            "conflict_cases_total": len(self.conflict_cases),
        }


def _derive_direction_correct(direction: str | None, actual_outcome: float) -> bool | None:
    """Mirror prediction_store.compute_direction_correct: YES if outcome>=50,
    NO if outcome<50, None for WAIT/AVOID/missing."""
    if direction is None or actual_outcome is None:
        return None
    if direction == "YES":
        return actual_outcome >= 50
    if direction == "NO":
        return actual_outcome < 50
    return None  # WAIT / AVOID


def _analysis_quality_of(record: dict[str, Any]) -> str | None:
    """Extract the analysis_quality label for LLM/fallback split."""
    tel = record.get("llm_telemetry")
    if isinstance(tel, dict):
        q = tel.get("analysis_quality")
        if isinstance(q, str) and q:
            return q
    legacy = record.get("legacy_analysis")
    if isinstance(legacy, dict):
        q = legacy.get("analysis_quality")
        if isinstance(q, str) and q:
            return q
    return None
