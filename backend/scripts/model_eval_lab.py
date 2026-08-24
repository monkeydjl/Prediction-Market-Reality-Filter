"""Model evaluation lab CLI (Plan §4.6, Q1 常规化).

Pure read-only slicing of resolved events by model / analysis_quality /
degraded_mode. Reports Brier / ECE / direction accuracy / cost / guardrail
rate per group. Distinct from report_quality_metrics.py (which slices by
source_type / analysis_quality / edge_bucket / source_reliability).

Q1 added three things that make a run comparable to the last one:
  - a **pinned eval set** (--eval-set / --write-eval-set): membership written
    down and fingerprinted, so a re-graded record shows up as drift instead of
    silently moving the score;
  - **version numbers**: report_schema_version on the report, name/revision
    plus a content digest on the set;
  - a **release gate** (--gate): thresholds from settings turned into an exit
    code. Off unless the flag is passed.

Usage:
    python -m scripts.model_eval_lab
    python -m scripts.model_eval_lab --sample 50
    python -m scripts.model_eval_lab --event-ids evt-001,evt-002
    python -m scripts.model_eval_lab --min-samples 10 --json
    python -m scripts.model_eval_lab --write-eval-set evalsets/baseline.json --size 50
    python -m scripts.model_eval_lab --eval-set evalsets/baseline.json --gate
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from typing import Any

# UTF-8 stdout for Windows GBK console safety.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.model_eval_lab_service import (  # noqa: E402
    build_model_eval_report,
    extract_model_metrics,
)

_DEFAULT_SAMPLE_SEED = "model-eval"


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


# ─── Collection ────────────────────────────────────────────────────────────

def _collect_entries(
    sample: int | None,
    event_ids: list[str] | None,
    *,
    sample_seed: str = _DEFAULT_SAMPLE_SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load resolved events from event_store, extract model metrics.

    Returns (items, report_errors).
    report_errors only records:
      - record is not a dict
      - extract_model_metrics raised
    Does NOT validate field types (degraded_mode not bool etc.).

    When event_ids is given, filters first; then applies sample within
    the filtered set.

    --sample used to call ``random.Random(42).sample``, which picks *positions*
    and so depended on the store's length and order: adding 20 resolved events
    replaced 10 of 50 members and a store rewrite (which reorders) replaced 36
    of 50. It now ranks by ``sha256(seed || event_id)``
    (``model_eval_set_service.select_event_ids``), which is order-independent
    and lets a new event displace at most one incumbent. Membership still moves
    as the store grows -- that is what --eval-set is for.
    """
    from app.memory import event_store
    from app.services.model_eval_set_service import select_event_ids
    entries = event_store.list_resolved_events()
    if event_ids:
        id_set = set(event_ids)
        entries = [e for e in entries if e.get("event_id") in id_set]
    if sample is not None and sample < len(entries):
        keep = set(select_event_ids(
            [str(e.get("event_id")) for e in entries], seed=sample_seed, size=sample,
        ))
        entries = [e for e in entries if str(e.get("event_id")) in keep]

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            errors.append({
                "event_id": entry.get("event_id", "?"),
                "error": "record missing or not a dict",
            })
            continue
        try:
            items.append(extract_model_metrics(record))
        except Exception as exc:
            errors.append({
                "event_id": record.get("event_id", "?"),
                "error": str(exc),
            })
    return items, errors


# ─── Eval set I/O ──────────────────────────────────────────────────────────

def _load_eval_set(path: Path) -> dict[str, Any]:
    """Read and validate a pinned eval-set manifest. Raises ValueError.

    Validation problems are joined into one message so an operator sees every
    problem in the file at once instead of fixing them one run at a time.
    """
    from app.services.model_eval_set_service import validate_manifest

    manifest = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError(
            f"{path} is not a usable eval set:\n  - " + "\n  - ".join(problems)
        )
    return manifest


def _write_eval_set(
    path: Path,
    items: list[dict[str, Any]],
    *,
    name: str,
    revision: str,
    seed: str,
    size: int,
    force: bool,
) -> dict[str, Any]:
    """Mint a manifest over ``items`` and write it. Raises ValueError.

    Refuses to overwrite an existing file without --force: a pinned set is the
    thing every past report was measured against, and silently replacing it
    makes those reports unverifiable with no trace that anything moved.
    """
    from app.services.model_eval_set_service import build_manifest

    if path.exists() and not force:
        raise ValueError(f"{path} already exists (pass --force to replace it)")
    manifest = build_manifest(
        items, name=name, revision=revision, seed=seed, size=size,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return manifest


# ─── Rendering ─────────────────────────────────────────────────────────────

def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "  -  "
    return f"{v * 100:.2f}%"


def _fmt_cost(v: float | None, n: int) -> str:
    if n == 0:
        return "[n/a]"
    return f"${v:.4f}" if v is not None else "[n/a]"


def _fmt_brier(block: dict[str, Any]) -> str:
    """Brier with the count it was computed from.

    ``n`` in these tables is the slice size, not the Brier denominator: the
    live store had a 45-event slice whose Brier came from 6 records. Every
    metric here carries its own count for the same reason dir_acc always
    printed ``(48/59)``.
    """
    b = block.get("brier_score")
    return "  -  " if b is None else f"{b:.4f}({block.get('n', 0)})"


def _fmt_ece(ece: float | None, ece_n: int) -> str:
    return "  -  " if ece is None else f"{ece:.2f}({ece_n})"


def _render_slice_table(
    title: str,
    slices: dict[str, dict[str, Any]],
    key_label: str,
) -> list[str]:
    """Render one slice dimension as an ASCII table."""
    lines: list[str] = [title, "=="]
    lines.append(
        f"  {key_label:<24} {'n':>4} {'brier(n)':>11} {'ece(n)':>10} "
        f"{'dir_acc':>10} {'cost_avg':>10} {'guard%':>8} {'degr%':>7}"
    )
    for k in sorted(slices.keys(), key=lambda x: -slices[x]["n"]):
        s = slices[k]
        brier_str = _fmt_brier(s["brier"])
        ece_str = _fmt_ece(s["ece"], s.get("ece_n", 0))
        acc = s["direction_accuracy"]
        dc_true = s["direction_correct_true"]
        dc_false = s["direction_correct_false"]
        acc_str = "  -  " if acc is None else f"{acc:.4f} ({dc_true}/{dc_true + dc_false})"
        cost_str = _fmt_cost(s["cost_avg"], s["cost_n"])
        guard_str = _fmt_pct(s["guardrail_rate"])
        degr_str = _fmt_pct(s["degraded_rate"])
        suffix = "  [INSUFFICIENT]" if s.get("insufficient_samples") else ""
        lines.append(
            f"  {k:<24} {s['n']:>4} {brier_str:>11} {ece_str:>10} "
            f"{acc_str:>10} {cost_str:>10} {guard_str:>8} {degr_str:>7}{suffix}"
        )
    lines.append("")
    return lines


def _render_eval_set(block: dict[str, Any]) -> list[str]:
    """Render the pinned-set header. Missing / drifted ids are named, not counted
    only: the point of the block is that the operator can go look at them."""
    sel = block.get("selection") or {}
    lines = [
        "== Eval Set ==",
        f"  name={block.get('name')}  revision={block.get('revision')}  "
        f"digest={str(block.get('digest'))[:12]}",
        f"  minted_at={block.get('created_at')}  "
        f"seed={sel.get('seed')}  population_at_mint={sel.get('population')}",
        f"  matched={block.get('matched')}/{block.get('event_count')}  "
        f"coverage={_fmt_pct(block.get('coverage'))}  "
        f"ignored_outside_set={block.get('ignored')}  "
        f"complete={block.get('complete')}",
    ]
    missing = block.get("missing_event_ids") or []
    drifted = block.get("drifted_event_ids") or []
    if missing:
        lines.append(f"  [WARN] missing from store ({len(missing)}): {', '.join(missing)}")
    if drifted:
        lines.append(
            f"  [WARN] re-graded since minting ({len(drifted)}): {', '.join(drifted)}"
        )
    lines.append("")
    return lines


def _render_gate(gate: dict[str, Any]) -> list[str]:
    """Render the release gate verdict.

    The metric_n column is each check's own denominator, which is why a check
    can fail while the value beside it looks fine.
    """
    verdict = "PASS" if gate["passed"] else "FAIL"
    lines = [f"== Release Gate: {verdict} ==", ""]
    lines.append(
        f"  {'check':<26} {'value':>10} {'cmp':>4} {'threshold':>10} "
        f"{'metric_n':>9}  result"
    )
    for c in gate["checks"]:
        value = c["value"]
        value_str = f"{value:.4f}" if isinstance(value, float) else str(value)
        count = c.get("sample_count")
        count_str = "  -  " if count is None else str(count)
        result = "ok" if c["passed"] else "FAIL"
        note = f"  ({c['reason']})" if c.get("reason") else ""
        lines.append(
            f"  {c['name']:<26} {value_str:>10} {c['comparison']:>4} "
            f"{str(c['threshold']):>10} {count_str:>9}  {result}{note}"
        )
    lines.append("")
    if not gate["passed"]:
        lines.append(f"  Blocking: {', '.join(gate['failed'])}")
        lines.append("")
    return lines


def _render_text(report: dict[str, Any], gate: dict[str, Any] | None = None) -> str:
    """Render human-readable ASCII report."""
    lines: list[str] = []
    ov = report["overview"]
    lines.append(
        f"[INFO] Loaded {ov['n']} resolved events "
        f"({len(report['report_errors'])} report errors)"
    )
    lines.append(f"[INFO] Min samples for table display: {report['min_samples']}")
    lines.append(f"[INFO] report_schema_version={report.get('report_schema_version')}")
    lines.append("")

    if isinstance(report.get("eval_set"), dict):
        lines.extend(_render_eval_set(report["eval_set"]))

    # Overview
    lines.append(f"== Overview (all {ov['n']} events) ==")
    brier_str = _fmt_brier(ov["brier"])
    ece_str = _fmt_ece(ov["ece"], ov.get("ece_n", 0))
    acc = ov["direction_accuracy"]
    dc_true = ov["direction_correct_true"]
    dc_false = ov["direction_correct_false"]
    acc_str = "  -  " if acc is None else f"{acc:.4f} ({dc_true}/{dc_true + dc_false})"
    cost_str = _fmt_cost(ov["cost_avg"], ov["cost_n"])
    lines.append(
        f"  n={ov['n']}  brier={brier_str}  ece={ece_str}  "
        f"direction_acc={acc_str}"
    )
    lines.append(
        f"  cost_total=${ov['cost_total']:.4f}  cost_avg={cost_str} (n={ov['cost_n']})  "
        f"guardrail_rate={_fmt_pct(ov['guardrail_rate'])}  "
        f"degraded_rate={_fmt_pct(ov['degraded_rate'])}"
    )
    lines.append("")

    # Slices
    lines.extend(_render_slice_table("== By Model ==", report["by_model"], "model"))
    lines.extend(_render_slice_table(
        "== By Analysis Quality ==", report["by_analysis_quality"], "analysis_quality",
    ))
    lines.extend(_render_slice_table(
        "== By Degraded Mode ==", report["by_degraded_mode"], "mode",
    ))

    # Calibration deviation
    lines.append("== Calibration Deviation ==")
    lines.append(f"  {'bucket':<10} {'n':>4} {'pred_mean':>10} {'act_mean':>10} {'dev':>7}")
    for row in report["calibration_deviation"]:
        pred = "  -  " if row["predicted_mean"] is None else f"{row['predicted_mean']:.2f}"
        act = "  -  " if row["actual_mean"] is None else f"{row['actual_mean']:.2f}"
        dev = "  -  " if row["deviation"] is None else f"{row['deviation']:+.2f}"
        lines.append(f"  {row['bucket']:<10} {row['n']:>4} {pred:>10} {act:>10} {dev:>7}")
    lines.append("")

    if gate is not None:
        lines.extend(_render_gate(gate))

    # Report errors
    if report["report_errors"]:
        lines.append(f"== Report Errors ({len(report['report_errors'])}) ==")
        for err in report["report_errors"]:
            lines.append(f"  [WARN] {err.get('event_id', '?')}: {err['error']}")
        lines.append("")

    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model_eval_lab",
        description=(
            "Model evaluation lab. Slices resolved events by model / "
            "analysis_quality / degraded_mode. Reports Brier / ECE / "
            "direction accuracy / cost / guardrail rate per group."
        ),
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Evaluate N resolved events chosen by a stable hash of the event "
             "id (order-independent). Membership still changes as the store "
             "grows -- use --eval-set for a set that does not move.",
    )
    parser.add_argument(
        "--sample-seed", type=str, default=_DEFAULT_SAMPLE_SEED,
        help=f"Seed for --sample (default {_DEFAULT_SAMPLE_SEED!r}). Change it "
             "to draw a different subset of the same size -- two seeds "
             "disagreeing by a lot is itself the finding that the sample is "
             "too small. Not recorded anywhere, so it does not make a run "
             "reproducible on its own; --eval-set does that.",
    )
    parser.add_argument(
        "--event-ids", type=str, default=None,
        help="Comma-separated event IDs to restrict analysis",
    )
    parser.add_argument(
        "--min-samples", type=int, default=5,
        help="Min samples for table display (insufficient groups flagged, "
             "not dropped). Default 5. Does NOT affect overview.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    parser.add_argument(
        "--eval-set", type=str, default=None, metavar="PATH",
        help="Evaluate exactly the events pinned by an eval-set manifest. "
             "Missing and re-graded events are reported, never dropped.",
    )
    parser.add_argument(
        "--write-eval-set", type=str, default=None, metavar="PATH",
        help="Mint a new eval-set manifest at PATH and exit without reporting",
    )
    parser.add_argument(
        "--size", type=int, default=50,
        help="Events to pin when writing an eval set (default 50)",
    )
    parser.add_argument(
        "--seed", type=str, default=None,
        help="Selection seed for --write-eval-set (default: the set name). "
             "Recorded in the manifest.",
    )
    parser.add_argument(
        "--set-name", type=str, default=None,
        help="Eval set name (default: the manifest filename stem)",
    )
    parser.add_argument(
        "--set-revision", type=str, default="1",
        help="Eval set revision -- bump when membership changes (default 1)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Allow --write-eval-set to replace an existing manifest",
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Evaluate the release gate and exit 1 when it fails. Thresholds "
             "come from MODEL_EVAL_GATE_* settings. A missing measurement "
             "fails: the gate never passes on absent evidence.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> str | None:
    """The first argument problem, or None. Rejected combinations are the ones
    that would silently narrow a pinned set -- the defect --eval-set exists to
    prevent."""
    if args.sample is not None and args.sample < 0:
        return "--sample must be >= 0"
    if args.min_samples < 0:
        return "--min-samples must be >= 0"
    if args.size <= 0:
        return "--size must be > 0"
    # build_manifest rejects an empty seed because a manifest records it; reject
    # it here too so the two selection paths cannot disagree about what is legal.
    if not args.sample_seed.strip():
        return "--sample-seed must not be empty"
    if args.eval_set and args.write_eval_set:
        return "--eval-set and --write-eval-set are mutually exclusive"
    if args.eval_set and (args.sample is not None or args.event_ids):
        return "--eval-set cannot be combined with --sample / --event-ids"
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code.

    Exit codes: 0 success (report_errors present still 0); 1 --gate failed;
    2 param / load / eval-set errors.
    """
    args = _build_parser().parse_args(argv)

    problem = _validate_args(args)
    if problem is not None:
        print(f"Error: {problem}", file=sys.stderr)
        return 2

    event_ids: list[str] | None = None
    if args.event_ids is not None:
        event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]
        if not event_ids:
            print("Error: --event-ids parsed to empty list", file=sys.stderr)
            return 2

    manifest: dict[str, Any] | None = None
    if args.eval_set:
        try:
            manifest = _load_eval_set(Path(args.eval_set))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    try:
        items, report_errors = _collect_entries(
            args.sample, event_ids, sample_seed=args.sample_seed,
        )
    except Exception as exc:
        print(f"Error: failed to load events: {exc}", file=sys.stderr)
        return 2

    if args.write_eval_set:
        path = Path(args.write_eval_set)
        name = args.set_name or path.stem
        try:
            minted = _write_eval_set(
                path, items, name=name, revision=args.set_revision,
                seed=args.seed or name, size=args.size, force=args.force,
            )
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        _print(
            f"[OK] wrote {path}: name={minted['name']} "
            f"revision={minted['revision']} "
            f"events={len(minted['event_ids'])} "
            f"population={minted['selection']['population']} "
            f"unreadable_skipped={len(report_errors)} "
            f"digest={minted['digest'][:12]}"
        )
        return 0

    eval_set_summary: dict[str, Any] | None = None
    if manifest is not None:
        from app.services.model_eval_set_service import resolve_eval_set
        items, eval_set_summary = resolve_eval_set(manifest, items)

    try:
        report = build_model_eval_report(
            items, report_errors, min_samples=args.min_samples,
            eval_set=eval_set_summary,
        )
    except Exception as exc:
        print(f"Error: report build failed: {exc}", file=sys.stderr)
        return 2

    gate: dict[str, Any] | None = None
    if args.gate:
        from app.core.config import settings
        from app.services.model_eval_gate_service import (
            evaluate_release_gate,
            gate_thresholds_from_settings,
        )
        gate = evaluate_release_gate(report, gate_thresholds_from_settings(settings))
        report["release_gate"] = gate

    if args.json:
        _print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    else:
        _print(_render_text(report, gate))

    if gate is not None and not gate["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
