import re
from typing import Any

from app.services.llm_gateway_service import complete_json


def _extract_float(pattern: str, text: str, fallback: float = 0.0) -> float:
    match = re.search(pattern, text)
    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            return fallback
    return fallback


async def ask_llm(prompt: str) -> dict[str, Any]:
    """
    Legacy helper used by older agents (contrarian, crowd, etc.).
    Uses the unified LLM Gateway with JSON mode.
    """
    system = (
        "You are a professional prediction market analyst. "
        "Return only valid JSON with keys: "
        "true_probability (0-100), confidence (0-1), "
        "narrative_type (string), reasoning (string ≤120 words)."
    )

    try:
        result = await complete_json(
            task="probability_analysis",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        if not result.ok or result.json_data is None:
            raise RuntimeError(result.degraded_reason or "LLM unavailable")
        data = result.json_data

        probability = float(data.get("true_probability", 50))
        confidence = float(data.get("confidence", 0.5))
        narrative_type = str(data.get("narrative_type", "unknown"))
        reasoning = str(data.get("reasoning", ""))

        return {
            "probability": max(0.0, min(100.0, probability)),
            "confidence": max(0.0, min(1.0, confidence)),
            "narrative_type": narrative_type,
            "reasoning": reasoning,
        }

    except Exception as exc:
        return {
            "probability": 50.0,
            "confidence": 0.0,
            "narrative_type": "API_ERROR",
            "reasoning": str(exc),
        }
