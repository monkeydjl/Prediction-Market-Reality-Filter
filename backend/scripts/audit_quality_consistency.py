"""Batch quality consistency auditor.

Addresses production-readiness gap §4.4: scan event_store + predictions to
detect silent conflicts between overlay fields and the final direction.

The audit catches 5 classes of inconsistency that would otherwise slip
through ``EventRecord(extra="allow")`` silently:

1. ``market_quality.score < threshold`` but ``final_displayed_direction``
   is YES/NO (should have been WAIT — market_quality downgrade didn't apply).
2. ``decision_quality.downgrade_reason`` is non-empty but
   ``final_downgrade_reason`` is None (Phase 1 downgrade lost in merge).
3. ``llm_telemetry.degraded_mode == True`` but
   ``decision_quality.analysis_quality == "llm"`` (degraded sample
   mislabeled as LLM — pollutes calibration).
4. ``source_reliability.suggested_direction == WAIT/AVOID`` but
   ``final_downgrade_reason`` doesn't mention "来源" (source downgrade
   silently dropped).
5. ``market_quality.wide_spread_flag == True`` but
   ``final_displayed_direction`` is YES/NO (hard cutoff didn't fire).

Each conflict is reported with: event_id, conflict_type, severity
(INFO/WARN/ERROR), and the offending field values. Exit code is non-zero
when any ERROR-severity conflict is found, so this can be wired into CI
or a cron audit job.

Usage:
    python -m scripts.audit_quality_consistency
    python -m scripts.audit_quality_consistency --event-id evt123
    python -m scripts.audit_quality_consistency --json > audit.json
    python -m scripts.audit_quality_consistency --verbose

CLI flags:
    --event-id ID   Audit only one event (debugging).
    --json          Output JSON instead of human-readable text.
    --verbose       Show INFO-level conflicts too (default: WARN+ only).
    --strict        Exit non-zero on any conflict (incl. INFO).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402
from app.memory import event_store  # noqa: E402


# ─── Severity levels ──────────────────────────────────────────────────────
# ERROR  — invariant violation, likely a bug in merge_quality_overlays or
#           build_*. Should never reach production. Must fix.
# WARN   — likely-correct but suspicious (e.g. score very close to threshold).
# INFO   — informational (e.g. record has no market_quality because source
#          is not prediction_market — expected, but worth surfacing).
ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


@dataclass
class Conflict:
    """One audit finding."""
    event_id: str
    conflict_type: str
    severity: str
    message: str
    field_values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Individual checks ────────────────────────────────────────────────────

def _check_market_quality_applied(
    event_id: str, record: dict[str, Any]
) -> list[Conflict]:
    """Check 1 & 5: market_quality score vs final_displayed_direction."""
    out: list[Conflict] = []
    mq = record.get("market_quality")
    if not isinstance(mq, dict):
        return out

    final_dir = record.get("final_displayed_direction")
    score = mq.get("score")
    wide = mq.get("wide_spread_flag", False)
    suggested = mq.get("suggested_direction")
    score_threshold = settings.MARKET_QUALITY_SCORE_THRESHOLD

    # Check 5: wide_spread_flag=True but direction is YES/NO (hard cutoff failed).
    if wide is True and final_dir in ("YES", "NO"):
        out.append(Conflict(
            event_id=event_id,
            conflict_type="wide_spread_not_downgraded",
            severity=ERROR,
            message=(
                f"market_quality.wide_spread_flag=True but "
                f"final_displayed_direction={final_dir} — hard cutoff "
                f"should have forced WAIT."
            ),
            field_values={
                "wide_spread_flag": wide,
                "spread_penalty": mq.get("spread_penalty"),
                "final_displayed_direction": final_dir,
                "suggested_direction": suggested,
            },
        ))

    # Check 1: score < threshold but final_dir is YES/NO.
    elif (isinstance(score, (int, float)) and score < score_threshold
          and final_dir in ("YES", "NO")
          and not wide):
        out.append(Conflict(
            event_id=event_id,
            conflict_type="market_score_below_threshold_not_downgraded",
            severity=ERROR,
            message=(
                f"market_quality.score={score} < threshold "
                f"{score_threshold} but final_displayed_direction={final_dir} "
                f"— market_quality downgrade didn't apply to final direction."
            ),
            field_values={
                "score": score,
                "score_threshold": score_threshold,
                "final_displayed_direction": final_dir,
                "suggested_direction": suggested,
            },
        ))

    return out


def _check_decision_quality_downgrade_applied(
    event_id: str, record: dict[str, Any]
) -> list[Conflict]:
    """Check 2: dq.downgrade_reason set but final_downgrade_reason is None.

    This catches two scenarios:
    - dq forced WAIT/AVOID but final is still YES/NO (downgrade direction lost)
    - dq forced WAIT/AVOID and final matches, but reason was dropped
    Both indicate merge_quality_overlays failed to propagate dq's contribution.
    """
    out: list[Conflict] = []
    dq = record.get("decision_quality")
    if not isinstance(dq, dict):
        return out

    dq_reason = dq.get("downgrade_reason")
    final_reason = record.get("final_downgrade_reason")
    dq_displayed = dq.get("displayed_direction")
    final_dir = record.get("final_displayed_direction")

    # dq had a downgrade reason but final_downgrade_reason is missing.
    # This means dq's downgrade was either reverted (direction) or
    # had its reason stripped — both are bugs.
    if dq_reason and not final_reason:
        out.append(Conflict(
            event_id=event_id,
            conflict_type="decision_quality_downgrade_lost_in_merge",
            severity=ERROR,
            message=(
                f"decision_quality.downgrade_reason is set but "
                f"final_downgrade_reason is None — Phase 1 downgrade "
                f"was lost in merge_quality_overlays."
            ),
            field_values={
                "dq_downgrade_reason": dq_reason,
                "dq_displayed_direction": dq_displayed,
                "final_displayed_direction": final_dir,
                "final_downgrade_reason": final_reason,
            },
        ))

    return out


def _check_llm_telemetry_degraded_mislabeled(
    event_id: str, record: dict[str, Any]
) -> list[Conflict]:
    """Check 3: llm_telemetry.degraded_mode=True but analysis_quality=llm."""
    out: list[Conflict] = []
    lt = record.get("llm_telemetry")
    if not isinstance(lt, dict):
        return out

    degraded = lt.get("degraded_mode")
    if degraded is True:
        # decision_quality.analysis_quality or ai_analysis_quality field.
        dq = record.get("decision_quality") or {}
        analysis_q = dq.get("analysis_quality") or record.get("analysis_quality")
        if analysis_q == "llm":
            out.append(Conflict(
                event_id=event_id,
                conflict_type="degraded_mode_mislabeled_as_llm",
                severity=WARN,
                message=(
                    f"llm_telemetry.degraded_mode=True but "
                    f"analysis_quality='llm' — degraded sample would "
                    f"pollute headline LLM calibration aggregate."
                ),
                field_values={
                    "degraded_mode": degraded,
                    "analysis_quality": analysis_q,
                },
            ))

    return out


def _check_source_reliability_applied(
    event_id: str, record: dict[str, Any]
) -> list[Conflict]:
    """Check 4: sr.suggested_direction=WAIT/AVOID but final_downgrade_reason
    doesn't mention '来源' (source)."""
    out: list[Conflict] = []
    sr = record.get("source_reliability")
    if not isinstance(sr, dict):
        return out

    sr_suggested = sr.get("suggested_direction")
    sr_reason = sr.get("downgrade_reason")
    final_reason = record.get("final_downgrade_reason")

    # sr downgraded (suggested != raw) and a reason was produced.
    if sr_suggested in ("WAIT", "AVOID") and sr_reason:
        # If final_downgrade_reason doesn't mention source/来源, sr was dropped.
        if not final_reason or "来源" not in final_reason:
            out.append(Conflict(
                event_id=event_id,
                conflict_type="source_reliability_downgrade_dropped",
                severity=WARN,
                message=(
                    f"source_reliability.suggested_direction={sr_suggested} "
                    f"with reason, but final_downgrade_reason "
                    f"({final_reason!r}) doesn't mention '来源' — Phase 4 "
                    f"downgrade may have been overridden by a stricter overlay."
                ),
                field_values={
                    "sr_suggested_direction": sr_suggested,
                    "sr_downgrade_reason": sr_reason,
                    "final_downgrade_reason": final_reason,
                },
            ))

    return out


def _audit_one_record(entry: dict[str, Any]) -> list[Conflict]:
    """Run all checks on a single event_store entry.

    ``entry`` is the {"event_id":..., "record":...} wrapper from event_store.
    """
    record = entry.get("record") or {}
    event_id = entry.get("event_id") or record.get("event_id", "<missing>")
    if not isinstance(record, dict):
        return [Conflict(
            event_id=str(event_id),
            conflict_type="malformed_record",
            severity=ERROR,
            message="record is not a dict",
        )]

    out: list[Conflict] = []
    out.extend(_check_market_quality_applied(event_id, record))
    out.extend(_check_decision_quality_downgrade_applied(event_id, record))
    out.extend(_check_llm_telemetry_degraded_mislabeled(event_id, record))
    out.extend(_check_source_reliability_applied(event_id, record))
    return out


# ─── Public API ────────────────────────────────────────────────────────────

def audit_quality_consistency(
    *,
    event_id_filter: str | None = None,
) -> list[Conflict]:
    """Scan event_store and return all conflicts found.

    When ``event_id_filter`` is provided, only that event is audited
    (useful for debugging a single record). Otherwise all events are
    scanned in event_store insertion order.
    """
    if event_id_filter:
        entry = event_store.get_event(event_id_filter)
        if entry is None:
            return [Conflict(
                event_id=event_id_filter,
                conflict_type="event_not_found",
                severity=ERROR,
                message="event_id not found in event_store",
            )]
        entries = [entry]
    else:
        entries = event_store.list_all_events()

    conflicts: list[Conflict] = []
    for entry in entries:
        conflicts.extend(_audit_one_record(entry))
    return conflicts


def _format_human(conflicts: list[Conflict], *, verbose: bool) -> str:
    """Render conflicts as human-readable text."""
    if not conflicts:
        return "✅ No conflicts found. All overlay fields are consistent.\n"

    visible = conflicts if verbose else [c for c in conflicts if c.severity != INFO]
    if not visible:
        return (
            f"ℹ️  {len(conflicts)} INFO-level conflicts hidden "
            f"(use --verbose to see them).\n"
        )

    lines = [f"❌ Found {len(visible)} conflict(s):\n"]
    by_severity: dict[str, list[Conflict]] = {}
    for c in visible:
        by_severity.setdefault(c.severity, []).append(c)

    for sev in (ERROR, WARN, INFO):
        if sev not in by_severity:
            continue
        lines.append(f"── {sev} ({len(by_severity[sev])}) ──────────────────")
        for c in by_severity[sev]:
            lines.append(f"[{c.conflict_type}] {c.event_id}")
            lines.append(f"  {c.message}")
            for k, v in c.field_values.items():
                lines.append(f"  • {k}: {v!r}")
            lines.append("")

    # Summary
    err_count = len(by_severity.get(ERROR, []))
    warn_count = len(by_severity.get(WARN, []))
    info_count = len(by_severity.get(INFO, []))
    lines.append(
        f"Total: {err_count} ERROR, {warn_count} WARN, {info_count} INFO"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit overlay field consistency across event_store.",
    )
    parser.add_argument("--event-id", help="Audit only this event_id.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--verbose", action="store_true", help="Show INFO.")
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero on any conflict (including INFO).",
    )
    args = parser.parse_args(argv)

    conflicts = audit_quality_consistency(event_id_filter=args.event_id)

    if args.json:
        print(json.dumps([c.to_dict() for c in conflicts], indent=2, ensure_ascii=False))
    else:
        print(_format_human(conflicts, verbose=args.verbose))

    err_count = sum(1 for c in conflicts if c.severity == ERROR)
    if err_count > 0:
        return 1
    if args.strict and conflicts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
