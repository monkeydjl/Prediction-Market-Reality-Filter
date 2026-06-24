"""AI optimization service for improving predictions."""

from typing import Any
from app.core.config import settings


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

    try:
        from openai import AsyncOpenAI
        import httpx

        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=httpx.Timeout(30.0, connect=10.0)
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

        prompt = f"""作为足球预测优化专家，请对以下世界杯比赛预测进行优化分析：

**比赛**: {home_team} vs {away_team}

**当前预测** (引擎: {prediction_method}):
- 预测比分: {predicted_score['home']:.1f} - {predicted_score['away']:.1f}
- 主队胜率: {outcome_probs['home_win']*100:.1f}%
- 平局概率: {outcome_probs['draw']*100:.1f}%
- 客队胜率: {outcome_probs['away_win']*100:.1f}%
- 置信度: {confidence*100:.1f}%{elo_info}{context_info}

请基于以下维度优化预测：

1. **数据盲点识别** - 当前预测可能忽略了哪些关键因素？
   - 战术克制关系
   - 球员伤停/轮换影响
   - 心理因素（压力、动机）
   - 天气、场地条件
   - 赛程密度/疲劳

2. **概率校准建议** - 基于世界杯历史数据，概率分布是否合理？
   - 强队vs弱队的实际冷门率
   - 世界杯小组赛的平局比例
   - 是否过度自信或过度保守

3. **优化后的预测** - 给出调整后的数据：
   - 优化后比分预测
   - 优化后胜平负概率
   - 优化后置信度
   - 关键调整理由

要求：
- 使用中文回答
- 基于足球专业知识和世界杯特点
- 优化要有据可依，不要臆测
- 以JSON格式返回优化结果
- 总长度控制在400字以内

返回格式：
{{
  "blind_spots": ["盲点1", "盲点2"],
  "calibration_issues": ["问题1", "问题2"],
  "optimized_prediction": {{
    "predicted_score": {{"home": 2.0, "away": 1.0}},
    "outcome_probabilities": {{"home_win": 0.55, "draw": 0.25, "away_win": 0.20}},
    "confidence": 0.68,
    "reasoning": "调整理由"
  }}
}}"""

        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL or "gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "你是一位足球数据科学家，擅长识别预测模型的盲点并提出优化建议。你的优化必须基于数据和足球专业知识。"
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1000,
            response_format={"type": "json_object"},
            timeout=25.0
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

    except TimeoutError:
        return {
            "status": "error",
            "message": "AI优化服务超时(30秒)，请稍后重试"
        }
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[AI Optimization Error] {error_detail}")
        return {
            "status": "error",
            "message": f"AI优化失败: {str(e)}"
        }
