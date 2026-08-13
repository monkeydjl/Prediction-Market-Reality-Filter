"""World Cup prediction quality and confidence calibration loop."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.memory import loop_run_store
from app.models.world_cup_prediction import MatchFixture, PredictionHistory
from app.services.audit_metadata import normalize_audit_metadata
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)

ENGINE_NAMES = ("elo_odds", "hybrid", "gbm", "integrated")
CONSISTENCY_REPAIR_AUDIT_JOB_NAME = "world_cup_consistency_repair"
CONFIDENCE_BUCKETS = [
    (0.0, 0.2, "0-20%"),
    (0.2, 0.4, "20-40%"),
    (0.4, 0.6, "40-60%"),
    (0.6, 0.8, "60-80%"),
    (0.8, 1.01, "80-100%"),
]
MIN_CALIBRATION_SAMPLES = 6
MIN_BUCKET_SAMPLES = 3
MIN_INTEGRATED_WEIGHT = 0.25
MAX_INTEGRATED_WEIGHT = 0.85
QUALITY_WEIGHT_BLEND = 0.35
# Must match CALIBRATION_BLEND_RATIO in world_cup_confidence_calibration.py
_CALIBRATION_BLEND = 0.70
_EPSILON = 1e-6


def bucket_engine(method: str | None) -> str:
    """Map a prediction method string to a public engine bucket."""
    normalized = (method or "").lower()
    if "integrated" in normalized:
        return "integrated"
    if "gbm" in normalized:
        return "gbm"
    if normalized.startswith("elo") or "elo_odds" in normalized or ("elo" in normalized and "odds" in normalized):
        return "elo_odds"
    return "hybrid"


def _consistency_engine(method: str | None) -> str:
    """Bucket history rows for same-engine consistency checks."""
    if not method:
        return "unknown"
    return bucket_engine(method)


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _actual_outcome(home: int, away: int) -> str:
    if home > away:
        return "home_win"
    if away > home:
        return "away_win"
    return "draw"


def _predicted_outcome(home: float, away: float) -> str:
    home_round = round(home)
    away_round = round(away)
    if home_round > away_round:
        return "home_win"
    if away_round > home_round:
        return "away_win"
    return "draw"


def _actual_outcome_probability(history: PredictionHistory, outcome: str) -> float:
    if outcome == "home_win":
        return float(history.home_win_prob or 0.0)
    if outcome == "draw":
        return float(history.draw_prob or 0.0)
    return float(history.away_win_prob or 0.0)


def _match_date(match: MatchFixture) -> str:
    kickoff = _utc_naive(match.kickoff_utc)
    if kickoff is None:  # kickoff_utc is NOT NULL — defensive only
        return "unknown"
    return kickoff.date().isoformat()


def _timestamp_bucket(value: datetime | None) -> str:
    timestamp = _utc_naive(value)
    if timestamp is None:
        return "unknown"
    return timestamp.replace(microsecond=0).isoformat()


def _brier_score(history: PredictionHistory, outcome: str) -> float:
    actual = {
        "home_win": (1.0, 0.0, 0.0),
        "draw": (0.0, 1.0, 0.0),
        "away_win": (0.0, 0.0, 1.0),
    }[outcome]
    predicted = (
        float(history.home_win_prob or 0.0),
        float(history.draw_prob or 0.0),
        float(history.away_win_prob or 0.0),
    )
    return sum((prob - target) ** 2 for prob, target in zip(predicted, actual))


def _build_sample(match: MatchFixture, history: PredictionHistory) -> dict[str, Any]:
    # collect_quality_samples filters home_score/away_score isnot(None), so a
    # null here means the contract was broken upstream — fail loudly rather
    # than score a 0-0 that never happened (same rule as
    # world_cup_result_fact_backfill_service._score_pair).
    home_score, away_score = match.home_score, match.away_score
    if home_score is None or away_score is None:
        raise ValueError(f"fixture {match.match_id} has no final score")
    actual_home = int(home_score)
    actual_away = int(away_score)
    predicted_home = float(history.predicted_home_score)
    predicted_away = float(history.predicted_away_score)
    actual = _actual_outcome(actual_home, actual_away)
    predicted = _predicted_outcome(predicted_home, predicted_away)
    actual_prob = max(_EPSILON, min(1.0 - _EPSILON, _actual_outcome_probability(history, actual)))

    return {
        "match_id": match.match_id,
        "match_date": _match_date(match),
        "home_team": match.home_team,
        "away_team": match.away_team,
        "engine": bucket_engine(history.prediction_method),
        "prediction_method": history.prediction_method,
        "timestamp": history.timestamp.isoformat() if history.timestamp else None,
        "confidence": max(0.0, min(1.0, float(history.confidence or 0.0))),
        "predicted_score": {"home": predicted_home, "away": predicted_away},
        "actual_score": {"home": actual_home, "away": actual_away},
        "predicted_outcome": predicted,
        "actual_outcome": actual,
        "outcome_correct": predicted == actual,
        "exact_score": round(predicted_home) == actual_home and round(predicted_away) == actual_away,
        "score_mae": (abs(predicted_home - actual_home) + abs(predicted_away - actual_away)) / 2.0,
        "brier_score": _brier_score(history, actual),
        "log_loss": -math.log(actual_prob),
        "actual_outcome_probability": actual_prob,
    }


def _is_applied_history(history: PredictionHistory) -> bool:
    trigger = history.trigger or ""
    return not trigger.endswith("_comparison")


def collect_quality_samples(session: Session) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Collect latest applied pre-match prediction per engine for each finished match."""
    finished_matches = session.query(MatchFixture).filter(
        MatchFixture.status == "finished",
        MatchFixture.home_score.isnot(None),
        MatchFixture.away_score.isnot(None),
    ).all()

    counters = {
        "finished_matches": len(finished_matches),
        "matches_without_history": 0,
        "history_rows_excluded_after_kickoff": 0,
        "history_rows_excluded_comparison": 0,
    }
    if not finished_matches:
        return [], counters

    match_by_id = {match.match_id: match for match in finished_matches}
    histories = session.query(PredictionHistory).filter(
        PredictionHistory.match_id.in_(list(match_by_id.keys()))
    ).order_by(
        PredictionHistory.match_id,
        PredictionHistory.timestamp.desc(),
        PredictionHistory.id.desc(),
    ).all()

    latest_by_match_engine: dict[tuple[str, str], PredictionHistory] = {}
    seen_matches: set[str] = set()
    for history in histories:
        match = match_by_id.get(history.match_id)
        if match is None:
            continue
        seen_matches.add(history.match_id)
        if not _is_applied_history(history):
            counters["history_rows_excluded_comparison"] += 1
            continue

        kickoff = _utc_naive(match.kickoff_utc)
        timestamp = _utc_naive(history.timestamp)
        if kickoff and timestamp and timestamp > kickoff:
            counters["history_rows_excluded_after_kickoff"] += 1
            continue

        engine = bucket_engine(history.prediction_method)
        key = (history.match_id, engine)
        if key not in latest_by_match_engine:
            latest_by_match_engine[key] = history

    counters["matches_without_history"] = len(match_by_id) - len(seen_matches)
    samples = [
        _build_sample(match_by_id[match_id], history)
        for (match_id, _engine), history in latest_by_match_engine.items()
    ]
    return samples, counters


def _confidence_bucket(confidence: float) -> tuple[float, float, str]:
    for lower, upper, label in CONFIDENCE_BUCKETS:
        if lower <= confidence < upper:
            return lower, upper, label
    return CONFIDENCE_BUCKETS[-1]


def _calibration_buckets(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Heterogeneous values (label str, bounds float, counters int) would infer
    # dict[str, object] and break the `+=` accumulation below.
    buckets: list[dict[str, Any]] = [
        {
            "label": label,
            "lower": lower,
            "upper": min(1.0, upper),
            "count": 0,
            "confidence_sum": 0.0,
            "correct": 0,
        }
        for lower, upper, label in CONFIDENCE_BUCKETS
    ]

    by_label = {bucket["label"]: bucket for bucket in buckets}
    for sample in samples:
        _lower, _upper, label = _confidence_bucket(float(sample["confidence"]))
        bucket = by_label[label]
        bucket["count"] += 1
        bucket["confidence_sum"] += float(sample["confidence"])
        bucket["correct"] += 1 if sample["outcome_correct"] else 0

    result: list[dict[str, Any]] = []
    for bucket in buckets:
        count = int(bucket["count"])
        if count:
            avg_confidence = bucket["confidence_sum"] / count
            accuracy = bucket["correct"] / count
            gap = avg_confidence - accuracy
        else:
            avg_confidence = None
            accuracy = None
            gap = None
        result.append({
            "label": bucket["label"],
            "lower": bucket["lower"],
            "upper": bucket["upper"],
            "count": count,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence is not None else None,
            "accuracy": round(accuracy, 3) if accuracy is not None else None,
            "gap": round(gap, 3) if gap is not None else None,
            "is_usable": count >= MIN_BUCKET_SAMPLES,
        })
    return result


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    buckets = _calibration_buckets(samples)
    if total == 0:
        return {
            "samples": 0,
            "outcome_accuracy": None,
            "exact_score_rate": None,
            "avg_score_mae": None,
            "avg_brier_score": None,
            "avg_log_loss": None,
            "avg_confidence": None,
            "confidence_bias": None,
            "expected_calibration_error": None,
            "is_calibratable": False,
            "calibration_buckets": buckets,
        }

    outcome_accuracy = sum(1 for sample in samples if sample["outcome_correct"]) / total
    avg_confidence = sum(float(sample["confidence"]) for sample in samples) / total
    ece = 0.0
    for bucket in buckets:
        if bucket["count"] and bucket["avg_confidence"] is not None and bucket["accuracy"] is not None:
            ece += (bucket["count"] / total) * abs(bucket["avg_confidence"] - bucket["accuracy"])

    return {
        "samples": total,
        "outcome_accuracy": round(outcome_accuracy, 3),
        "exact_score_rate": round(sum(1 for sample in samples if sample["exact_score"]) / total, 3),
        "avg_score_mae": round(sum(float(sample["score_mae"]) for sample in samples) / total, 3),
        "avg_brier_score": round(sum(float(sample["brier_score"]) for sample in samples) / total, 4),
        "avg_log_loss": round(sum(float(sample["log_loss"]) for sample in samples) / total, 4),
        "avg_confidence": round(avg_confidence, 3),
        "confidence_bias": round(avg_confidence - outcome_accuracy, 3),
        "expected_calibration_error": round(ece, 3),
        "is_calibratable": total >= MIN_CALIBRATION_SAMPLES,
        "calibration_buckets": buckets,
    }


def _trend_points(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        by_date.setdefault(str(sample["match_date"]), []).append(sample)

    points: list[dict[str, Any]] = []
    for date in sorted(by_date):
        summary = _summarize(by_date[date])
        points.append({
            "date": date,
            "samples": summary["samples"],
            "outcome_accuracy": summary["outcome_accuracy"],
            "avg_brier_score": summary["avg_brier_score"],
            "avg_log_loss": summary["avg_log_loss"],
            "expected_calibration_error": summary["expected_calibration_error"],
        })
    return points


def detect_prediction_consistency_issues(
    session: Session,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Find same-match, same-engine, same-second score conflicts in history."""
    histories = session.query(PredictionHistory).order_by(
        PredictionHistory.match_id,
        PredictionHistory.timestamp,
        PredictionHistory.id,
    ).all()

    groups: dict[tuple[str, str, str], list[PredictionHistory]] = defaultdict(list)
    for history in histories:
        if not _is_applied_history(history):
            continue
        groups[(
            history.match_id,
            _consistency_engine(history.prediction_method),
            _timestamp_bucket(history.timestamp),
        )].append(history)

    issues: list[dict[str, Any]] = []
    for (match_id, engine, timestamp), rows in groups.items():
        score_variants: dict[tuple[float, float], dict[str, Any]] = {}
        for row in rows:
            score = (
                round(float(row.predicted_home_score), 3),
                round(float(row.predicted_away_score), 3),
            )
            variant = score_variants.setdefault(score, {
                "predicted_score": {"home": score[0], "away": score[1]},
                "count": 0,
                "history_ids": [],
                "triggers": set(),
                "methods": set(),
            })
            variant["count"] += 1
            variant["history_ids"].append(row.id)
            variant["triggers"].add(row.trigger or "unknown")
            variant["methods"].add(row.prediction_method or "unknown")

        if len(score_variants) <= 1:
            continue

        variants = []
        for variant in score_variants.values():
            variants.append({
                "predicted_score": variant["predicted_score"],
                "count": variant["count"],
                "history_ids": sorted(variant["history_ids"]),
                "triggers": sorted(variant["triggers"]),
                "methods": sorted(variant["methods"]),
            })

        has_unknown_method = any(
            "unknown" in variant["methods"]
            for variant in variants
        )
        issues.append({
            "type": "conflicting_same_timestamp_score",
            "severity": "warn",
            "match_id": match_id,
            "engine": engine,
            "timestamp": timestamp,
            "rows": len(rows),
            "variant_count": len(variants),
            "has_unknown_method": has_unknown_method,
            "variants": variants,
            "message": "同一场、同一时间、同一引擎出现不同预测比分。",
        })

    issues.sort(key=lambda item: (item["timestamp"], item["match_id"], item["engine"]), reverse=True)
    return issues[:limit]


def _issue_history_ids(issue: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for variant in issue.get("variants", []):
        ids.extend(int(row_id) for row_id in variant.get("history_ids", []))
    return sorted(set(ids))


def _issue_methods(issue: dict[str, Any]) -> list[str]:
    methods: set[str] = set()
    for variant in issue.get("variants", []):
        methods.update(str(method) for method in variant.get("methods", []))
    return sorted(methods)


def _repair_item(issue: dict[str, Any]) -> dict[str, Any]:
    history_ids = _issue_history_ids(issue)
    methods = _issue_methods(issue)
    if issue.get("has_unknown_method"):
        action = "manual_review_unknown_method"
        rationale = "历史行缺少 prediction_method，无法安全判断这些分数是否来自不同引擎。"
    else:
        action = "manual_review_same_engine_conflict"
        rationale = "同一公开引擎桶、同一秒内存在不同比分，需要人工确认应保留哪条历史快照。"

    return {
        "match_id": issue.get("match_id"),
        "engine": issue.get("engine"),
        "timestamp": issue.get("timestamp"),
        "history_ids": history_ids,
        "methods": methods,
        "variant_count": issue.get("variant_count", 0),
        "can_autofix": False,
        "recommended_action": action,
        "rationale": rationale,
    }


def build_consistency_repair_plan(
    session: Session | None = None,
    *,
    limit: int = 25,
) -> dict[str, Any]:
    """Return a dry-run plan for prediction history consistency issues."""
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        issues = detect_prediction_consistency_issues(session, limit=limit)
        items = [_repair_item(issue) for issue in issues]
        auto_fixable = sum(1 for item in items if item["can_autofix"])
        manual_review = len(items) - auto_fixable
        return {
            "status": "ok",
            "dry_run": True,
            "issue_count": len(items),
            "auto_fixable": auto_fixable,
            "manual_review": manual_review,
            "items": items,
        }
    finally:
        if should_close:
            close_prediction_session(session)


def _infer_method_from_same_score(
    session: Session,
    row: PredictionHistory,
    requested_ids: set[int],
) -> dict[str, Any]:
    score = (
        round(float(row.predicted_home_score), 3),
        round(float(row.predicted_away_score), 3),
    )
    candidates = session.query(PredictionHistory).filter(
        PredictionHistory.match_id == row.match_id,
        PredictionHistory.prediction_method.isnot(None),
    ).all()

    method_counts: dict[str, int] = defaultdict(int)
    source_ids: list[int] = []
    for candidate in candidates:
        if candidate.id in requested_ids or not _is_applied_history(candidate):
            continue
        candidate_score = (
            round(float(candidate.predicted_home_score), 3),
            round(float(candidate.predicted_away_score), 3),
        )
        if candidate_score != score:
            continue
        method_counts[str(candidate.prediction_method)] += 1
        source_ids.append(int(candidate.id))

    methods = sorted(method_counts)
    if len(methods) == 1:
        return {
            "inferred_method": methods[0],
            "can_apply": True,
            "reason": "same_match_same_score_known_method",
            "source_history_ids": sorted(source_ids),
            "candidate_methods": method_counts,
        }
    if len(methods) > 1:
        return {
            "inferred_method": None,
            "can_apply": False,
            "reason": "ambiguous_same_score_methods",
            "source_history_ids": sorted(source_ids),
            "candidate_methods": method_counts,
        }
    return {
        "inferred_method": None,
        "can_apply": False,
        "reason": "no_same_score_known_method",
        "source_history_ids": [],
        "candidate_methods": {},
    }


def preview_consistency_history_repair(
    history_ids: list[int],
    session: Session | None = None,
) -> dict[str, Any]:
    """Dry-run method-fill preview for selected prediction history rows."""
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        requested_ids = [int(row_id) for row_id in history_ids]
        unique_requested_ids = sorted(set(requested_ids))
        rows = session.query(PredictionHistory).filter(
            PredictionHistory.id.in_(unique_requested_ids)
        ).all() if unique_requested_ids else []
        by_id = {int(row.id): row for row in rows}

        previews: list[dict[str, Any]] = []
        for row_id in unique_requested_ids:
            row = by_id.get(row_id)
            if row is None:
                previews.append({
                    "history_id": row_id,
                    "status": "missing",
                    "can_apply": False,
                    "reason": "history_row_not_found",
                })
                continue

            payload = {
                "history_id": row_id,
                "status": "ok",
                "match_id": row.match_id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "predicted_score": {
                    "home": round(float(row.predicted_home_score), 3),
                    "away": round(float(row.predicted_away_score), 3),
                },
                "current_method": row.prediction_method,
                "trigger": row.trigger,
            }
            if row.prediction_method:
                payload.update({
                    "inferred_method": row.prediction_method,
                    "can_apply": False,
                    "reason": "already_has_method",
                    "source_history_ids": [],
                    "candidate_methods": {str(row.prediction_method): 1},
                })
            else:
                payload.update(_infer_method_from_same_score(session, row, set(unique_requested_ids)))
            previews.append(payload)

        inferable = sum(1 for item in previews if item.get("can_apply"))
        return {
            "status": "ok",
            "dry_run": True,
            "requested": len(unique_requested_ids),
            "inferable": inferable,
            "manual_review": len(previews) - inferable,
            "items": previews,
        }
    finally:
        if should_close:
            close_prediction_session(session)


def _start_consistency_repair_audit_run(enabled: bool) -> str | None:
    if not enabled:
        return None
    try:
        return loop_run_store.start_run(CONSISTENCY_REPAIR_AUDIT_JOB_NAME)
    except Exception:
        logger.exception("Failed to start consistency repair audit run")
        return None


def _consistency_repair_audit_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "dry_run": result.get("dry_run"),
        "confirm": result.get("confirm"),
        "protected": result.get("protected"),
        "requested": result.get("requested", 0),
        "inferable": result.get("inferable", 0),
        "updated": result.get("updated", 0),
        "skipped": result.get("skipped", 0),
        "manual_review": result.get("manual_review", 0),
        "audit_metadata": normalize_audit_metadata(result.get("audit_metadata")),
    }


def _finish_consistency_repair_audit_run(run_id: str | None, result: dict[str, Any]) -> None:
    if not run_id:
        return
    try:
        status = "failed" if result.get("status") == "error" else "success"
        loop_run_store.finish_run(
            run_id,
            status,
            result=_consistency_repair_audit_summary(result),
            error=result.get("error") if status == "failed" else None,
        )
    except Exception:
        logger.exception("Failed to finish consistency repair audit run")


def _is_skippable_repair_item(item: dict[str, Any]) -> bool:
    return item.get("status") == "missing" or item.get("reason") == "already_has_method"


def apply_consistency_history_repair(
    history_ids: list[int],
    session: Session | None = None,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    audit: bool = True,
    audit_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply selected method-fill repairs with dry-run and confirmation guards."""
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    run_id = _start_consistency_repair_audit_run(audit)
    audit_meta = normalize_audit_metadata(audit_metadata)

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        if run_id:
            result["run_id"] = run_id
        _finish_consistency_repair_audit_run(run_id, result)
        return result

    protected = not dry_run and not confirm

    try:
        preview = preview_consistency_history_repair(history_ids=history_ids, session=session)
        updated = 0
        skipped = 0
        manual_review = 0
        items: list[dict[str, Any]] = []

        for item in preview["items"]:
            payload = dict(item)
            can_update = bool(
                item.get("can_apply")
                and item.get("current_method") is None
                and item.get("inferred_method")
            )

            if can_update and dry_run:
                payload["action"] = "would_update"
            elif can_update and protected:
                skipped += 1
                payload["action"] = "confirmation_required"
            elif can_update:
                row = session.query(PredictionHistory).filter_by(id=int(item["history_id"])).first()
                if row is None:
                    skipped += 1
                    payload.update({
                        "status": "missing",
                        "can_apply": False,
                        "action": "skipped",
                        "reason": "history_row_not_found",
                    })
                elif row.prediction_method:
                    skipped += 1
                    payload.update({
                        "current_method": row.prediction_method,
                        "can_apply": False,
                        "action": "skipped",
                        "reason": "already_has_method",
                    })
                else:
                    row.prediction_method = str(item["inferred_method"])
                    updated += 1
                    payload.update({
                        "action": "updated",
                        "applied_method": row.prediction_method,
                    })
            elif _is_skippable_repair_item(item):
                skipped += 1
                payload["action"] = "skipped"
            else:
                manual_review += 1
                payload["action"] = "manual_review"

            items.append(payload)

        if not dry_run and confirm:
            session.commit()

        return finish({
            "status": "protected" if protected else "ok",
            "dry_run": dry_run,
            "confirm": confirm,
            "protected": protected,
            "requested": preview["requested"],
            "inferable": preview["inferable"],
            "updated": updated,
            "skipped": skipped,
            "manual_review": manual_review,
            "audit_metadata": audit_meta,
            "items": items,
        })
    except Exception as exc:
        if not dry_run and confirm:
            session.rollback()
        _finish_consistency_repair_audit_run(run_id, {
            "status": "error",
            "dry_run": dry_run,
            "confirm": confirm,
            "protected": protected,
            "requested": len(set(int(row_id) for row_id in history_ids)),
            "inferable": 0,
            "updated": 0,
            "skipped": 0,
            "manual_review": 0,
            "audit_metadata": audit_meta,
            "error": str(exc),
        })
        raise
    finally:
        if should_close:
            close_prediction_session(session)


def _recommendations(overall: dict[str, Any], by_engine: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []

    if int(overall["samples"]) < MIN_CALIBRATION_SAMPLES:
        recommendations.append({
            "level": "warn",
            "title": "样本不足",
            "message": f"当前只有 {overall['samples']} 条可评估预测，先继续回填完赛结果再做强校准。",
        })
        return recommendations

    bias = overall.get("confidence_bias")
    ece = overall.get("expected_calibration_error")
    if bias is not None and bias > 0.10:
        recommendations.append({
            "level": "warn",
            "title": "整体偏过度自信",
            "message": f"平均置信度高于实际准确率 {bias * 100:.1f} 个百分点，建议下调高置信度桶。",
        })
    elif bias is not None and bias < -0.10:
        recommendations.append({
            "level": "info",
            "title": "整体偏保守",
            "message": f"平均置信度低于实际准确率 {abs(bias) * 100:.1f} 个百分点，可适度上调可靠引擎置信度。",
        })

    if ece is not None and ece > 0.12:
        recommendations.append({
            "level": "warn",
            "title": "校准误差偏高",
            "message": f"ECE 为 {ece:.3f}，高置信度选择应优先使用校准后置信度。",
        })

    eligible = [
        (engine, stats)
        for engine, stats in by_engine.items()
        if int(stats["samples"]) >= MIN_CALIBRATION_SAMPLES and stats["avg_brier_score"] is not None
    ]
    if eligible:
        best_engine, best_stats = min(eligible, key=lambda item: item[1]["avg_brier_score"])
        recommendations.append({
            "level": "info",
            "title": "当前最佳概率引擎",
            "message": f"{best_engine} 的 Brier 均值最低（{best_stats['avg_brier_score']:.4f}），可作为集成权重基准。",
        })

    if not recommendations:
        recommendations.append({
            "level": "ok",
            "title": "校准状态正常",
            "message": "当前置信度与实际命中率没有明显背离，继续累计样本。",
        })
    return recommendations


def _integrated_weight_payload(default_elo_weight: float, by_engine: dict[str, dict[str, Any]]) -> dict[str, Any]:
    default_elo = max(MIN_INTEGRATED_WEIGHT, min(MAX_INTEGRATED_WEIGHT, float(default_elo_weight)))
    default_hybrid = 1.0 - default_elo
    elo_stats = by_engine.get("elo_odds", {})
    hybrid_stats = by_engine.get("hybrid", {})
    elo_samples = int(elo_stats.get("samples") or 0)
    hybrid_samples = int(hybrid_stats.get("samples") or 0)
    elo_brier = elo_stats.get("avg_brier_score")
    hybrid_brier = hybrid_stats.get("avg_brier_score")

    base_payload = {
        "elo_weight": round(default_elo, 3),
        "hybrid_weight": round(default_hybrid, 3),
        "source": "rule_default",
        "samples": {"elo_odds": elo_samples, "hybrid": hybrid_samples},
        "brier": {"elo_odds": elo_brier, "hybrid": hybrid_brier},
    }

    if (
        elo_samples < MIN_CALIBRATION_SAMPLES
        or hybrid_samples < MIN_CALIBRATION_SAMPLES
        or elo_brier is None
        or hybrid_brier is None
    ):
        base_payload["reason"] = "insufficient_component_samples"
        return base_payload

    elo_score = 1.0 / (float(elo_brier) + 0.05)
    hybrid_score = 1.0 / (float(hybrid_brier) + 0.05)
    learned_elo = elo_score / (elo_score + hybrid_score)
    blended_elo = default_elo * (1.0 - QUALITY_WEIGHT_BLEND) + learned_elo * QUALITY_WEIGHT_BLEND
    elo_weight = max(MIN_INTEGRATED_WEIGHT, min(MAX_INTEGRATED_WEIGHT, blended_elo))

    return {
        "elo_weight": round(elo_weight, 3),
        "hybrid_weight": round(1.0 - elo_weight, 3),
        "source": "historical_brier",
        "samples": {"elo_odds": elo_samples, "hybrid": hybrid_samples},
        "brier": {"elo_odds": elo_brier, "hybrid": hybrid_brier},
        "learned_elo_weight": round(learned_elo, 3),
        "blend": QUALITY_WEIGHT_BLEND,
    }


def build_quality_loop_report(session: Session | None = None) -> dict[str, Any]:
    """Build prediction quality and confidence calibration report."""
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        samples, counters = collect_quality_samples(session)
        by_engine = {
            engine: _summarize([sample for sample in samples if sample["engine"] == engine])
            for engine in ENGINE_NAMES
        }
        overall = _summarize(samples)
        trends = {
            "overall": _trend_points(samples),
            "by_engine": {
                engine: _trend_points([sample for sample in samples if sample["engine"] == engine])
                for engine in ENGINE_NAMES
            },
        }

        return {
            "status": "ok",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sample_policy": "latest applied pre-match prediction per engine per finished match",
            "counters": counters,
            "overall": overall,
            "by_engine": by_engine,
            "trends": trends,
            "consistency_issues": detect_prediction_consistency_issues(session),
            "integrated_weight_suggestion": _integrated_weight_payload(0.70, by_engine),
            "recommendations": _recommendations(overall, by_engine),
        }
    finally:
        if should_close:
            close_prediction_session(session)


def refresh_prediction_accuracy(session: Session) -> list[dict[str, Any]]:
    """Compute by-stage accuracy from MatchResult and upsert into PredictionAccuracy.

    Populates the PredictionAccuracy table (previously defined but never written)
    with aggregated metrics grouped by match stage.  Called from the accuracy-stats
    endpoint so the table stays in sync with live scoring data.

    Returns:
        List of dicts describing each stage row that was upserted.
    """
    from app.models.world_cup_prediction import (
        MatchFixture,
        MatchResult,
        PredictionAccuracy,
    )

    # Join MatchResult with MatchFixture to get stage info
    rows = (
        session.query(MatchResult, MatchFixture)
        .join(MatchFixture, MatchResult.match_id == MatchFixture.match_id)
        .filter(MatchResult.brier_score.isnot(None))
        .all()
    )
    if not rows:
        return []

    # Group by stage
    stages: dict[str, list[tuple]] = {}
    all_rows: list[tuple] = []
    for result, fixture in rows:
        stage = (fixture.stage or "unknown").lower()
        stages.setdefault(stage, []).append((result, fixture))
        all_rows.append((result, fixture))

    # Add "all" as a virtual stage
    stages["all"] = all_rows

    upserted: list[dict[str, Any]] = []
    for stage, stage_rows in stages.items():
        n = len(stage_rows)
        exact = sum(
            1 for r, _ in stage_rows
            if abs(r.home_error or 0) < 0.5 and abs(r.away_error or 0) < 0.5
        )
        goal_diff_correct = sum(
            1 for r, _ in stage_rows
            if abs((r.home_error or 0) - (r.away_error or 0)) < 0.5
        )
        outcome_correct = sum(1 for r, _ in stage_rows if r.outcome_correct == 1)
        mae_vals = [r.score_mae for r, _ in stage_rows if r.score_mae is not None]
        mae = sum(mae_vals) / len(mae_vals) if mae_vals else None

        # Upsert
        existing = session.query(PredictionAccuracy).filter_by(stage=stage).first()
        if existing:
            existing.matches_evaluated = n
            existing.exact_score_correct = exact
            existing.goal_diff_correct = goal_diff_correct
            existing.outcome_correct = outcome_correct
            existing.outcome_accuracy = round(outcome_correct / n, 4) if n else 0
            existing.score_mae = round(mae, 4) if mae else None
        else:
            row = PredictionAccuracy(
                stage=stage,
                matches_evaluated=n,
                exact_score_correct=exact,
                goal_diff_correct=goal_diff_correct,
                outcome_correct=outcome_correct,
                outcome_accuracy=round(outcome_correct / n, 4) if n else 0,
                score_mae=round(mae, 4) if mae else None,
            )
            session.add(row)

        upserted.append({
            "stage": stage,
            "matches": n,
            "exact": exact,
            "outcome_accuracy": round(outcome_correct / n, 3) if n else 0,
        })

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    return upserted


def calibrate_confidence_from_quality(
    raw_confidence: float,
    engine_name: str | None = None,
    session: Session | None = None,
) -> float:
    """Calibrate confidence from the quality loop buckets.

    NOTE: This is a simplified standalone utility that uses single-bucket
    nearest-neighbor blending.  The production calibration path goes through
    ``world_cup_confidence_calibration.apply_confidence_calibration`` which
    uses piecewise-linear interpolation across all reliable bucket centres
    and falls back to overall calibration when engine-specific data is sparse.
    """
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        report = build_quality_loop_report(session=session)
        stats = report["by_engine"].get(engine_name) if engine_name else report["overall"]
        if not stats or not stats.get("is_calibratable"):
            return round(raw_confidence, 3)

        _lower, _upper, label = _confidence_bucket(raw_confidence)
        buckets = stats["calibration_buckets"]
        bucket = next((item for item in buckets if item["label"] == label), None)
        if not bucket or not bucket.get("is_usable") or bucket.get("accuracy") is None:
            usable = [
                item for item in buckets
                if item.get("is_usable") and item.get("accuracy") is not None
            ]
            if not usable:
                return round(raw_confidence, 3)
            bucket = min(
                usable,
                key=lambda item: abs(((item["lower"] + item["upper"]) / 2.0) - raw_confidence),
            )

        # Blend: weight the empirical accuracy heavily (70%) with a 30% raw
        # anchor to avoid over-correction on small samples, matching the
        # piecewise-linear calibration blend in world_cup_confidence_calibration.
        calibrated = _CALIBRATION_BLEND * float(bucket["accuracy"]) + (1 - _CALIBRATION_BLEND) * raw_confidence
        return round(max(0.05, min(0.99, calibrated)), 3)
    finally:
        if should_close:
            close_prediction_session(session)


def suggest_integrated_engine_weights(
    default_elo_weight: float,
    session: Session | None = None,
) -> dict[str, Any]:
    """Suggest integrated engine weights from historical Brier performance.

    The returned weight is deliberately conservative: historical Brier only
    nudges the existing rule-based weight, and only when both component engines
    have enough samples.
    """
    default_elo = max(MIN_INTEGRATED_WEIGHT, min(MAX_INTEGRATED_WEIGHT, float(default_elo_weight)))

    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        report = build_quality_loop_report(session=session)
        return _integrated_weight_payload(default_elo, report["by_engine"])
    finally:
        if should_close:
            close_prediction_session(session)
