"""AI analysis service for World Cup predictions."""


from app.services.llm_gateway_service import complete_chat, has_configured_llm_route


async def analyze_prediction_with_ai(
    home_team: str,
    away_team: str,
    predicted_score: dict[str, float],
    outcome_probabilities: dict[str, float],
    confidence: float,
    prediction_method: str,
    elo_ratings: dict[str, float] | None = None,
    key_factors: list[str] | None = None,
) -> str:
    """Use the LLM Gateway to analyze a match prediction and provide insights.

    The Gateway handles provider/model ordering and fallback for World Cup
    tasks, including LLM_ROUTE_WORLD_CUP, LLM_ROUTE_DEFAULT, numbered
    OPENAI_API_KEY_N providers, and legacy OPENAI_* config.
    """

    if not has_configured_llm_route("world_cup"):
        return (
            "AI\u5206\u6790\u529f\u80fd\u9700\u8981\u914d\u7f6e\u81f3\u5c11\u4e00\u4e2a\u53ef\u7528\u7684 LLM Gateway \u8def\u7531\u3002\n\n"
            "\u5f53\u524d\u9884\u6d4b\u57fa\u4e8e\u6570\u636e\u6a21\u578b\u8ba1\u7b97\u5f97\u51fa\uff0c\u5305\u62ec Elo \u8bc4\u5206\u3001\u5386\u53f2\u5bf9\u6218\u8bb0\u5f55\u548c\u8d54\u7387\u6570\u636e\u3002"
        )

    try:
        elo_info = ""
        if elo_ratings:
            elo_info = f"\n- Elo rating: home {elo_ratings['home']:.0f}, away {elo_ratings['away']:.0f}"

        factors_info = ""
        if key_factors:
            factors_info = f"\n- Key factors: {', '.join(key_factors)}"

        prompt = f"""Analyze this World Cup match prediction in Chinese.

Match: {home_team} vs {away_team}

Prediction:
- Score: {predicted_score['home']:.1f} - {predicted_score['away']:.1f}
- Home win probability: {outcome_probabilities['home_win']*100:.1f}%
- Draw probability: {outcome_probabilities['draw']*100:.1f}%
- Away win probability: {outcome_probabilities['away_win']*100:.1f}%
- Confidence: {confidence*100:.1f}%
- Engine: {prediction_method}{elo_info}{factors_info}

Please cover:
1. Prediction reasonableness based on probabilities and Elo/data.
2. Key factors most likely to affect the result.
3. Risk warnings for uncertainty.
4. Simulated trading guidance and risk control.

Requirements: answer in Chinese, concise and data-grounded, within 300 Chinese characters."""

        result = await complete_chat(
            task="world_cup",
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional football prediction analyst. Answer in Chinese.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=800,
        )

        if not result.ok or not result.content:
            return (
                f"AI\u5206\u6790\u6682\u65f6\u65e0\u6cd5\u751f\u6210\uff1a{result.degraded_reason or 'all_routes_failed'}\u3002\n\n"
                "\u9884\u6d4b\u4ecd\u57fa\u4e8e\u6570\u636e\u6a21\u578b\u8ba1\u7b97\u5f97\u51fa\u3002"
            )
        return result.content

    except Exception as exc:
        return (
            f"AI\u5206\u6790\u5931\u8d25: {str(exc)}\n\n"
            "\u9884\u6d4b\u57fa\u4e8e\u6570\u636e\u6a21\u578b\u8ba1\u7b97\uff0c\u5305\u62ec Elo \u8bc4\u5206\u548c\u5386\u53f2\u6570\u636e\u3002"
        )
