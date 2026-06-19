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
    """A short, human-readable why behind the act/watch/skip verdict, from the
    frozen diagnosis inputs. Explains the gating factor a reviewer most needs:
    dormancy, weak skill, or a liquidity discount."""
    decision = prediction.get("decision")
    qualified = prediction.get("qualified")
    segment_n = prediction.get("segment_n")
    liq_factor = prediction.get("liquidity_factor")
    if decision == "act":
        return "已合格类别 + 调整后 edge 达到行动阈值"
    # watch / skip: name the dominant reason it is not act.
    if qualified is False:
        return f"类别样本不足（{segment_n or 0} 条，未达合格线），暂不行动"
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

    return {
        "event_id": prediction.get("event_id"),
        "event": {
            "title": record.get("event_title", ""),
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
            # decision = the Decision Gate verdict (act / watch / skip).
            # action = the event record's human-review action (escalate/track/watch).
            "decision": prediction.get("decision"),
            "action": report.get("recommended_action", ""),
        },
        "risk": {
            "level": risk.get("level"),
            "flags": risk.get("flags", []),
        },
        "category": prediction.get("base_rate_category"),
        "status": prediction.get("status"),
    }
