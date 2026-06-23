"""AI-based prediction engine using LLM analysis.

This module uses LLM to provide tactical, psychological, and contextual
insights that complement the statistical rule-based predictions.
"""

import json
from typing import Any

from app.core.config import settings


def build_ai_prediction_prompt(
    home_team: str,
    away_team: str,
    kickoff_utc: str,
    stage: str,
    factors: dict[str, Any]
) -> str:
    """Build the prompt for LLM match prediction."""

    home = factors.get("home_team", {})
    away = factors.get("away_team", {})
    h2h = factors.get("head_to_head", {})
    context = factors.get("context", {})

    prompt = f"""You are a World Cup match analyst. Predict the score for this match.

Match: {home_team} vs {away_team}
Kickoff: {kickoff_utc}
Stage: {stage}

Team Statistics:
{home_team}:
- FIFA Ranking: {home.get('fifa_ranking', 'N/A')}
- Recent Form (last 5 matches): {home.get('recent_form', 0.5):.0%}
- Goals per game: {home.get('goals_per_game', 0.0):.1f}
- Defense rating: {home.get('defense_rating', 0.5):.0%}
- Injury impact: {home.get('injury_impact', 0.0):.2f}
- Days since last match: {home.get('days_since_last_match', 7)}

{away_team}:
- FIFA Ranking: {away.get('fifa_ranking', 'N/A')}
- Recent Form (last 5 matches): {away.get('recent_form', 0.5):.0%}
- Goals per game: {away.get('goals_per_game', 0.0):.1f}
- Defense rating: {away.get('defense_rating', 0.5):.0%}
- Injury impact: {away.get('injury_impact', 0.0):.2f}
- Days since last match: {away.get('days_since_last_match', 7)}

Head-to-Head:
- Total matches: {h2h.get('matches_played', 0)}
- {home_team} wins: {h2h.get('home_wins', 0)}
- Draws: {h2h.get('draws', 0)}
- {away_team} wins: {h2h.get('away_wins', 0)}
- Avg goals {home_team}: {h2h.get('avg_goals_home', 0.0):.1f}
- Avg goals {away_team}: {h2h.get('avg_goals_away', 0.0):.1f}

Context:
- Tournament stage: {context.get('tournament_stage', stage)}
- Stakes: {context.get('stakes', 'medium')}
- Weather: {context.get('weather', 'N/A')}

Consider these factors:
1. Tactical matchup (playing styles, strengths vs weaknesses)
2. Psychological factors (pressure, motivation, must-win situations)
3. Key player impact (star players, injuries)
4. Tournament context (group standings, knockout stakes)
5. Home advantage (if applicable)

Provide your prediction in JSON format:
{{
  "predicted_score": {{"home": 2.1, "away": 1.3}},
  "reasoning": "Brief explanation of key factors",
  "confidence": 0.75,
  "key_factors": ["factor 1", "factor 2", "factor 3"]
}}

Return ONLY the JSON, no additional text."""

    return prompt


async def predict_score_ai(
    home_team: str,
    away_team: str,
    kickoff_utc: str,
    stage: str,
    factors: dict[str, Any]
) -> dict[str, Any] | None:
    """Generate score prediction using AI analysis.

    Returns:
        {
            "predicted_score": {"home": float, "away": float},
            "reasoning": str,
            "confidence": float,
            "key_factors": list[str]
        }
        or None if AI prediction fails
    """

    # Check if AI is configured
    api_key = getattr(settings, "DASHSCOPE_API_KEY", None)
    if not api_key:
        return None

    try:
        # Import here to avoid circular dependency
        from app.services.ai_analysis_service import analyze_with_dashscope

        prompt = build_ai_prediction_prompt(home_team, away_team, kickoff_utc, stage, factors)

        # Call LLM
        response = await analyze_with_dashscope(prompt, temperature=0.7)

        # Parse JSON response
        # Try to extract JSON from response (in case LLM adds extra text)
        response_text = response.strip()

        # Find JSON object in response
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')

        if start_idx == -1 or end_idx == -1:
            return None

        json_str = response_text[start_idx:end_idx + 1]
        result = json.loads(json_str)

        # Validate structure
        if not isinstance(result.get("predicted_score"), dict):
            return None
        if "home" not in result["predicted_score"] or "away" not in result["predicted_score"]:
            return None

        # Ensure scores are in reasonable range
        home_score = float(result["predicted_score"]["home"])
        away_score = float(result["predicted_score"]["away"])

        if home_score < 0 or home_score > 10 or away_score < 0 or away_score > 10:
            return None

        return {
            "predicted_score": {
                "home": round(home_score, 2),
                "away": round(away_score, 2)
            },
            "reasoning": result.get("reasoning", "")[:500],  # Limit length
            "confidence": min(max(float(result.get("confidence", 0.7)), 0.0), 1.0),
            "key_factors": result.get("key_factors", [])[:5]  # Max 5 factors
        }

    except Exception as e:
        # Log error but don't fail - fall back to rule-only prediction
        print(f"AI prediction failed: {e}")
        return None
