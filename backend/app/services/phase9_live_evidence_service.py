"""Read-only live prediction evidence for Phase 9 acceptance."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelPrediction,
    get_kernel_session,
)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def build_live_evidence_report() -> dict[str, Any]:
    """Summarize settled live predictions without changing kernel state.

    Readiness is evaluated independently for each sport/competition/engine
    group because calibration and learning thresholds are group-scoped.
    Missing outcomes and outcomes without ``outcome_correct`` remain unsettled.
    """
    session = get_kernel_session()
    try:
        rows = (
            session.query(KernelPrediction, KernelMatchOutcome)
            .outerjoin(
                KernelMatchOutcome,
                KernelPrediction.match_id == KernelMatchOutcome.match_id,
            )
            .all()
        )
        groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "prediction_count": 0,
                "settled_count": 0,
                "correct_count": 0,
                "brier_total": 0.0,
                "brier_count": 0,
                "latest_settled_at": None,
            }
        )
        for prediction, outcome in rows:
            key = (prediction.sport, prediction.competition, prediction.engine)
            group = groups[key]
            group["prediction_count"] += 1
            if outcome is None or outcome.outcome_correct is None:
                continue
            group["settled_count"] += 1
            group["correct_count"] += int(outcome.outcome_correct)
            if outcome.brier_score is not None:
                group["brier_total"] += float(outcome.brier_score)
                group["brier_count"] += 1
            finished_at = outcome.finished_at
            latest = group["latest_settled_at"]
            if finished_at is not None and (latest is None or finished_at > latest):
                group["latest_settled_at"] = finished_at

        threshold = max(1, int(settings.MIN_SAMPLES_FOR_CALIBRATION))
        report_groups: list[dict[str, Any]] = []
        for sport, competition, engine in sorted(groups):
            group = groups[(sport, competition, engine)]
            settled = int(group["settled_count"])
            report_groups.append(
                {
                    "sport": sport,
                    "competition": competition,
                    "engine": engine,
                    "prediction_count": int(group["prediction_count"]),
                    "settled_count": settled,
                    "remaining_samples": max(0, threshold - settled),
                    "readiness": "ready" if settled >= threshold else "insufficient_samples",
                    "accuracy": (
                        round(group["correct_count"] / settled, 4)
                        if settled
                        else None
                    ),
                    "avg_brier_score": (
                        round(group["brier_total"] / group["brier_count"], 4)
                        if group["brier_count"]
                        else None
                    ),
                    "latest_settled_at": _isoformat(group["latest_settled_at"]),
                }
            )

        settled_total = sum(item["settled_count"] for item in report_groups)
        ready_count = sum(item["readiness"] == "ready" for item in report_groups)
        return {
            "threshold": threshold,
            "total_predictions": sum(item["prediction_count"] for item in report_groups),
            "total_settled": settled_total,
            "group_count": len(report_groups),
            "ready_group_count": ready_count,
            "learning_ready": ready_count > 0,
            "groups": report_groups,
        }
    finally:
        session.close()
