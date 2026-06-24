"""AI-based prediction engine using LLM analysis.

This module uses LLM to provide tactical, psychological, and contextual
insights that complement the statistical rule-based predictions.
"""

import json
import logging
from typing import Any

from app.core.config import settings
from app.services.world_cup_tactical_profiles import format_tactical_summary

logger = logging.getLogger(__name__)


def build_ai_prediction_prompt(
    home_team: str,
    away_team: str,
    kickoff_utc: str,
    stage: str,
    factors: dict[str, Any],
    rule_prediction: dict[str, Any]
) -> str:
    """Build the prompt for LLM match prediction adjustment.

    Instead of asking the LLM to predict a score from scratch, this prompt
    presents the rule-engine prediction and asks the LLM whether it needs
    calibration, returning adjustment deltas rather than absolute scores.
    """

    home = factors.get("home_team", {})
    away = factors.get("away_team", {})
    h2h = factors.get("head_to_head", {})

    # Extract rule-engine prediction details
    rule_score = rule_prediction.get("predicted_score", {})
    rule_home = float(rule_score.get("home", 0.0))
    rule_away = float(rule_score.get("away", 0.0))

    outcome_probs = rule_prediction.get("outcome_probabilities", {})
    home_win_pct = float(outcome_probs.get("home_win", 0.0)) * 100
    draw_pct = float(outcome_probs.get("draw", 0.0)) * 100
    away_pct = float(outcome_probs.get("away_win", 0.0)) * 100

    confidence_pct = float(rule_prediction.get("confidence", 0.7)) * 100

    # Team statistics
    home_gpg = float(home.get("goals_per_game", 0.0))
    home_cpg = float(home.get("goals_conceded_per_game", 0.0))
    home_form = float(home.get("recent_form", 0.5))
    away_gpg = float(away.get("goals_per_game", 0.0))
    away_cpg = float(away.get("goals_conceded_per_game", 0.0))
    away_form = float(away.get("recent_form", 0.5))

    # Head-to-head summary
    h2h_played = int(h2h.get("matches_played", 0))
    if h2h_played > 0:
        h2h_summary = (
            f"{h2h_played}场，{home_team}胜{h2h.get('home_wins', 0)}"
            f"平{h2h.get('draws', 0)}负{h2h.get('away_wins', 0)}，"
            f"均进{float(h2h.get('avg_goals_home', 0.0)):.1f}-"
            f"{float(h2h.get('avg_goals_away', 0.0)):.1f}"
        )
    else:
        h2h_summary = "无历史交锋记录"

    tactical_summary = format_tactical_summary(home_team, away_team)

    prompt = f"""你是足球预测校准专家。规则引擎已给出预测，请判断是否需要调整。

比赛：{home_team} vs {away_team}
规则引擎预测：比分 {rule_home:.1f}-{rule_away:.1f}
胜平负概率：主胜{home_win_pct:.0f}% 平{draw_pct:.0f}% 客胜{away_pct:.0f}%
置信度：{confidence_pct:.0f}%

球队数据：
- {home_team}: 场均进球{home_gpg:.1f} 失球{home_cpg:.1f} 近期状态{home_form:.0%}
- {away_team}: 场均进球{away_gpg:.1f} 失球{away_cpg:.1f} 近期状态{away_form:.0%}
- H2H: {h2h_summary}
- 战术：{tactical_summary}

请判断规则引擎预测是否合理，给出调整建议：
1. 主队比分需要调整多少？（-1.0 到 +1.0，0表示不调整）
2. 客队比分需要调整多少？
3. 调整理由是什么？（限50字）

JSON格式:
{{
  "home_adjustment": 0.0,
  "away_adjustment": 0.0,
  "reasoning": "简短理由",
  "confidence_in_adjustment": 0.6
}}

Return ONLY the JSON, no additional text."""

    return prompt


async def predict_score_ai(
    home_team: str,
    away_team: str,
    kickoff_utc: str,
    stage: str,
    factors: dict[str, Any],
    rule_prediction: dict[str, Any]
) -> dict[str, Any] | None:
    """Generate score prediction by calibrating the rule-engine prediction.

    Instead of predicting a score from scratch, the LLM evaluates the
    rule-engine prediction and suggests adjustments (deltas).  The
    adjustments are applied to the rule-engine score, outcome
    probabilities are recalculated via Poisson, and confidence is
    modulated by the LLM's own confidence in its adjustment.

    Args:
        rule_prediction: The prediction dict produced by the rule engine,
            used as the baseline that the AI calibrates.

    Returns:
        {
            "predicted_score": {"home": float, "away": float},
            "outcome_probabilities": {"home_win": float, "draw": float, "away_win": float},
            "confidence": float,
            "reasoning": str,
            "confidence_in_adjustment": float,
            "key_factors": list[str]
        }
        or None if AI prediction fails
    """

    # Check if AI is configured
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return None

    try:
        # Call OpenAI directly (ask_llm returns a fixed-format dict, not raw text)
        from app.services.openai_service import get_client
        from app.services.world_cup_rule_engine import calculate_outcome_probabilities

        prompt = build_ai_prediction_prompt(
            home_team, away_team, kickoff_utc, stage, factors, rule_prediction
        )

        client = get_client()
        ai_response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "你是足球预测校准专家。只返回有效的JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        response_text = ai_response.choices[0].message.content or ""

        # Find JSON object in response
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')

        if start_idx == -1 or end_idx == -1:
            logger.warning("AI response contained no JSON for %s vs %s", home_team, away_team)
            return None

        json_str = response_text[start_idx:end_idx + 1]
        result = json.loads(json_str)

        # Extract adjustment deltas
        home_adjustment = float(result.get("home_adjustment", 0.0))
        away_adjustment = float(result.get("away_adjustment", 0.0))

        # Clamp adjustments to the allowed range (-1.0 .. +1.0)
        home_adjustment = max(-1.0, min(home_adjustment, 1.0))
        away_adjustment = max(-1.0, min(away_adjustment, 1.0))

        confidence_in_adjustment = float(result.get("confidence_in_adjustment", 0.5))
        confidence_in_adjustment = max(0.0, min(confidence_in_adjustment, 1.0))

        reasoning = str(result.get("reasoning", ""))[:500]

        # Apply adjustments to the rule-engine prediction
        rule_score = rule_prediction.get("predicted_score", {})
        rule_home = float(rule_score.get("home", 0.0))
        rule_away = float(rule_score.get("away", 0.0))

        adjusted_home = max(0.0, rule_home + home_adjustment)
        adjusted_away = max(0.0, rule_away + away_adjustment)

        # Recalculate outcome probabilities from the adjusted scores (Poisson)
        outcome_probs = calculate_outcome_probabilities(adjusted_home, adjusted_away)

        # Confidence modulation:
        #   - If the AI is confident (>0.7) in a meaningful adjustment (>0.3),
        #     give the prediction a slight boost.
        #   - Otherwise, slightly reduce confidence (uncertain or trivial change).
        rule_confidence = float(rule_prediction.get("confidence", 0.7))
        adjustment_magnitude = max(abs(home_adjustment), abs(away_adjustment))

        if confidence_in_adjustment > 0.7 and adjustment_magnitude > 0.3:
            final_confidence = min(0.95, rule_confidence + 0.05)
        else:
            final_confidence = max(0.50, rule_confidence - 0.05)

        logger.info(
            "AI adjustment for %s vs %s: home_adj=%+.2f, away_adj=%+.2f, "
            "conf_in_adj=%.2f, rule=%.2f-%.2f -> adjusted=%.2f-%.2f, reasoning=%s",
            home_team, away_team, home_adjustment, away_adjustment,
            confidence_in_adjustment, rule_home, rule_away,
            adjusted_home, adjusted_away, reasoning[:100],
        )

        return {
            "predicted_score": {
                "home": round(adjusted_home, 2),
                "away": round(adjusted_away, 2)
            },
            "outcome_probabilities": outcome_probs,
            "confidence": round(final_confidence, 3),
            "reasoning": reasoning,
            "confidence_in_adjustment": round(confidence_in_adjustment, 3),
            "key_factors": []
        }

    except Exception as e:
        # Log error but don't fail - fall back to rule-only prediction
        logger.error("AI prediction failed for %s vs %s: %s", home_team, away_team, e, exc_info=True)
        return None
