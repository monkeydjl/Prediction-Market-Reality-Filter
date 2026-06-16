"""
auto_resolve_service.py
=======================
自动将已解决的 Polymarket 市场与本地预测记录匹配，
同步更新 agent_memory.json 和 analysis_audit.jsonl，
确保 calibration_service 和 ReputationEngine 都有数据可读。

Question matching lives in app.utils.text_match (shared with the event-layer
auto-resolve); this module owns the market-layer resolution workflow only.
"""

from typing import Any

from app.memory.agent_memory import resolve_prediction
from app.services.analysis_audit_service import resolve_by_question
from app.services.polymarket_history_service import fetch_resolved_markets
from app.utils.text_match import build_index, find_match


async def run_auto_resolve(resolved_limit: int = 200) -> dict[str, Any]:
    """
    从 Polymarket 拉取已解决市场，与本地预测匹配，写入实际结果。
    同时更新 agent_memory.json（供 calibration_service）
    和 analysis_audit.jsonl（供 ReputationEngine）。
    """
    resolved_markets = await fetch_resolved_markets(limit=resolved_limit)
    if not resolved_markets:
        return {"status": "no_resolved_markets", "resolved_count": 0, "checked_count": 0}

    from app.memory.agent_memory import load_memory
    memory = load_memory()
    unresolved = [e for e in memory if not e.get("resolved")]

    if not unresolved:
        return {"status": "no_unresolved_predictions", "resolved_count": 0,
                "checked_count": len(resolved_markets)}

    resolved_index = build_index(resolved_markets)
    resolved_count = 0
    match_log = []

    for prediction in unresolved:
        q = prediction.get("market_question", "")
        match = find_match(q, resolved_index)
        if match is None:
            continue

        matched_question, actual_outcome, score = match

        # 更新 agent_memory.json
        resolve_prediction(market_question=q, actual_outcome=actual_outcome)

        # 更新 analysis_audit.jsonl（ReputationEngine 的数据源）
        resolve_by_question(market_question=q, actual_outcome=actual_outcome)

        resolved_count += 1
        match_log.append({
            "prediction": q[:80],
            "matched_to": matched_question[:80],
            "actual_outcome": actual_outcome,
            "match_score": round(score, 3),
        })

    return {
        "status": "ok",
        "resolved_count": resolved_count,
        "checked_count": len(resolved_markets),
        "unresolved_predictions": len(unresolved),
        "matches": match_log,
    }
