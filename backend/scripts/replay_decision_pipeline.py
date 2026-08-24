"""Replay Phase 1-5 overlays on frozen event records to quantify
direction-change impact, Brier delta, and per-phase contributions.

Converges spec §4.5 (replay harness), §1.5 (A/B compare), and §4.2
(degraded-mode tests) into one tool.

Usage:
    # Default: all events, all_off -> current (marginal contribution)
    python -m scripts.replay_decision_pipeline

    # Specific events
    python -m scripts.replay_decision_pipeline --event-ids id1 id2

    # Sample N events (stable: same seed + same ids = same subset)
    python -m scripts.replay_decision_pipeline --sample-size 500
    python -m scripts.replay_decision_pipeline --sample-size 500 --sample-seed 2026-w34

    # A/B compare two configs
    python -m scripts.replay_decision_pipeline --compare current all_off

    # Custom output dir
    python -m scripts.replay_decision_pipeline --output-dir docs/reports/replay/

    # Skip per-phase marginal (faster, no N+1 loop)
    python -m scripts.replay_decision_pipeline --skip-marginal

Exit codes: 0 report written, 1 no records to replay, 2 bad arguments.

Every report carries a ``run`` block naming the compare pair, the population,
the sample (size/seed/strategy), and what was requested but not found, so two
archived reports can be told apart. See ``docs/ops/RUNBOOK.md``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.replay.config import ReplayConfig  # noqa: E402
from app.replay.runner import replay_record, simulate_llm_degraded  # noqa: E402
from app.replay.metrics import ReplayMetrics  # noqa: E402
from app.replay.report import (  # noqa: E402
    REPLAY_REPORT_SCHEMA_VERSION,
    write_report,
)
from app.utils.stable_sample import SELECTION_STRATEGY, stable_sample  # noqa: E402

logger = logging.getLogger(__name__)

# Seed for --sample-size. Named rather than the bare 42 the old positional
# sampler used, because it now goes into the report and an operator comparing
# two reports has to be able to see that the two agree on it.
_DEFAULT_SAMPLE_SEED = "replay"

_CONFIG_NAMES = ("current", "all_off", "llm_degraded")


@dataclass
class LoadNotes:
    """What ``_load_records`` would otherwise have absorbed in silence.

    Every field here is rendered into the report's Run block. A replay over 8
    of 10 requested events reads exactly like a replay over 10 unless the two
    missing ones are named, and a duplicated event_id inflates every count in
    the report by one without changing anything an operator can see.
    """

    population: int = 0
    missing_event_ids: list[str] = field(default_factory=list)
    duplicate_event_ids: list[str] = field(default_factory=list)
    skipped_no_event_id: int = 0


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


def _load_records(
    event_ids: list[str] | None,
    sample_size: int | None,
    *,
    sample_seed: str = _DEFAULT_SAMPLE_SEED,
) -> tuple[list[dict[str, Any]], LoadNotes]:
    """Load event records from event_store, with what the load absorbed.

    Unwraps the {event_id, record} envelope that event_store.list_all_events
    returns, and reports three things it used to swallow — see ``LoadNotes``.

    ``sample_size`` selects by hash rank (``app.utils.stable_sample``), not by
    position. The old body was ``random.seed(42)`` followed by
    ``random.sample``, under the comment "deterministic sampling for
    reproducibility": ``sample`` picks *positions*, so two runs a week apart
    over a store that gained events replayed different events while the report
    claimed to be the same measurement. It also reseeded the process-global
    RNG from inside a read-only diagnostic.
    """
    from app.memory.event_store import list_all_events
    entries = list_all_events()
    records = [e["record"] for e in entries if isinstance(e.get("record"), dict)]
    notes = LoadNotes()

    if event_ids:
        wanted = {eid for eid in event_ids if eid}
        records = [r for r in records if r.get("event_id") in wanted]
        found = {r.get("event_id") for r in records}
        notes.missing_event_ids = sorted(wanted - found)  # type: ignore[operator]

    # Records are keyed by event_id downstream (``_run_marginal_loop`` builds
    # ``{r["event_id"]: ...}``), so one without an id raises KeyError there and
    # a repeated id gets counted twice by ``add_pair`` / ``add_phase_result``.
    # Resolve both here, where it can be reported, instead of crashing or
    # inflating every count.
    unique: dict[str, dict[str, Any]] = {}
    for r in records:
        eid = r.get("event_id")
        if not isinstance(eid, str) or not eid:
            notes.skipped_no_event_id += 1
            continue
        if eid in unique:
            notes.duplicate_event_ids.append(eid)
            continue
        unique[eid] = r
    notes.duplicate_event_ids = sorted(set(notes.duplicate_event_ids))
    notes.population = len(unique)

    if sample_size and len(unique) > sample_size:
        keep = stable_sample(list(unique), seed=sample_seed, size=sample_size)
        return [unique[eid] for eid in keep], notes
    return list(unique.values()), notes


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

    Guardrail exception: the guardrail runs *after* all overlays and only
    acts when ``final_displayed_direction`` is a strong direction (YES/NO).
    The standard ``all_off + only guardrails on`` baseline produces no
    direction, so the guardrail no-ops (guardrail_service returns early on
    ``final_direction is None``) and the phase reports 0 contribution even
    when it truly fires under ``all_on``. To attribute guardrail
    contribution, the base for that phase is ``all_on minus guardrails``
    (other overlays produce a direction; guardrail off) and the phase dir
    is ``all_on`` (guardrail on, may downgrade). This isolates the
    guardrail's marginal effect on top of the other overlays.
    """
    from app.replay.metrics import _effective_direction
    # 1. Baseline: all_off (raw pre-overlay direction)
    base_results = {r["event_id"]: replay_record(r, ReplayConfig.preset_all_off()) for r in records}
    # 2. Final: all_on (use current settings)
    final_results = {r["event_id"]: replay_record(r, ReplayConfig.preset_all_on()) for r in records}
    # 3. Per-phase: only P on (except guardrails — see below)
    for phase_name, field_name in _PHASE_FIELDS:
        if phase_name == "guardrails":
            # Guardrail needs a strong direction to act on. Use all_on minus
            # guardrails as the base so other overlays produce a direction;
            # compare against all_on (guardrail on) to isolate its effect.
            _run_guardrail_marginal(records, metrics, final_results)
            continue
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


def _run_guardrail_marginal(
    records: list[dict[str, Any]],
    metrics: ReplayMetrics,
    final_results: dict[str, dict[str, Any]],
) -> None:
    """Attribute guardrail contribution: base = all_on minus guardrails,
    phase = all_on (guardrail on). Isolates the guardrail's marginal
    downgrade effect on top of the other overlays' produced direction."""
    from app.replay.metrics import _effective_direction
    # Base: all overlays on EXCEPT guardrails (so a direction exists for
    # the guardrail to gate). Without this, all_off leaves
    # final_displayed_direction=None and the guardrail no-ops.
    base_cfg = ReplayConfig.preset_all_on()
    base_cfg.guardrails_enabled = False
    for r in records:
        eid = r["event_id"]
        base_replayed = replay_record(r, base_cfg)
        metrics.add_phase_result(
            event_id=eid,
            phase="guardrails",
            base_dir=_effective_direction(base_replayed),
            # phase_dir = all_on (guardrail on) — same as final_results.
            phase_dir=_effective_direction(final_results[eid]),
            final_dir=_effective_direction(final_results[eid]),
        )


def run_replay(
    records: list[dict[str, Any]],
    *,
    compare: tuple[str, str] | None = None,
    skip_marginal: bool = False,
    output_dir: Path,
    load_notes: LoadNotes | None = None,
    sample: dict[str, Any] | None = None,
) -> Path:
    """Run the replay loop and write the report. Returns the report.md path.

    ``load_notes`` and ``sample`` are the two facts this function cannot
    observe for itself; everything else in the Run block (which configs, how
    many records, whether the per-phase loop ran) is derived here so there is
    one owner of the provenance rather than two halves that can disagree.
    """
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
        # Use _effective_direction for cases.jsonl so the all_off baseline
        # side (which strips final_displayed_direction and never rebuilds it)
        # still contributes its raw actionable_recommendation.direction.
        # Without this, direction_a would be null under the default
        # all_off -> current comparison, breaking per-case traceability
        # even though the aggregate metrics (which use the same helper)
        # report the correct direction_matrix.
        from app.replay.metrics import _effective_direction
        cases.append({
            "event_id": r.get("event_id"),
            "direction_a": _effective_direction(replayed_a),
            "direction_b": _effective_direction(replayed_b),
        })

    if not skip_marginal:
        _run_marginal_loop(records, metrics)

    notes = load_notes or LoadNotes(population=len(records))
    run = {
        "schema_version": REPLAY_REPORT_SCHEMA_VERSION,
        "compare": {"a": compare[0], "b": compare[1]},
        "records_replayed": len(records),
        "population": notes.population,
        "marginal": not skip_marginal,
        "sample": sample,
        "missing_event_ids": notes.missing_event_ids,
        "duplicate_event_ids": notes.duplicate_event_ids,
        "skipped_no_event_id": notes.skipped_no_event_id,
    }
    return write_report(metrics.to_dict(), output_dir, cases=cases, run=run)


def _config_by_name(name: str) -> ReplayConfig:
    if name == "current":
        return ReplayConfig.preset_all_on()
    if name == "all_off":
        return ReplayConfig.preset_all_off()
    if name == "llm_degraded":
        return ReplayConfig.preset_llm_degraded()
    raise ValueError(f"Unknown config name: {name!r} (use current/all_off/llm_degraded)")


def _validate_args(args: argparse.Namespace) -> str | None:
    """The first problem with these arguments, or None.

    ``--compare`` used to reach ``_config_by_name`` unchecked, so a typo raised
    ValueError out of ``run_replay`` and the operator got a traceback -- which
    reads as a crashed tool rather than a mistyped flag, and exits 1, the same
    code as "no records to replay".
    """
    for name in (args.compare or ()):
        if name not in _CONFIG_NAMES:
            return (
                f"--compare got unknown config {name!r} "
                f"(use one of: {', '.join(_CONFIG_NAMES)})"
            )
    if args.sample_size is not None and args.sample_size <= 0:
        return "--sample-size must be > 0"
    # stable_sample hashes the seed into the rank key, so an empty seed is a
    # legal-but-meaningless input that would silently draw one fixed subset.
    if not args.sample_seed.strip():
        return "--sample-seed must not be empty"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-ids", nargs="*", default=None, help="Specific event IDs to replay.")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Replay a stable subset of N events (see --sample-seed).")
    parser.add_argument("--sample-seed", default=_DEFAULT_SAMPLE_SEED,
                        help=f"Seed for --sample-size selection. Default: {_DEFAULT_SAMPLE_SEED!r}. "
                             "Same seed + same ids = same subset, whatever the store's size or order.")
    parser.add_argument("--compare", nargs=2, default=None, metavar=("CONFIG_A", "CONFIG_B"),
                        help="Two config names (current/all_off/llm_degraded). Default: all_off current")
    parser.add_argument("--skip-marginal", action="store_true", help="Skip the N+1 per-phase loop.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    problem = _validate_args(args)
    if problem:
        # Exit 2 -- argparse's own usage-error code. Exit 1 already means "no
        # records to replay", and a wrapper script cannot tell a bad flag from
        # an empty store if both exit the same way.
        logger.error("%s", problem)
        return 2

    if args.output_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        args.output_dir = Path("docs/reports/replay") / ts

    records, notes = _load_records(
        args.event_ids, args.sample_size, sample_seed=args.sample_seed,
    )
    if not records:
        logger.warning("No records to replay.")
        return 1
    for event_id in notes.missing_event_ids:
        logger.warning("Requested event_id not in the store: %s", event_id)
    for event_id in notes.duplicate_event_ids:
        logger.warning("Duplicate event_id in the store, kept once: %s", event_id)
    if notes.skipped_no_event_id:
        logger.warning("Skipped %d record(s) with no event_id.", notes.skipped_no_event_id)

    sample = None
    if args.sample_size:
        sample = {
            "size": args.sample_size,
            "seed": args.sample_seed,
            "strategy": SELECTION_STRATEGY,
        }

    logger.info("Replaying %d of %d records...", len(records), notes.population)
    report_path = run_replay(
        records,
        compare=tuple(args.compare) if args.compare else None,
        skip_marginal=args.skip_marginal,
        output_dir=args.output_dir,
        load_notes=notes,
        sample=sample,
    )
    logger.info("Report written to %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
