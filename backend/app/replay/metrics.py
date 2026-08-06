"""ReplayMetrics: accumulate pairwise (original, replayed) statistics.

5 metric classes (spec §4.5):
1. direction_matrix — YES->WAIT / YES->AVOID / WAIT->AVOID counts
2. brier_mean — frozen Brier on resolved samples (overlays don't recompute
   ai_probability, so original == replayed; reported as a calibration
   reference, not an improvement signal)
3. direction_correct + direction_correct_delta — resolved-sample direction
   accuracy + delta (the real improvement signal: does the overlay's
   direction change match the settled outcome)
4. brier_by_quality — LLM vs deterministic_fallback split (spec §4.5)
5. phase_contributions + conflict_cases — per-phase marginal + conflicts
"""
from __future__ import annotations

from dataclasses import dataclass
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
        # Brier is frozen at freeze time (overlays don't recompute
        # ai_probability), so original and replayed carry the same
        # brier_score. We keep a single sum (not original/replayed) because
        # the delta is always 0 and reporting it as "improvement" is
        # misleading. The real quality signal is direction_correct_delta
        # (does the overlay's direction change match the actual outcome).
        self.brier_sum: float = 0.0
        self.direction_correct_original: int = 0
        self.direction_correct_replayed: int = 0
        self.direction_correct_resolved_count: int = 0
        self.brier_by_quality: dict[str, _BrierBucket] = {}
        self.phase_contributions: dict[str, _PhaseContribution] = {}
        self.conflict_cases: list[dict[str, Any]] = []

    def add_pair(self, original: dict[str, Any], replayed: dict[str, Any]) -> None:
        """Accumulate one (original, replayed) record pair for direction
        matrix + brier + direction_correct + LLM/fallback split.

        Direction lookup uses ``_effective_direction``: when
        ``final_displayed_direction`` is absent (e.g. all overlays off in
        the baseline config), falls back to
        ``actionable_recommendation.direction`` — the raw pre-overlay
        direction. Without this fallback, the default ``all_off -> current``
        comparison would report total=0 because the all_off baseline strips
        ``final_displayed_direction`` and never rebuilds it.

        Brier note: ``brier_score`` is frozen at freeze time; replay does
        not recompute ``ai_probability``, so original and replayed share the
        same Brier. The improvement signal is ``direction_correct_delta``,
        which re-derives correctness for BOTH sides from the current replay
        direction vs ``actual_outcome`` (NOT the frozen ``direction_correct``
        field, which reflects the freeze-time snapshot direction and would
        pair incorrectly with the A/B replay's left-side direction).
        WAIT/AVOID abstentions are excluded from delta; their signal flows
        through ``direction_matrix`` (e.g. YES->WAIT counts).
        """
        orig_dir = _effective_direction(original)
        replay_dir = _effective_direction(replayed)
        if orig_dir is not None and replay_dir is not None:
            self.total += 1
            key = (orig_dir, replay_dir)
            self.direction_matrix[key] = self.direction_matrix.get(key, 0) + 1

        # Brier on resolved samples. brier_score is frozen at freeze time
        # (overlays don't recompute ai_probability), so original and replayed
        # share the same value — we keep a single sum as a calibration
        # reference, not an improvement signal.
        orig_brier = original.get("brier_score")
        actual = original.get("actual_outcome")
        if actual is not None and orig_brier is not None:
            self.resolved_count += 1
            self.brier_sum += orig_brier

            # direction_correct: re-derive for BOTH sides from the current
            # replay direction vs the settled outcome. The frozen
            # ``direction_correct`` field on the record reflects the
            # freeze-time snapshot direction, NOT the A/B replay's left-side
            # config direction — using it directly caused asymmetry: e.g.
            # orig_dir=YES (all_off fallback to actionable_recommendation)
            # but orig_dc read the frozen WAIT snapshot's direction_correct,
            # pairing a YES direction with a WAIT-derived correctness flag.
            # Symmetric re-derivation fixes the mismatch. WAIT/AVOID return
            # None (abstention) and are excluded from delta — direction_correct
            # measures "explicit YES/NO prediction accuracy"; abstention
            # signals flow through direction_matrix (YES->WAIT etc.).
            orig_dc = _derive_direction_correct(orig_dir, actual)
            replay_dc = _derive_direction_correct(replay_dir, actual)
            if orig_dc is not None and replay_dc is not None:
                self.direction_correct_resolved_count += 1
                if orig_dc:
                    self.direction_correct_original += 1
                if replay_dc:
                    self.direction_correct_replayed += 1

            # LLM vs fallback split (spec §4.5). Uses the frozen brier_score
            # bucketed by the replayed record's analysis_quality — this is
            # a calibration reference, not an improvement signal.
            quality = _analysis_quality_of(replayed)
            if quality is not None:
                bucket = self.brier_by_quality.setdefault(
                    quality, _BrierBucket()
                )
                bucket.n += 1
                bucket.brier_sum += orig_brier

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

    def direction_correct_delta(self) -> float | None:
        """Returns replayed_correct_rate - original_correct_rate.

        Positive = overlays improved direction accuracy; negative = overlays
        hurt. None when no samples with both sides having explicit YES/NO
        directions (WAIT/AVOID abstentions are excluded — their signal
        flows through direction_matrix, e.g. YES->WAIT counts).

        Both sides' correctness is re-derived from the current replay
        direction vs ``actual_outcome`` (NOT the frozen ``direction_correct``
        field, which reflects the freeze-time snapshot direction and would
        pair incorrectly with the A/B replay's left-side direction).
        """
        if self.direction_correct_resolved_count == 0:
            return None
        n = self.direction_correct_resolved_count
        orig_rate = self.direction_correct_original / n
        replay_rate = self.direction_correct_replayed / n
        return replay_rate - orig_rate

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for report rendering."""
        return {
            "total": self.total,
            "direction_matrix": {
                f"{k[0]}->{k[1]}": v for k, v in self.direction_matrix.items()
            },
            "resolved_count": self.resolved_count,
            # Single frozen Brier mean (original == replayed in production
            # because brier_score is frozen at freeze time). Replaces the
            # old brier_original_mean / brier_replayed_mean / brier_delta
            # triplet which always reported delta=0 and was misleading.
            "brier_mean": (
                self.brier_sum / self.resolved_count
                if self.resolved_count
                else None
            ),
            "brier_frozen": True,  # explicit flag for report renderer
            "direction_correct_original": self.direction_correct_original,
            "direction_correct_replayed": self.direction_correct_replayed,
            "direction_correct_resolved_count": self.direction_correct_resolved_count,
            "direction_correct_delta": self.direction_correct_delta(),
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


def _effective_direction(record: dict[str, Any]) -> str | None:
    """Return the direction used for metrics comparison.

    Prefers ``final_displayed_direction`` (the post-overlay merged direction).
    Falls back to ``actionable_recommendation.direction`` (the raw pre-overlay
    direction) when ``final_displayed_direction`` is absent — this happens
    when all overlays are off (preset_all_off baseline), because
    ``_build_all_overlays`` only sets ``final_displayed_direction`` when at
    least one overlay runs. Without this fallback the all_off baseline would
    contribute None and the default A/B comparison would report total=0.
    """
    fd = record.get("final_displayed_direction")
    if fd is not None:
        return fd
    rec = record.get("actionable_recommendation")
    if isinstance(rec, dict):
        d = rec.get("direction")
        if d in ("YES", "NO", "WAIT", "AVOID"):
            return d
    return None


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
