"""A/B feature-flag impact CLI (Plan 5 §1.5).

Quantifies how much each Phase overlay flips the final direction when
toggled on. Reuses ``replay_record`` from the existing replay harness —
no new replay logic.

Usage:
    python -m scripts.analyze_feature_flag_impact [--sample-size N]
        [--event-ids id1,id2] [--compare all_off all_on]
        [--json report.json]

Output: an ASCII matrix of direction transitions (e.g. "YES -> WAIT: 17%")
showing the direction-change rate when the chosen phase is enabled vs
disabled.

The default comparison is ``all_off`` vs ``all_on`` (total system
impact). Use ``--compare`` to swap either side, e.g.
``--compare all_off current`` to measure against live settings.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# source_trust_registry_cli.py / review_queue_cli.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

from app.replay.config import ReplayConfig
from app.replay.runner import replay_record, simulate_llm_degraded

_DIRECTIONS = ("YES", "NO", "WAIT", "AVOID")


def _load_records(
    event_ids: list[str] | None,
    sample_size: int | None,
) -> list[dict[str, Any]]:
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


def _config_by_name(name: str) -> ReplayConfig:
    if name == "all_off":
        return ReplayConfig.preset_all_off()
    if name == "all_on":
        return ReplayConfig.preset_all_on()
    if name == "current":
        return ReplayConfig.preset_all_on()  # all None → use live settings
    if name == "llm_degraded":
        return ReplayConfig.preset_llm_degraded()
    if name == "decision_quality_only":
        return ReplayConfig.preset_decision_quality_only()
    if name == "market_quality_only":
        return ReplayConfig.preset_market_quality_only()
    if name == "source_reliability_only":
        return ReplayConfig.preset_source_reliability_only()
    if name == "guardrails_only":
        return ReplayConfig.preset_guardrails_only()
    if name == "guardrails_baseline":
        return ReplayConfig.preset_guardrails_baseline()
    raise ValueError(f"unknown config preset: {name!r}")


# Sensitive field suffixes that --set must refuse to override.
_SENSITIVE_SUFFIXES = ("_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD")
_SENSITIVE_EXACT = {"OPENAI_API_KEY"}


def parse_kv(s: str) -> tuple[str, Any]:
    """Parse a KEY=VALUE string for --set / --set-a / --set-b.

    Type coercion order: bool literal → int → float → str.
    Exits with code 2 on: missing '=', unknown setting, sensitive field.
    """
    from app.core.config import settings

    if "=" not in s:
        print(f"[FAIL] invalid --set value (expected KEY=VALUE): {s!r}", file=sys.stderr)
        raise SystemExit(2)
    key, raw = s.split("=", 1)
    key = key.strip().upper()

    if not hasattr(settings, key):
        print(f"[FAIL] unknown setting: {key!r}", file=sys.stderr)
        raise SystemExit(2)

    if key in _SENSITIVE_EXACT or key.endswith(_SENSITIVE_SUFFIXES):
        print(f"[FAIL] {key} blocked by sensitive-name policy", file=sys.stderr)
        raise SystemExit(2)

    if raw.lower() in ("true", "false", "on", "off", "yes", "no"):
        val = raw.lower() in ("true", "on", "yes")
    else:
        try:
            val = int(raw)
        except ValueError:
            try:
                val = float(raw)
            except ValueError:
                val = raw
    return (key, val)


_PER_PHASE_PRESETS = (
    "decision_quality_only",
    "market_quality_only",
    "source_reliability_only",
    "guardrails_only",
)


def _effective_direction(record: dict[str, Any]) -> str | None:
    """Return the effective direction of a record under a replay config.

    Fallback chain (mirrors how overlays derive the final direction):
      1. ``final_displayed_direction`` — set by merge_quality_overlays when
         at least one overlay ran. When ``all_off`` is used, replay_record
         strips this field and does not regenerate it.
      2. ``actionable_recommendation.direction`` — the pre-overlay direction
         from the LLM analysis. This is what ``raw_direction`` in every
         overlay block mirrors.

    Returns ``None`` when neither field yields a direction in
    ``_DIRECTIONS`` (YES/NO/WAIT/AVOID). ``probability.direction`` is
    intentionally NOT used — it holds ``rising``/``falling``/``stable``
    (see scoring_service.probability_direction), not a decision direction.
    Records with ``None`` direction are excluded from the matrix AND from
    the total, so the change-rate denominator stays correct.
    """
    dir_val = record.get("final_displayed_direction")
    if dir_val in _DIRECTIONS:
        return dir_val
    rec = record.get("actionable_recommendation")
    if isinstance(rec, dict):
        rec_dir = rec.get("direction")
        if rec_dir in _DIRECTIONS:
            return rec_dir
    return None


def _compute_direction_matrix(
    records: list[dict[str, Any]],
    cfg_a: ReplayConfig,
    cfg_b: ReplayConfig,
    name_a: str | None = None,
    name_b: str | None = None,
) -> tuple[dict[str, dict[str, int]], int]:
    """Run each record under cfg_a (off) and cfg_b (on), tally direction
    transitions into a matrix[prev_dir][cur_dir] = count.

    Returns (matrix, counted) where ``counted`` is the number of records
    that produced a direction in _DIRECTIONS under BOTH configs. Records
    lacking ``actionable_recommendation.direction`` (and without a
    ``final_displayed_direction`` from overlays) are excluded from the
    matrix AND from ``counted``, so the change-rate denominator stays
    correct.

    ``name_a`` / ``name_b``: preset names corresponding to cfg_a / cfg_b.
    When a name is ``"llm_degraded"``, ``simulate_llm_degraded`` is called
    after ``replay_record`` to force ``degraded_mode=True`` and re-run the
    guardrail so ``llm_degraded_blocks_act`` actually fires. Without this
    post-step, ``preset_llm_degraded`` only builds the telemetry block —
    it does not flip degraded mode, so rule 1 would never trigger and the
    A/B matrix would underestimate the degradation scenario's impact.
    See ``replay_decision_pipeline.run_replay`` for the same pattern.
    """
    matrix: dict[str, dict[str, int]] = {
        a: {b: 0 for b in _DIRECTIONS} for a in _DIRECTIONS
    }
    counted = 0
    for record in records:
        replayed_a = replay_record(record, cfg_a)
        if name_a == "llm_degraded":
            simulate_llm_degraded(replayed_a, cfg=cfg_a)
        replayed_b = replay_record(record, cfg_b)
        if name_b == "llm_degraded":
            simulate_llm_degraded(replayed_b, cfg=cfg_b)
        dir_a = _effective_direction(replayed_a)
        dir_b = _effective_direction(replayed_b)
        if dir_a is not None and dir_b is not None:
            matrix[dir_a][dir_b] += 1
            counted += 1
    return matrix, counted


def _format_matrix(matrix: dict[str, dict[str, int]], total: int) -> str:
    """Render the matrix as an ASCII table with a change-rate summary."""
    lines: list[str] = []
    lines.append("[INFO] Direction transition matrix (rows = off, cols = on):")
    header = "        " + "  ".join(f"{d:>6}" for d in _DIRECTIONS)
    lines.append(header)
    for a in _DIRECTIONS:
        row = f"  {a:<4} " + "  ".join(f"{matrix[a][b]:>6}" for b in _DIRECTIONS)
        lines.append(row)
    # Change rate = (total - diagonal) / total.
    diagonal = sum(matrix[a][a] for a in _DIRECTIONS)
    changed = total - diagonal
    rate = (changed / total * 100.0) if total > 0 else 0.0
    lines.append("")
    lines.append(f"[INFO] Total events: {total}")
    lines.append(f"[INFO] Direction changed: {changed} ({rate:.1f}%)")
    lines.append(f"[INFO] Direction unchanged: {diagonal} ({100.0 - rate:.1f}%)")
    lines.append(f"[INFO] Change rate: {rate:.1f}% ({changed} of {total})")
    # Top transitions (excluding diagonal).
    transitions: list[tuple[str, str, int]] = []
    for a in _DIRECTIONS:
        for b in _DIRECTIONS:
            if a != b and matrix[a][b] > 0:
                transitions.append((a, b, matrix[a][b]))
    transitions.sort(key=lambda t: t[2], reverse=True)
    if transitions:
        lines.append("[INFO] Top transitions:")
        for a, b, n in transitions:
            pct = (n / total * 100.0) if total > 0 else 0.0
            lines.append(f"       {a} -> {b}: {n} ({pct:.1f}%)")
    return "\n".join(lines)


def _print(text: str) -> None:
    print(text, flush=True)


def _run_diff_mode(
    records: list[dict[str, Any]],
    compare_a: str,
    compare_b: str,
    shared_overrides: dict[str, Any],
    a_overrides: dict[str, Any],
    b_overrides: dict[str, Any],
    diff_report: bool,
    diff_report_path: str | None,
    diff_json: str | None,
) -> int:
    """Run diff mode: replay under A and B, build_diff, render output."""
    from app.services.quality_diff_service import build_diff

    cfg_a = _config_by_name(compare_a)
    cfg_b = _config_by_name(compare_b)
    cfg_a.settings_overrides = {**shared_overrides, **a_overrides} or None
    cfg_b.settings_overrides = {**shared_overrides, **b_overrides} or None

    _print(f"[INFO] Config A: preset={compare_a}, settings_overrides={cfg_a.settings_overrides or {}}")
    _print(f"[INFO] Config B: preset={compare_b}, settings_overrides={cfg_b.settings_overrides or {}}")

    records_a: list[dict[str, Any]] = []
    records_b: list[dict[str, Any]] = []
    for record in records:
        replayed_a = replay_record(record, cfg_a)
        replayed_b = replay_record(record, cfg_b)
        # Inject outcome back (replay preserves it, but make contract explicit)
        outcome = record.get("outcome")
        if outcome is not None:
            replayed_a["outcome"] = outcome
            replayed_b["outcome"] = outcome
        records_a.append(replayed_a)
        records_b.append(replayed_b)

    config_meta_a = {
        "preset": compare_a,
        "settings_overrides": cfg_a.settings_overrides or {},
        "name": _config_label(compare_a, cfg_a.settings_overrides),
    }
    config_meta_b = {
        "preset": compare_b,
        "settings_overrides": cfg_b.settings_overrides or {},
        "name": _config_label(compare_b, cfg_b.settings_overrides),
    }

    diff = build_diff(records_a, records_b, config_meta_a, config_meta_b)

    # effective_config block for JSON
    effective_a = {
        "preset": compare_a,
        "settings_overrides": cfg_a.settings_overrides or {},
        "applied_bool_fields": {
            f.upper(): v for f, v in cfg_a.__dict__.items()
            if f != "settings_overrides" and v is not None
        },
    }
    effective_b = {
        "preset": compare_b,
        "settings_overrides": cfg_b.settings_overrides or {},
        "applied_bool_fields": {
            f.upper(): v for f, v in cfg_b.__dict__.items()
            if f != "settings_overrides" and v is not None
        },
    }

    if diff_report or diff_report_path:
        text = _render_diff_text(diff)
        if diff_report:
            _print(text)
        if diff_report_path:
            Path(diff_report_path).write_text(text, encoding="utf-8")
            _print(f"[OK] Text diff report written to {diff_report_path}")

    if diff_json:
        payload = {**diff, "effective_config_a": effective_a, "effective_config_b": effective_b}
        out_path = Path(diff_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        _print(f"[OK] JSON diff report written to {out_path}")

    _print("[OK] Done.")
    return 0


def _config_label(preset: str, overrides: dict[str, Any] | None) -> str:
    """Build a human-readable label like 'current +MARKET_MAX_SPREAD_PCT=15'."""
    if not overrides:
        return preset
    summary = ",".join(f"+{k}={v}" for k, v in overrides.items())
    return f"{preset} {summary}"


def _render_diff_text(diff: dict[str, Any]) -> str:
    """Render the diff report as human-readable text."""
    lines: list[str] = []
    a = diff["config_a"]
    b = diff["config_b"]
    lines.append(f"Config A: preset={a['preset']}, settings_overrides={a['settings_overrides']}")
    lines.append(f"Config B: preset={b['preset']}, settings_overrides={b['settings_overrides']}")
    lines.append("")

    ov = diff["overview"]
    lines.append("Overview")
    lines.append("─" * 60)
    lines.append(f"  n_total: {ov['n_total']}")
    lines.append(
        f"  n_direction_compared: {ov['n_direction_compared']}  "
        f"(n_missing_a: {ov['n_missing_a']}, n_missing_b: {ov['n_missing_b']})"
    )
    lines.append(f"  n_scored_compared: {ov['n_scored_compared']}")
    changed = ov["direction_changed"]
    rate = ov["change_rate"]
    rate_pct = f"{rate * 100:.1f}%" if rate is not None else "—"
    lines.append(f"  direction_changed: {changed} ({rate_pct})")
    lines.append(f"  change_rate: {rate}")
    lines.append("")

    rs = diff["regression_summary"]
    lines.append("Regression summary")
    lines.append("─" * 60)
    lines.append(f"  accuracy_regressions: {rs['accuracy_regressions']} slices")
    lines.append(f"  accuracy_improvements: {rs['accuracy_improvements']} slices")
    lines.append(f"  brier_regressions: {rs['brier_regressions']} slices")
    lines.append(f"  brier_improvements: {rs['brier_improvements']} slices")
    lad = rs["largest_accuracy_drop"]
    if lad:
        lines.append(f"  largest_accuracy_drop: {lad['slice']}: {lad['delta']:+.4f}")
    lbd = rs["largest_brier_drop"]
    if lbd:
        lines.append(f"  largest_brier_drop: {lbd['slice']}: {lbd['delta']:+.4f}")
    lines.append("")

    # Direction matrix
    from app.services.quality_diff_service import DIRECTION_LABELS
    keys = DIRECTION_LABELS + ("OTHER",)
    lines.append("Direction matrix (rows=A, cols=B)")
    lines.append("─" * 60)
    header = "        " + "  ".join(f"{d:>6}" for d in keys)
    lines.append(header)
    for r in keys:
        row = f"  {r:<4} " + "  ".join(f"{diff['direction_matrix'][r][c]:>6}" for c in keys)
        lines.append(row)
    lines.append("")

    # Top transitions
    if diff["top_transitions"]:
        lines.append("Top transitions")
        lines.append("─" * 60)
        for t in diff["top_transitions"][:10]:
            lines.append(f"  {t['from']} -> {t['to']}: {t['n']} ({t['pct']:.1f}%)")
        lines.append("")

    # Slice diffs
    for dim_name, slices in diff["slice_diff"].items():
        lines.append(f"Slice diff: {dim_name}")
        lines.append("─" * 60)
        if not slices:
            lines.append("  (no data)")
            lines.append("")
            continue
        lines.append(
            f"  {'slice':<28} {'n_a':>4} {'n_b':>4} {'acc_a':>6} {'acc_b':>6} {'Δacc':>7} "
            f"{'brier_a':>7} {'brier_b':>7} {'Δbrier':>7}"
        )
        for key in sorted(slices.keys(), key=lambda k: -(slices[k]["a"]["n"] + slices[k]["b"]["n"])):
            s = slices[key]
            a, b, d = s["a"], s["b"], s["delta"]
            acc_a = f"{a['direction_accuracy']:.3f}" if a["direction_accuracy"] is not None else "—"
            acc_b = f"{b['direction_accuracy']:.3f}" if b["direction_accuracy"] is not None else "—"
            dacc = f"{d['direction_accuracy']:+.4f}" if d["direction_accuracy"] is not None else "—"
            br_a = f"{a['brier']['brier_score']:.4f}" if a["brier"]["brier_score"] is not None else "—"
            br_b = f"{b['brier']['brier_score']:.4f}" if b["brier"]["brier_score"] is not None else "—"
            dbr = f"{d['brier_score']:+.4f}" if d["brier_score"] is not None else "—"
            lines.append(
                f"  {key:<28} {a['n']:>4} {b['n']:>4} {acc_a:>6} {acc_b:>6} {dacc:>7} "
                f"{br_a:>7} {br_b:>7} {dbr:>7}"
            )
        lines.append("")

    if diff["diff_errors"]:
        lines.append(f"Diff errors: {len(diff['diff_errors'])}")
        for err in diff["diff_errors"][:10]:
            lines.append(f"  {err['event_id']} (side={err['side']}, stage={err['stage']}): {err['error']}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analyze_feature_flag_impact",
        description="Quantify how much each Phase overlay flips the final "
                    "direction when toggled on (Plan 5 §1.5).",
    )
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Random sample N records (deterministic seed).")
    parser.add_argument("--event-ids", type=str, default=None,
                        help="Comma-separated event ids to restrict the run.")
    parser.add_argument("--compare", nargs=2,
                        default=None,
                        metavar=("CONFIG_A", "CONFIG_B"),
                        help="Two config presets to compare "
                             "(all_off / all_on / current / llm_degraded / "
                             "decision_quality_only / market_quality_only / "
                             "source_reliability_only / guardrails_baseline / "
                             "guardrails_only). "
                             "Default: all_off all_on (legacy mode) or "
                             "current current (diff mode).")
    parser.add_argument("--per-phase", action="store_true", default=False,
                        help="Run a marginal-impact comparison for each "
                             "overlay: all_off vs decision_quality_only / "
                             "market_quality_only / source_reliability_only, "
                             "and guardrails_baseline (DQ + LLM telemetry + "
                             "execution_quality, guardrails off) vs "
                             "guardrails_only (same prerequisites + all 4 "
                             "guardrail rules on). Prints a matrix per "
                             "overlay so you can see which overlay causes "
                             "the most direction flips.")
    parser.add_argument("--json", type=str, default=None,
                        metavar="PATH",
                        help="Write a JSON report to this path.")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="Override a settings field for BOTH configs (repeatable). "
                             "Applies after preset. bool: true/false/on/off/yes/no.")
    parser.add_argument("--set-a", action="append", default=[], metavar="KEY=VALUE",
                        help="Override a settings field for config A only (repeatable).")
    parser.add_argument("--set-b", action="append", default=[], metavar="KEY=VALUE",
                        help="Override a settings field for config B only (repeatable).")
    parser.add_argument("--diff-report", action="store_true", default=False,
                        help="Output a diff report (direction matrix + slice deltas + "
                             "regression summary). Default mode without this flag is "
                             "the legacy direction-matrix-only output.")
    parser.add_argument("--diff-report-path", type=str, default=None, metavar="PATH",
                        help="Write the text diff report to this file (instead of stdout).")
    parser.add_argument("--diff-json", type=str, default=None, metavar="PATH",
                        help="Write the JSON diff report to this file.")
    args = parser.parse_args(argv)

    # --- Mode + combo validation ---
    diff_mode = args.diff_report or args.diff_report_path is not None or args.diff_json is not None
    has_set_ab = bool(args.set_a or args.set_b)

    if args.per_phase and diff_mode:
        print("[FAIL] --per-phase and --diff-* are mutually exclusive", file=sys.stderr)
        return 2
    if args.json and args.diff_json:
        print("[FAIL] --json and --diff-json are mutually exclusive", file=sys.stderr)
        return 2
    if (has_set_ab or args.set) and not diff_mode:
        print("[FAIL] --set/--set-a/--set-b require a --diff-* output flag", file=sys.stderr)
        return 2
    if args.diff_json and args.diff_report_path:
        print("[FAIL] --diff-json and --diff-report-path are mutually exclusive (pick one file format)", file=sys.stderr)
        return 2

    # Parse --set* into override dicts
    shared_overrides = dict(parse_kv(s) for s in args.set)
    a_overrides = dict(parse_kv(s) for s in args.set_a)
    b_overrides = dict(parse_kv(s) for s in args.set_b)

    # Determine preset names
    if args.compare:
        compare_a, compare_b = args.compare
    elif diff_mode:
        compare_a, compare_b = "current", "current"
    else:
        # Legacy mode without --compare: preserve pre-existing default.
        compare_a, compare_b = "all_off", "all_on"

    event_ids = None
    if args.event_ids:
        event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]

    records = _load_records(event_ids, args.sample_size)
    if not records:
        _print("[WARN] No records found. Exiting.")
        return 0

    _print(f"[INFO] Loaded {len(records)} records.")

    if diff_mode:
        return _run_diff_mode(
            records, compare_a, compare_b,
            shared_overrides, a_overrides, b_overrides,
            args.diff_report, args.diff_report_path, args.diff_json,
        )

    # Legacy mode (--per-phase or --compare + optional --json)
    if args.per_phase:
        # Per-phase mode. For each overlay, compare the "without it" baseline
        # vs the "with it" config to isolate that overlay's marginal impact.
        # - decision_quality / market_quality / source_reliability: all_off vs <overlay>_only
        # - guardrails: guardrails_baseline (DQ + LLM telemetry + execution_quality,
        #   guardrails off) vs guardrails_only (same prerequisites + guardrails on
        #   with all 4 rules). This isolates guardrails' marginal impact: rule 1
        #   needs llm_telemetry.degraded_mode, rule 4 needs execution_quality, so
        #   both prerequisites must be present in the baseline too.
        cfg_off = ReplayConfig.preset_all_off()
        cfg_guardrails_base = ReplayConfig.preset_guardrails_baseline()
        json_phases: list[dict[str, Any]] = []
        for phase_name in _PER_PHASE_PRESETS:
            cfg_phase = _config_by_name(phase_name)
            if phase_name == "guardrails_only":
                baseline_cfg = cfg_guardrails_base
                baseline_name = "guardrails_baseline"
            else:
                baseline_cfg = cfg_off
                baseline_name = "all_off"
            _print(f"\n[INFO] Comparing {baseline_name} vs {phase_name}...")
            matrix, counted = _compute_direction_matrix(
                records, baseline_cfg, cfg_phase, baseline_name, phase_name,
            )
            report = _format_matrix(matrix, total=counted)
            _print(report)
            json_phases.append({
                "compare": [baseline_name, phase_name],
                "total": counted,
                "loaded": len(records),
                "matrix": matrix,
            })
        if args.json:
            out_path = Path(args.json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "mode": "per_phase",
                "loaded": len(records),
                "phases": json_phases,
            }
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            _print(f"\n[OK] JSON report written to {out_path}")
        _print("\n[OK] Done.")
        return 0

    try:
        cfg_a = _config_by_name(compare_a)
        cfg_b = _config_by_name(compare_b)
    except ValueError as e:
        _print(f"[FAIL] {e}")
        return 2

    _print(f"[INFO] Comparing {compare_a} vs {compare_b}...")
    matrix, counted = _compute_direction_matrix(
        records, cfg_a, cfg_b, compare_a, compare_b,
    )
    report = _format_matrix(matrix, total=counted)
    _print(report)

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "compare": [compare_a, compare_b],
            "total": counted,
            "loaded": len(records),
            "matrix": matrix,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        _print(f"[OK] JSON report written to {out_path}")

    _print("[OK] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
