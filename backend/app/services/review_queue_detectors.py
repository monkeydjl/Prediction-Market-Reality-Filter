"""Review queue trigger detectors (Plan 4 §6.2).

Pure functions that scan a single event record and return review-queue
candidate dicts. No I/O, no LLM, no settings reads — the orchestrator
calls ``detect_review_candidates`` and decides whether to enqueue.

Each candidate is a dict with keys:
    trigger   — one of the locked trigger type strings
    severity  — "WARN" or "ERROR"
    reason    — Chinese reason string (vocabulary-locked)
    context   — dict of relevant field values for the reviewer

Trigger types (locked):
    high_value_downgraded        — a committed YES/NO call but final direction is
                                   WAIT/AVOID
    source_market_conflict       — source_reliability says WAIT but market_quality
                                   does not (strong cross-overlay conflict)
    outcome_prediction_mismatch  — resolved outcome contradicts a high-confidence
                                   prediction
    auto_resolve_low_confidence  — auto-resolved event with match confidence
                                   below threshold (real-time hook in
                                   resolve_with_calibration; only trigger
                                   with its own public wrapper
                                   detect_auto_resolve_low_confidence)
    audit_inconsistency          — batch audit CLI only
                                   (audit_quality_consistency --enqueue).
                                   No _detect_* function here; conflicts come
                                   from audit_quality_consistency checks and
                                   are enqueued directly via store.
    conclusion_challenge_failed  - conclusion challenge gate rejected or could
                                   not validate a strong conclusion. Enqueued
                                   by event/world-cup orchestrators; no
                                   _detect_* function here.

Field-shape contract (Q7)
-------------------------
A detector may only read fields the record actually carries. Two of them read
fields nothing writes and were therefore unconditionally dead — including the
queue's *only* ERROR-severity trigger:

- ``actionable_recommendation`` is produced by exactly one function,
  ``event_intelligence_service._build_actionable_recommendation``, and is typed
  as ``models.event.ActionableRecommendation`` (which declares no ``extra``, so
  Pydantic drops unknown keys on any typed round-trip). Its seven keys are
  direction / confidence / suggested_allocation_pct / edge / risk_level /
  rationale / calibration_status. ``signal`` and ``ai_probability`` are not
  among them — measured 0 of 235 records in the live store.
- ``record["outcome"]`` is a ``models.event.Outcome`` **dict**
  (status / actual_outcome / confidence / resolved_at / source / notes), never
  the strings ``"YES"``/``"NO"``. ``event_store.resolve_event`` is its only
  writer and validates through ``EventRecord``.

So the mapping used here:

- "an act signal" (Decision Gate / prediction vocabulary, which never appears on
  an event record) -> ``actionable_recommendation.direction in {YES, NO}``, i.e.
  a committed call as opposed to WAIT/AVOID. Note ``confidence`` is the *string*
  ``high``/``medium``/``low`` and cannot serve as a numeric gate.
- the numeric confidence -> ``record["probability"]["estimated"]``, which is
  **0-100** (present on 235 of 235 live records), stance-adjusted the way
  ``domain_reliability_service`` grades a refuting source: a NO call at
  P(YES)=19 is a 0.81-conviction call, not a 0.19-conviction one.

Do NOT reach into ``legacy_analysis.signal`` for the "high value" gate. It is
the layer this module sits above (``event_intelligence_service`` states the event
layer does not read it for its own logic), its vocabulary is
LONG/SHORT/WATCHLIST/STRONG_* rather than act/provisional_act anyway, and
``review_queue_store._check_vocabulary`` **rejects** the words long/short — so a
reason string naming a legacy signal would raise on enqueue and the item would
be silently dropped by the orchestrator's best-effort try/except.
"""
from __future__ import annotations

import math
from typing import Any

# A committed call, as opposed to an abstention. The event-layer equivalent of
# the Decision Gate's act / provisional_act.
_CALLED_DIRECTIONS = frozenset({"YES", "NO"})
_WAIT_LIKE = frozenset({"WAIT", "AVOID"})


def detect_review_candidates(
    record: dict[str, Any],
    *,
    mismatch_confidence_threshold: float = 0.75,
    auto_resolve_confidence_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """Scan a record and return review-queue candidate dicts.

    Pure, synchronous, deterministic. Returns an empty list when no
    detector fires. Does not crash on missing fields.

    ``mismatch_confidence_threshold`` is the orchestrator-configurable
    cutoff for the ``outcome_prediction_mismatch`` detector. It is a **0-1**
    conviction, compared against the stance-adjusted
    ``probability.estimated`` / 100 — matching its 0.75 default and its sibling
    ``auto_resolve_confidence_threshold``, both of which are fractions. Comparing
    the raw 0-100 estimate against 0.75 instead would fire on every wrong call at
    ERROR severity: 11 of 11 on the live 235-event store, versus 0 at the
    intended 0.75 and 6 at 0.5. The orchestrator should pass
    ``settings.REVIEW_QUEUE_MISMATCH_CONFIDENCE`` so the env var takes
    effect.

    ``auto_resolve_confidence_threshold`` controls the
    ``auto_resolve_low_confidence`` detector. It defaults to 0.95, which
    catches fuzzy verified auto-resolves that barely pass the identity gate.
    """
    if not isinstance(record, dict):
        return []
    candidates: list[dict[str, Any]] = []
    candidates.extend(_detect_high_value_downgraded(record))
    candidates.extend(_detect_source_market_conflict(record))
    candidates.extend(_detect_outcome_prediction_mismatch(
        record, confidence_threshold=mismatch_confidence_threshold,
    ))
    candidates.extend(_detect_auto_resolve_low_confidence(
        record, confidence_threshold=auto_resolve_confidence_threshold,
    ))
    return candidates


def detect_auto_resolve_low_confidence(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """Public wrapper for the auto-resolve low-confidence detector."""
    return _detect_auto_resolve_low_confidence(
        record, confidence_threshold=confidence_threshold,
    )


def _detect_high_value_downgraded(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag a committed YES/NO call that the overlay merge downgraded away.

    Gates on ``actionable_recommendation.direction``. It used to gate on
    ``rec.get("signal") in {"act", "provisional_act"}`` — a key the event record
    has never carried under any flag combination (see the field-shape contract
    above), so this detector could not fire. The second gate,
    ``final_displayed_direction``, is live: ``event_intelligence_service`` sets it
    whenever at least one quality overlay applies.
    """
    rec = record.get("actionable_recommendation")
    if not isinstance(rec, dict):
        return []
    direction = rec.get("direction")
    if direction not in _CALLED_DIRECTIONS:
        return []
    final_dir = record.get("final_displayed_direction")
    if final_dir not in _WAIT_LIKE:
        return []
    reason = record.get("final_downgrade_reason") or ""
    return [{
        "trigger": "high_value_downgraded",
        "severity": "WARN",
        "reason": f"高价值判断 {direction} 被降级为 {final_dir}"
                  + (f"：{reason}" if reason else ""),
        "context": {
            "raw_direction": direction,
            "recommendation_confidence": rec.get("confidence"),
            "final_direction": final_dir,
            "downgrade_reason": reason,
            "estimated_probability": _estimated_probability(record),
        },
    }]


def _detect_source_market_conflict(record: dict[str, Any]) -> list[dict[str, Any]]:
    sr = record.get("source_reliability")
    mq = record.get("market_quality")
    if not isinstance(sr, dict) or not isinstance(mq, dict):
        return []
    sr_says_wait = sr.get("suggested_direction") in _WAIT_LIKE and sr.get("downgraded") is True
    mq_says_ok = mq.get("downgraded") is not True
    if not (sr_says_wait and mq_says_ok):
        return []
    return [{
        "trigger": "source_market_conflict",
        "severity": "WARN",
        "reason": "来源可靠性与市场质量强冲突：来源建议 WAIT 但市场质量未降级",
        "context": {
            "sr_suggested_direction": sr.get("suggested_direction"),
            "sr_downgrade_reason": sr.get("downgrade_reason"),
            "mq_suggested_direction": mq.get("suggested_direction"),
        },
    }]


def _estimated_probability(record: dict[str, Any]) -> float | None:
    """``record["probability"]["estimated"]`` as a 0-100 float, or None.

    This is the record's *latest* estimate: every re-scan rewrites it, so it is
    fit for an operator alert ("does a human need to look at this now?") but not
    for grading. Calibration must use the frozen estimate from the ledger
    instead — see ``domain_reliability_service.attribute_record``, which takes
    ``committed_probability`` as a parameter and deliberately has no fallback to
    the latest value.
    """
    prob_block = record.get("probability")
    if not isinstance(prob_block, dict):
        return None
    value = prob_block.get("estimated")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _conviction(record: dict[str, Any], direction: str) -> float | None:
    """How sure the record was of the direction it called, on a 0-1 scale.

    ``estimated`` is P(YES) on 0-100; the conviction behind a NO call is the
    complement. Same stance adjustment ``domain_reliability_service`` applies
    when it grades a refuting domain on ``100 - committed``.

    The scale conversion is unconditional (``/100``) rather than
    ``calibration_drift_service._normalize_prob``'s ``if value > 1.0`` heuristic:
    this field's scale is fixed by its sole producer, so the heuristic could only
    add a way to misread a genuine 0.9% estimate as 90%.
    """
    estimated = _estimated_probability(record)
    if estimated is None:
        return None
    p_yes = max(0.0, min(1.0, estimated / 100.0))
    return p_yes if direction == "YES" else 1.0 - p_yes


def _detect_outcome_prediction_mismatch(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    """Flag a resolved event that contradicts a confident committed call.

    The queue's only ERROR-severity trigger, and it was dead twice over: it read
    ``actionable_recommendation.ai_probability`` (never written) and compared
    ``record["outcome"]`` — an ``Outcome`` dict — against the strings
    ``"YES"``/``"NO"``. Both gates now read the shapes production writes, in the
    order ``domain_reliability_service._extract`` established: resolved status,
    committed direction, then a usable ``actual_outcome``.

    ``actual_outcome`` is 0-100 with 0=NO, so ``> 0`` means "resolved YES-ward"
    (the live store holds only 0.0 and 100.0, but the model allows partials and
    a partial YES still contradicts a NO call).
    """
    outcome = record.get("outcome")
    if not isinstance(outcome, dict):
        return []
    if outcome.get("status") != "resolved":
        return []
    actual = outcome.get("actual_outcome")
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return []
    if not math.isfinite(actual) or actual < 0:
        return []
    rec = record.get("actionable_recommendation")
    if not isinstance(rec, dict):
        return []
    direction = rec.get("direction")
    if direction not in _CALLED_DIRECTIONS:
        return []
    resolved_yes = actual > 0
    if resolved_yes == (direction == "YES"):
        return []
    conviction = _conviction(record, direction)
    if conviction is None or conviction < confidence_threshold:
        return []
    return [{
        "trigger": "outcome_prediction_mismatch",
        "severity": "ERROR",
        "reason": (
            f"结算结果 {'YES' if resolved_yes else 'NO'} 与预测方向 {direction} "
            f"相反，且判断自信度 {conviction:.2f} 不低于阈值 "
            f"{confidence_threshold:.2f}"
        ),
        "context": {
            "outcome_status": "resolved",
            "actual_outcome": float(actual),
            "predicted_direction": direction,
            "estimated_probability": _estimated_probability(record),
            "conviction": round(conviction, 4),
            "confidence_threshold": confidence_threshold,
        },
    }]


def _detect_auto_resolve_low_confidence(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """Flag auto-resolved events whose match confidence is below threshold."""
    outcome = record.get("outcome")
    if not isinstance(outcome, dict):
        return []
    if outcome.get("status") != "resolved":
        return []
    if outcome.get("source") != "auto_market":
        return []
    confidence = outcome.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return []
    if confidence >= confidence_threshold:
        return []
    return [{
        "trigger": "auto_resolve_low_confidence",
        "severity": "WARN",
        "reason": (
            f"自动结算置信度 {confidence:.2f} "
            f"低于阈值 {confidence_threshold:.2f}"
        ),
        "context": {
            "outcome_source": "auto_market",
            "outcome_confidence": confidence,
            "confidence_threshold": confidence_threshold,
            "actual_outcome": outcome.get("actual_outcome"),
        },
    }]
