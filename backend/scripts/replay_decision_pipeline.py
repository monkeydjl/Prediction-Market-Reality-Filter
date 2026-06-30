"""Replay Phase 1-5 overlays on frozen event records to quantify
direction-change impact, Brier delta, and per-phase contributions.

Converges spec §4.5 (replay harness), §1.5 (A/B compare), and §4.2
(degraded-mode tests) into one tool.

Usage:
    # Default: all events, all_off -> current (marginal contribution)
    python -m scripts.replay_decision_pipeline

    # Specific events
    python -m scripts.replay_decision_pipeline --event-ids id1 id2

    # Sample N events
    python -m scripts.replay_decision_pipeline --sample-size 500

    # A/B compare two configs
    python -m scripts.replay_decision_pipeline --compare current all_off

    # Custom output dir
    python -m scripts.replay_decision_pipeline --output-dir docs/reports/replay/

    # Skip per-phase marginal (faster, no N+1 loop)
    python -m scripts.replay_decision_pipeline --skip-marginal
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.replay.config import ReplayConfig  # noqa: E402
from app.replay.runner import replay_record, simulate_llm_degraded  # noqa: E402
from app.replay.metrics import ReplayMetrics  # noqa: E402
from app.replay.report import write_report  # noqa: E402

logger = logging.getLogger(__name__)


# The 6 phase names used for marginal-contribution attribution. Must match
# the ReplayConfig field prefixes (without the "_enabled" suffix).
_PHASE_FIELDS = [
    ("decision_quality", "decision_quality_enabled"),
    ("market_quality", "market_quality_enabled"),
    ("source_reliability", "source_reliability_enabled"),
    ("prediction_calibration", "prediction_calibration_enabled"),
    ("llm_telemetry", "llm_telemetry_enabled"),
    ("guardrails", "guardrails_enabled"),
]


def _load_records(event_ids: list[str] | None, sample_size: int | None) -> list[dict[str, Any]]:
    """Load event records from event_store. Unwraps the {event_id, record}
    envelope that event_store.list_all_events returns."""
    from app.memory.event_store import list_all_events
    entries = list_all_events()
    records = [e["record"] for e in entries if isinstance(e.get("record"), dict)]
    if event_ids:
        wanted = set(event_ids)
        records = [r for r in records if r.get("event_id") in wanted]
    if sample_size and len(records) > sample_size:
        random.seed(42)  # deterministic sampling for reproducibility
        records = random.sample(records, sample_size)
    return records


def _enrich_with_outcome(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort: attach brier_score + actual_outcome + direction_correct
    from prediction_store so metrics can compute Brier on resolved samples.
    Records without a prediction row stay unchanged."""
    try:
        from app.memory.prediction_store import list_recent
        # list_recent does not support status filter; fetch all and filter
        # client-side to resolved predictions only. prediction_store writes
        # status="scored" (act) or "observed" (watch/skip) at resolve time —
        # NOT "resolved" (see prediction_store.score_prediction). Filtering
        # for "resolved" would silently drop every resolved prediction,
        # leaving Brier / direction-accuracy metrics permanently empty.
        all_preds = list_recent(limit=10000)
        preds = {
            p["event_id"]: p
            for p in all_preds
            if p.get("status") in ("scored", "observed")
        }
    except Exception as exc:
        logger.debug("prediction_store unavailable, skipping outcome enrichment: %s", exc)
        return records
    for r in records:
        p = preds.get(r.get("event_id"))
        if p:
            r.setdefault("brier_score", p.get("brier_score"))
            r.setdefault("actual_outcome", p.get("actual_outcome"))
            r.setdefault("direction_correct", p.get("direction_correct"))
    return records


def _run_marginal_loop(records: list[dict[str, Any]], metrics: ReplayMetrics) -> None:
    """N+1 replay loop: baseline (all_off) + one-per-phase (only P on) +
    final (all_on). Feeds metrics.add_phase_result per event per phase.

    Uses ``_effective_direction`` for base/phase/final directions so the
    all_off baseline (which never sets ``final_displayed_direction``)
    contributes its raw ``actionable_recommendation.direction`` instead of
    None. Without this, every base_dir would be None and
    ``directions_changed`` / ``downgrades_caused`` would stay 0.
    """
    from app.replay.metrics import _effective_direction
    # 1. Baseline: all_off (raw pre-overlay direction)
    base_results = {r["event_id"]: replay_record(r, ReplayConfig.preset_all_off()) for r in records}
    # 2. Final: all_on (use current settings)
    final_results = {r["event_id"]: replay_record(r, ReplayConfig.preset_all_on()) for r in records}
    # 3. Per-phase: only P on
    for phase_name, field_name in _PHASE_FIELDS:
        phase_cfg = ReplayConfig.preset_all_off()
        setattr(phase_cfg, field_name, True)
        for r in records:
            eid = r["event_id"]
            phase_replayed = replay_record(r, phase_cfg)
            metrics.add_phase_result(
                event_id=eid,
                phase=phase_name,
                base_dir=_effective_direction(base_results[eid]),
                phase_dir=_effective_direction(phase_replayed),
                final_dir=_effective_direction(final_results[eid]),
            )


def run_replay(
    records: list[dict[str, Any]],
    *,
    compare: tuple[str, str] | None = None,
    skip_marginal: bool = False,
    output_dir: Path,
) -> Path:
    """Run the replay loop and write the report. Returns the report.md path."""
    records = _enrich_with_outcome(records)

    # Determine the two configs to compare. Default: all_off -> current.
    # Orientation matters: ``add_pair(original=A, replayed=B)`` populates
    # direction_matrix as ``A_dir -> B_dir``. We want "raw -> with overlays"
    # so ``YES->WAIT`` reads as "overlays downgraded YES to WAIT" (the
    # downgrade we want to measure), not the reverse.
    if compare is None:
        compare = ("all_off", "current")
    cfg_a = _config_by_name(compare[0])
    cfg_b = _config_by_name(compare[1])

    metrics = ReplayMetrics()
    cases: list[dict[str, Any]] = []
    for r in records:
        replayed_a = replay_record(r, cfg_a)
        # preset_llm_degraded only enables the telemetry/guardrail flags;
        # it does NOT flip degraded_mode. simulate_llm_degraded is the
        # post-step that forces degraded_mode=True and re-runs the guardrail
        # so llm_degraded_blocks_act actually fires. Without this call,
        # --compare current llm_degraded would just rebuild the same
        # overlays as current and never trigger the degradation path.
        # Pass cfg so simulate_llm_degraded can re-apply the config's
        # guardrail flags (replay_record's apply_replay_config has exited
        # by now, so settings are restored to defaults where
        # GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT=False).
        if compare[0] == "llm_degraded":
            simulate_llm_degraded(replayed_a, cfg=cfg_a)
        replayed_b = replay_record(r, cfg_b)
        if compare[1] == "llm_degraded":
            simulate_llm_degraded(replayed_b, cfg=cfg_b)
        # Compare B (replayed under alt config) against A (baseline).
        metrics.add_pair(original=replayed_a, replayed=replayed_b)
        cases.append({
            "event_id": r.get("event_id"),
            "direction_a": replayed_a.get("final_displayed_direction"),
            "direction_b": replayed_b.get("final_displayed_direction"),
        })

    if not skip_marginal:
        _run_marginal_loop(records, metrics)

    return write_report(metrics.to_dict(), output_dir, cases=cases)


def _config_by_name(name: str) -> ReplayConfig:
    if name == "current":
        return ReplayConfig.preset_all_on()
    if name == "all_off":
        return ReplayConfig.preset_all_off()
    if name == "llm_degraded":
        return ReplayConfig.preset_llm_degraded()
    raise ValueError(f"Unknown config name: {name!r} (use current/all_off/llm_degraded)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-ids", nargs="*", default=None, help="Specific event IDs to replay.")
    parser.add_argument("--sample-size", type=int, default=None, help="Random sample N events.")
    parser.add_argument("--compare", nargs=2, default=None, metavar=("CONFIG_A", "CONFIG_B"),
                        help="Two config names (current/all_off/llm_degraded). Default: all_off current")
    parser.add_argument("--skip-marginal", action="store_true", help="Skip the N+1 per-phase loop.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.output_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        args.output_dir = Path("docs/reports/replay") / ts

    records = _load_records(args.event_ids, args.sample_size)
    if not records:
        logger.warning("No records to replay.")
        return 1

    logger.info("Replaying %d records...", len(records))
    report_path = run_replay(
        records,
        compare=tuple(args.compare) if args.compare else None,
        skip_marginal=args.skip_marginal,
        output_dir=args.output_dir,
    )
    logger.info("Report written to %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
