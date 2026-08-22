"""AI optimization service for improving World Cup predictions."""

import asyncio
import logging
from typing import Any

from app.services.llm_fact_grounding import build_fact_grounding_section
from app.services.llm_gateway_service import complete_json, has_configured_llm_route

logger = logging.getLogger(__name__)

# Global semaphore to limit concurrent AI calls across the entire process
_ai_semaphore: asyncio.Semaphore | None = None


def _get_ai_semaphore() -> asyncio.Semaphore:
    """Get or create the global AI call semaphore (max 3 concurrent)."""
    global _ai_semaphore
    if _ai_semaphore is None:
        _ai_semaphore = asyncio.Semaphore(3)
    return _ai_semaphore


async def optimize_prediction_with_ai(
    home_team: str,
    away_team: str,
    current_prediction: dict[str, Any],
    prediction_method: str,
    match_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use the LLM Gateway to optimize a match prediction.

    The Gateway handles provider/model ordering and fallback for World Cup
    tasks, including LLM_ROUTE_WORLD_CUP, LLM_ROUTE_DEFAULT, numbered
    OPENAI_API_KEY_N providers, and legacy OPENAI_* config.
    """

    if not has_configured_llm_route("world_cup"):
        return {
            "status": "unavailable",
            "message": "AI\u4f18\u5316\u529f\u80fd\u9700\u8981\u914d\u7f6e\u81f3\u5c11\u4e00\u4e2a\u53ef\u7528\u7684 LLM Gateway \u8def\u7531",
        }

    predicted_score = current_prediction["predicted_score"]
    outcome_probs = current_prediction["outcome_probabilities"]
    confidence = current_prediction["confidence"]
    elo_ratings = current_prediction.get("elo_ratings")

    # `match_context` used to be read for three keys only - injuries,
    # recent_form, head_to_head - which no caller has ever passed, while the
    # five keys the /optimize route does pass (stage, group, venue,
    # data_quality, key_factors) were dropped on the floor. Forward the whole
    # mapping instead: the grounding section renders whatever is present and
    # names the rest as facts the model does not hold.
    facts: dict[str, Any] = dict(match_context or {})
    facts["elo_ratings"] = elo_ratings
    grounding = build_fact_grounding_section(facts)

    prompt = f"""Optimize this World Cup prediction for {home_team} vs {away_team}.

Current prediction:
- Score: {predicted_score['home']:.1f}-{predicted_score['away']:.1f}
- Home win: {outcome_probs['home_win']*100:.0f}%
- Draw: {outcome_probs['draw']*100:.0f}%
- Away win: {outcome_probs['away_win']*100:.0f}%
- Confidence: {confidence*100:.0f}%
- Engine: {prediction_method}

{grounding}

Identify at most 2 blind spots and at most 2 calibration issues that follow from
the facts above, then provide an optimized prediction. Return a shorter list, or
an empty one, rather than naming a blind spot you cannot ground in those facts.
Return concise Chinese reasoning where text is needed.

Return ONLY this JSON object:
{{
  "blind_spots": ["blind spot 1", "blind spot 2"],
  "calibration_issues": ["issue 1", "issue 2"],
  "optimized_prediction": {{
    "predicted_score": {{"home": 2.0, "away": 1.0}},
    "outcome_probabilities": {{"home_win": 0.55, "draw": 0.25, "away_win": 0.20}},
    "confidence": 0.68,
    "reasoning": "short reason"
  }}
}}"""

    messages = [
        {
            "role": "system",
            "content": (
                "You are a football prediction optimization expert. Return only "
                "valid JSON. Ground every blind spot in the facts you are given "
                "and never assert a statistic that was not provided."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        async with _get_ai_semaphore():
            result = await complete_json(
                task="world_cup",
                messages=messages,
                temperature=0.5,
                max_tokens=400,
            )

        if result.ok and result.json_data is not None:
            return {
                "status": "ok",
                "optimization": result.json_data,
            }

        logger.warning(
            "AI optimization gateway failed for %s vs %s: %s",
            home_team,
            away_team,
            result.degraded_reason,
        )
        return {
            "status": "error",
            "message": f"AI\u4f18\u5316\u5931\u8d25: {result.degraded_reason or 'all_routes_failed'}",
        }
    except Exception as exc:
        error_text = str(exc)
        logger.error("[AI Optimization Error] %s", error_text, exc_info=True)
        return {
            "status": "error",
            "message": f"AI\u4f18\u5316\u5931\u8d25: {error_text[:200]}",
        }
