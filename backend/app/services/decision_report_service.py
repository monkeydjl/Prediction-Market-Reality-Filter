"""decision_report_service.py
=========================
Assemble a human-reviewable decision report from a committed prediction and its
event intelligence record - the loop's final, decision-centric output
(V2_REFACTOR_PLAN "Report Engine").

Pure assembly: a prediction dict (from prediction_store) + the event record dict
(from event_store) in, one report dict out. No store imports, no I/O - trivially
testable. Event vocabulary only (see event-conventions): the report surfaces the
loop's own decision-gate terms (decision act/watch/skip, adjusted_edge, trust) and
the record's human-review recommended_action; it never emits trading language.
"""

from typing import Any


def _diagnosis_reason(prediction: dict[str, Any]) -> str:
    """A short, human-readable why behind the act/provisional_act/watch/skip
    verdict, from the frozen diagnosis inputs. Explains the gating factor a
    reviewer most needs: dormancy, weak skill, or a liquidity discount."""
    decision = prediction.get("decision")
    qualified = prediction.get("qualified")
    segment_n = prediction.get("segment_n")
    segment_min = prediction.get("segment_min_samples")
    liq_factor = prediction.get("liquidity_factor")
    if decision == "act":
        return "已合格类别 + 调整后 edge 达到行动阈值"
    if decision == "provisional_act":
        # Dormant but edge large: uncalibrated provisional action.
        suffix = f"/{segment_min}" if segment_min else ""
        return f"未经校准的临时行动建议（类别样本 {segment_n or 0}{suffix}，edge 达标但未合格）"
    # watch / skip: name the dominant reason it is not act.
    if qualified is False:
        suffix = f"/{segment_min}" if segment_min else ""
        return f"类别样本不足（{segment_n or 0}{suffix} 条，未达合格线），暂不行动"
    if liq_factor is not None and liq_factor < 1.0:
        return f"流动性折损（factor {liq_factor}），调整后 edge 被压低"
    return "调整后 edge 未达行动阈值"


def build_decision_report(
    prediction: dict[str, Any],
    record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Join a committed prediction with its event record into a decision report.

    `record` is the EventRecord dict (event_store entry["record"]). When it is
    None (a prediction whose event is no longer stored), a minimal report is
    built from the prediction alone - the edge/decision are still meaningful.
    """
    record = record or {}
    probability = record.get("probability") or {}
    credibility = record.get("credibility") or {}
    risk = record.get("risk") or {}
    report = record.get("intelligence_report") or {}
    decision = prediction.get("decision")
    qualified = prediction.get("qualified")
    # calibration_status: calibrated when qualified (segment has enough samples),
    # uncalibrated_provisional otherwise (dormant or provisional_act).
    calibration_status = "calibrated" if qualified else "uncalibrated_provisional"
    actionable = record.get("actionable_recommendation")
    # The helper in event_intelligence_service defaults the inner
    # calibration_status to uncalibrated_provisional (it lacks segment stats).
    # Override here where we DO have the prediction's qualified flag, so the
    # inner field matches the outer recommendation.calibration_status.
    if actionable is not None:
        actionable = {**actionable, "calibration_status": calibration_status}

    return {
        "event_id": prediction.get("event_id"),
        "event": {
            "title": record.get("event_title", ""),
            "title_zh": record.get("event_title_zh", ""),
            "summary": record.get("event_summary", ""),
        },
        "probability": {
            "estimated": probability.get("estimated"),
            "baseline": probability.get("baseline"),
            "change": probability.get("change"),
            "direction": probability.get("direction"),
        },
        "market_view": {
            # Frozen at commit on the prediction; the live record baseline may differ.
            "market_probability": prediction.get("market_probability"),
            "platform": prediction.get("platform", ""),
            "liquidity": prediction.get("liquidity"),
            "volume": prediction.get("volume"),
        },
        "edge": {
            "raw": prediction.get("raw_edge"),
            "adjusted": prediction.get("adjusted_edge"),
            "trust": prediction.get("trust"),
        },
        # Why this verdict, frozen at decision time (not recomputed): lets a
        # reviewer see whether it is dormancy, weak skill, or a liquidity discount.
        "diagnosis": {
            "qualified": prediction.get("qualified"),
            "segment_n": prediction.get("segment_n"),
            "segment_min_samples": prediction.get("segment_min_samples"),
            "segment_skill": prediction.get("segment_skill"),
            "liquidity_factor": prediction.get("liquidity_factor"),
            "reason": _diagnosis_reason(prediction),
        },
        "confidence": {
            "level": credibility.get("level"),
            "score": credibility.get("score"),
            "confidence": credibility.get("confidence"),
        },
        "recommendation": {
            # decision = the Decision Gate verdict (act / provisional_act / watch / skip).
            # action = the event record's human-review action (escalate/track/watch).
            # calibration_status = calibrated (qualified segment) vs uncalibrated_provisional.
            "decision": prediction.get("decision"),
            "action": report.get("recommended_action", ""),
            "calibration_status": calibration_status,
        },
        "risk": {
            "level": risk.get("level"),
            "flags": risk.get("flags", []),
        },
        "category": prediction.get("base_rate_category"),
        "status": prediction.get("status"),
        "actionable_recommendation": actionable,
        # Phase 1: pass through decision_quality overlay (audit/explanation
        # layer). None when DECISION_QUALITY_ENABLED=false or build failed
        # and no fallback was attached. Downstream consumers read
        # displayed_direction / downgrade_reason / decision_rationale_zh.
        "decision_quality": record.get("decision_quality"),
        # Phase 2: pass through market_quality overlay + the merged
        # final_displayed_direction / final_downgrade_reason (the
        # user-facing fields, computed by merge_quality_overlays at
        # analyze_event time). market_quality is None for non-prediction-
        # market sources or when MARKET_QUALITY_ENABLED=false; the merged
        # final_* fields are None when both overlays are off (byte-identical
        # to pre-Phase-2 records).
        "market_quality": record.get("market_quality"),
        "final_displayed_direction": record.get("final_displayed_direction"),
        "final_downgrade_reason": record.get("final_downgrade_reason"),
    }
