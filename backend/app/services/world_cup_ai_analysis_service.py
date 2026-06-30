"""AI analysis service for World Cup predictions."""

from typing import Any

from app.core.config import settings


async def analyze_prediction_with_ai(
    home_team: str,
    away_team: str,
    predicted_score: dict[str, float],
    outcome_probabilities: dict[str, float],
    confidence: float,
    prediction_method: str,
    elo_ratings: dict[str, float] | None = None,
    key_factors: list[str] | None = None
) -> str:
    """Use AI to analyze a match prediction and provide insights.

    Args:
        home_team: Home team name
        away_team: Away team name
        predicted_score: Predicted score (home, away)
        outcome_probabilities: Win/draw/loss probabilities
        confidence: Prediction confidence (0-1)
        prediction_method: Which engine was used
        elo_ratings: Optional Elo ratings
        key_factors: Optional key factors from rule engine

    Returns:
        AI analysis text explaining the prediction
    """

    # Check if AI is available
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "sk-your-key-here":
        return "AI分析功能需要配置OPENAI_API_KEY。\n\n当前预测基于数据模型计算得出，包括Elo评分、历史对战记录和赔率数据。"

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        # Build context
        elo_info = ""
        if elo_ratings:
            elo_info = f"\n- Elo评分: 主队 {elo_ratings['home']:.0f}, 客队 {elo_ratings['away']:.0f}"

        factors_info = ""
        if key_factors:
            factors_info = f"\n- 关键因素: {', '.join(key_factors)}"

        prompt = f"""作为足球预测分析专家，请对以下世界杯比赛预测进行深度分析：

**比赛**: {home_team} vs {away_team}

**预测结果**:
- 预测比分: {predicted_score['home']:.1f} - {predicted_score['away']:.1f}
- 主队胜率: {outcome_probabilities['home_win']*100:.1f}%
- 平局概率: {outcome_probabilities['draw']*100:.1f}%
- 客队胜率: {outcome_probabilities['away_win']*100:.1f}%
- 预测置信度: {confidence*100:.1f}%
- 预测引擎: {prediction_method}{elo_info}{factors_info}

请提供：
1. **预测合理性分析** - 基于概率分布、Elo评分等数据，评估预测的合理性
2. **关键影响因素** - 解释哪些因素最可能影响比赛结果
3. **风险提示** - 指出预测可能不准确的情况（如置信度较低、球队状态不稳定等）
4. **投注建议** - 如果这是真实投注，给出风险评估和建议

要求：
- 使用中文回答
- 简洁专业，避免冗余
- 基于数据分析，不要臆测
- 总长度控制在300字以内"""

        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL or "gpt-4",
            messages=[
                {"role": "system", "content": "你是一位专业的足球数据分析师，擅长解读预测模型的结果并给出客观建议。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )

        analysis = response.choices[0].message.content
        return analysis or "AI分析暂时无法生成，请稍后重试。"

    except Exception as e:
        return f"AI分析失败: {str(e)}\n\n预测基于数据模型计算，包括Elo评分和历史数据。"
