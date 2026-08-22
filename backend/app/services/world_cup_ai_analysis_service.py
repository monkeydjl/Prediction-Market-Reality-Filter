"""AI analysis service for World Cup predictions."""


from app.services.llm_fact_grounding import build_fact_grounding_section
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
    data_quality: str | None = None,
) -> str:
    """Use the LLM Gateway to analyze a match prediction and provide insights.

    The Gateway handles provider/model ordering and fallback for World Cup
    tasks, including LLM_ROUTE_WORLD_CUP, LLM_ROUTE_DEFAULT, numbered
    OPENAI_API_KEY_N providers, and legacy OPENAI_* config.
    """

    if not has_configured_llm_route("world_cup"):
        # Naming Elo / head-to-head / odds here was a provenance claim nothing
        # checked: this branch never sees the prediction's factors, and the
        # sources actually used vary by engine. Say only what is known.
        return (
            "AI\u5206\u6790\u529f\u80fd\u9700\u8981\u914d\u7f6e\u81f3\u5c11\u4e00\u4e2a\u53ef\u7528\u7684 LLM Gateway \u8def\u7531\u3002\n\n"
            "\u5f53\u524d\u9884\u6d4b\u7531\u6570\u636e\u6a21\u578b\u8ba1\u7b97\u5f97\u51fa\u3002"
        )

    try:
        # Only what the caller actually handed over reaches the prompt. Empty
        # values are dropped by `build_fact_grounding_section`, which then names
        # the corresponding fact kind as one the model does not have.
        grounding = build_fact_grounding_section(
            {
                "elo_ratings": elo_ratings,
                "key_factors": key_factors,
                "data_quality": data_quality,
            }
        )

        prompt = f"""Analyze this World Cup match prediction in Chinese.

Match: {home_team} vs {away_team}

Prediction:
- Score: {predicted_score['home']:.1f} - {predicted_score['away']:.1f}
- Home win probability: {outcome_probabilities['home_win']*100:.1f}%
- Draw probability: {outcome_probabilities['draw']*100:.1f}%
- Away win probability: {outcome_probabilities['away_win']*100:.1f}%
- Confidence: {confidence*100:.1f}%
- Engine: {prediction_method}

{grounding}

Please cover:
1. Whether the prediction is reasonable given the probabilities and the facts above.
2. Which of the facts above is most likely to affect the result.
3. Risk warnings for uncertainty, including any fact you were not given.
4. Simulated trading guidance and risk control.

Requirements: answer in Chinese, concise, grounded only in the facts above, within 300 Chinese characters."""

        result = await complete_chat(
            task="world_cup",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional football prediction analyst. "
                        "Answer in Chinese. You interpret only the structured "
                        "facts you are given and never assert a statistic that "
                        "was not provided."
                    ),
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
            "\u9884\u6d4b\u7531\u6570\u636e\u6a21\u578b\u8ba1\u7b97\u5f97\u51fa\u3002"
        )
