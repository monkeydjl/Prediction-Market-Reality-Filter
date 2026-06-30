"""AI optimization service for improving predictions."""

import asyncio
import logging
from typing import Any
from app.core.config import settings

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
    match_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Use AI to optimize a match prediction by considering additional factors.

    Args:
        home_team: Home team name
        away_team: Away team name
        current_prediction: Current prediction data
        prediction_method: Which engine was used
        match_context: Optional context (injuries, form, tactics, etc.)

    Returns:
        Optimized prediction with reasoning
    """

    # Check if AI is available
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "sk-your-key-here":
        return {
            "status": "unavailable",
            "message": "AI优化功能需要配置OPENAI_API_KEY"
        }

    # Create a dedicated client with correct base_url but no SDK-level retries
    # (we handle retries manually to control backoff timing for 429 errors)
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.DASHSCOPE_BASE_URL,
        timeout=60.0,
        max_retries=0,
    )

    # Extract current prediction data
    predicted_score = current_prediction["predicted_score"]
    outcome_probs = current_prediction["outcome_probabilities"]
    confidence = current_prediction["confidence"]
    elo_ratings = current_prediction.get("elo_ratings")

    # Build context
    context_info = ""
    if match_context:
        context_info = f"\n\n**额外上下文**:\n"
        if match_context.get("injuries"):
            context_info += f"- 伤停情况: {match_context['injuries']}\n"
        if match_context.get("recent_form"):
            context_info += f"- 近期状态: {match_context['recent_form']}\n"
        if match_context.get("head_to_head"):
            context_info += f"- 历史交锋: {match_context['head_to_head']}\n"

    elo_info = ""
    if elo_ratings:
        elo_info = f"\n- Elo评分: 主队 {elo_ratings['home']:.0f}, 客队 {elo_ratings['away']:.0f}, 差值 {elo_ratings['difference']:.1f}"

    prompt = f"""优化世界杯预测 {home_team} vs {away_team}

当前: 比分{predicted_score['home']:.1f}-{predicted_score['away']:.1f}, 主胜{outcome_probs['home_win']*100:.0f}% 平{outcome_probs['draw']*100:.0f}% 客胜{outcome_probs['away_win']*100:.0f}%, 置信{confidence*100:.0f}%{elo_info}

识别2个盲点和2个校准问题，给出优化后预测。限200字。

JSON格式:
{{
  "blind_spots": ["盲点1", "盲点2"],
  "calibration_issues": ["问题1", "问题2"],
  "optimized_prediction": {{
    "predicted_score": {{"home": 2.0, "away": 1.0}},
    "outcome_probabilities": {{"home_win": 0.55, "draw": 0.25, "away_win": 0.20}},
    "confidence": 0.68,
    "reasoning": "简短理由"
  }}
}}"""

    messages = [
        {
            "role": "system",
            "content": "足球预测优化专家，简洁回答，只返回JSON。"
        },
        {"role": "user", "content": prompt}
    ]

    # Retry logic with exponential backoff for 429 rate limit errors
    max_retries = 5
    base_delay = 5.0  # Start with 5 seconds (longer backoff for persistent rate limits)

    async with _get_ai_semaphore():
        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=settings.OPENAI_MODEL or "gpt-3.5-turbo",
                    messages=messages,
                    temperature=0.5,
                    max_tokens=400,
                    response_format={"type": "json_object"},
                )

                result_text = response.choices[0].message.content

                if not result_text:
                    return {
                        "status": "error",
                        "message": "AI未返回优化结果"
                    }

                # Parse JSON response
                import json
                try:
                    optimization = json.loads(result_text)
                    return {
                        "status": "ok",
                        "optimization": optimization
                    }
                except json.JSONDecodeError:
                    # Fallback to text response
                    return {
                        "status": "ok",
                        "optimization": {
                            "blind_spots": [],
                            "calibration_issues": [],
                            "optimized_prediction": None,
                            "raw_text": result_text
                        }
                    }

            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "rate_limit" in error_str.lower()

                if is_rate_limit and attempt < max_retries:
                    # Exponential backoff: 2s, 4s, 8s, 16s
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "AI optimization rate limited (attempt %d/%d), retrying in %.1fs",
                        attempt + 1, max_retries, delay
                    )
                    await asyncio.sleep(delay)
                    continue

                if is_rate_limit:
                    return {
                        "status": "error",
                        "message": "AI请求频率超限，请等待1-2分钟后重试"
                    }

                # Non-rate-limit errors: no retry
                logger.error("[AI Optimization Error] %s", error_str, exc_info=True)
                return {
                    "status": "error",
                    "message": f"AI优化失败: {error_str[:200]}"
                }

    return {"status": "error", "message": "AI优化失败：未知错误"}
