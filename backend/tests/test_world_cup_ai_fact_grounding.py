"""P2-W4: the LLM may explain the structured facts, and nothing else.

Before this suite the two World Cup prompts asked open questions ("key factors",
"blind spots") with no statement of what the model did or did not know, and the
analysis prompt asked for reasoning "based on probabilities and Elo/data" while
its only caller passed no Elo. These tests pin the prompt text that closes that
gap, because the prompt *is* the contract here - there is no return value to
assert against.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.llm_fact_grounding import (
    INVENTABLE_FACT_KINDS,
    build_fact_grounding_section,
)
from app.services.llm_gateway_service import LLMAttempt, LLMResult
from app.services.world_cup_ai_analysis_service import analyze_prediction_with_ai
from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai


def _chat_result(content: str) -> LLMResult:
    return LLMResult(
        ok=True,
        content=content,
        provider="openai_1",
        model="model-a",
        attempts=[LLMAttempt("openai_1", "model-a", "success")],
    )


def _json_result(data: dict) -> LLMResult:
    return LLMResult(
        ok=True,
        content="{}",
        json_data=data,
        provider="openai_1",
        model="model-a",
        attempts=[LLMAttempt("openai_1", "model-a", "success")],
    )


class FactGroundingSectionTests(unittest.TestCase):
    def test_names_every_inventable_kind_when_nothing_is_supplied(self):
        section = build_fact_grounding_section({})
        self.assertIn("No facts beyond the prediction numbers above", section)
        # 红黄牌 is the item P2-W4 is named after; the rest are the same class.
        for _key, label in INVENTABLE_FACT_KINDS:
            self.assertIn(label, section)

    def test_a_supplied_kind_leaves_the_not_given_list(self):
        section = build_fact_grounding_section({"elo_ratings": {"home": 1800, "away": 1750}})
        self.assertIn("Elo ratings: home 1800, away 1750", section)
        not_given = section.split("You were NOT given")[1]
        self.assertNotIn("Elo 评分", not_given)
        # The kinds still absent must still be listed.
        self.assertIn("红黄牌", not_given)

    def test_an_empty_value_counts_as_not_supplied(self):
        # An engine that stored `elo_ratings: {}` (world_cup_gbm_engine can, via
        # `gbm_pred.get("elo_ratings", {})`) must not take Elo off the missing
        # list while putting no number in its place.
        for empty in ({}, [], "", "   ", None):
            section = build_fact_grounding_section({"elo_ratings": empty})
            self.assertIn("Elo ratings (Elo 评分)", section, f"empty={empty!r}")
            self.assertNotIn("- Elo ratings:", section, f"empty={empty!r}")

    def test_keys_outside_the_vocabulary_render_without_changing_the_list(self):
        section = build_fact_grounding_section({"venue": "Estadio Azteca", "stage": "group_stage"})
        self.assertIn("- Venue: Estadio Azteca", section)
        self.assertIn("- Stage: group_stage", section)
        for _key, label in INVENTABLE_FACT_KINDS:
            self.assertIn(label, section)

    def test_states_the_three_hard_rules(self):
        section = build_fact_grounding_section({})
        # Rule 1 bounds the facts, rule 2 gives the model an out other than
        # guessing, rule 3 stops it describing sources it was never told about.
        self.assertIn("must appear in the prediction numbers or the fact list above", section)
        self.assertIn("该项数据未提供", section)
        self.assertIn("Do not say the prediction was built from any data source", section)

    def test_a_list_fact_is_flattened_onto_one_line(self):
        section = build_fact_grounding_section({"key_factors": ["form", "rest"]})
        self.assertIn("- Engine key factors: form, rest", section)


class AnalysisPromptGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def _prompt_for(self, **kwargs) -> str:
        with patch(
            "app.services.world_cup_ai_analysis_service.has_configured_llm_route",
            return_value=True,
        ), patch(
            "app.services.world_cup_ai_analysis_service.complete_chat",
            new=AsyncMock(return_value=_chat_result("analysis ok")),
        ) as mock_complete:
            await analyze_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                predicted_score={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
                confidence=0.7,
                prediction_method="hybrid",
                **kwargs,
            )
        messages = mock_complete.await_args.kwargs["messages"]
        self.assertEqual(messages[1]["role"], "user")
        return str(messages[1]["content"])

    async def test_prompt_forbids_inventing_card_counts(self):
        prompt = await self._prompt_for()
        self.assertIn("红黄牌", prompt)
        self.assertIn("该项数据未提供", prompt)

    async def test_prompt_no_longer_asks_for_reasoning_from_elo_it_lacks(self):
        prompt = await self._prompt_for()
        # The old line was "Prediction reasonableness based on probabilities and
        # Elo/data." with no Elo anywhere in the prompt.
        self.assertNotIn("Elo/data", prompt)
        self.assertIn("Elo ratings (Elo 评分)", prompt)

    async def test_supplied_elo_reaches_the_prompt(self):
        prompt = await self._prompt_for(elo_ratings={"home": 1900.0, "away": 1850.0})
        self.assertIn("home 1900.0, away 1850.0", prompt)
        self.assertNotIn("Elo 评分", prompt.split("You were NOT given")[1])

    async def test_data_quality_is_shown_to_the_model(self):
        prompt = await self._prompt_for(data_quality="partial")
        self.assertIn("- Input data quality: partial", prompt)

    async def test_system_message_repeats_the_constraint(self):
        with patch(
            "app.services.world_cup_ai_analysis_service.has_configured_llm_route",
            return_value=True,
        ), patch(
            "app.services.world_cup_ai_analysis_service.complete_chat",
            new=AsyncMock(return_value=_chat_result("analysis ok")),
        ) as mock_complete:
            await analyze_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                predicted_score={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
                confidence=0.7,
                prediction_method="hybrid",
            )
        system = mock_complete.await_args.kwargs["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("never assert a statistic that was not provided", system["content"])

    async def test_unavailable_route_message_claims_no_data_source(self):
        with patch(
            "app.services.world_cup_ai_analysis_service.has_configured_llm_route",
            return_value=False,
        ):
            message = await analyze_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                predicted_score={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
                confidence=0.7,
                prediction_method="hybrid",
            )
        # This branch never reads the prediction's factors, so it cannot know
        # whether Elo, head-to-head or odds were used.
        self.assertNotIn("Elo", message)
        self.assertNotIn("赔率", message)
        self.assertIn("由数据模型计算得出", message)


class OptimizationPromptGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def _prompt_for(self, **kwargs) -> str:
        current_prediction = {
            "predicted_score": {"home": 2.0, "away": 1.0},
            "outcome_probabilities": {"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
            "confidence": 0.7,
        }
        current_prediction.update(kwargs.pop("current_prediction", {}))
        with patch(
            "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
            return_value=True,
        ), patch(
            "app.services.world_cup_ai_optimization_service.complete_json",
            new=AsyncMock(return_value=_json_result({"blind_spots": []})),
        ) as mock_complete:
            await optimize_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                current_prediction=current_prediction,
                prediction_method="hybrid",
                **kwargs,
            )
        return str(mock_complete.await_args.kwargs["messages"][1]["content"])

    async def test_prompt_forbids_inventing_card_counts(self):
        prompt = await self._prompt_for()
        self.assertIn("红黄牌", prompt)
        self.assertIn("该项数据未提供", prompt)

    async def test_the_context_keys_the_route_actually_sends_now_render(self):
        # /optimize passes exactly these five; the old three-key reader dropped
        # every one of them, so `context_info` was empty at every call site.
        prompt = await self._prompt_for(
            match_context={
                "stage": "group_stage",
                "group": "C",
                "venue": "Estadio Azteca",
                "data_quality": "partial",
                "key_factors": ["form", "rest"],
            }
        )
        self.assertIn("- Stage: group_stage", prompt)
        self.assertIn("- Group: C", prompt)
        self.assertIn("- Venue: Estadio Azteca", prompt)
        self.assertIn("- Input data quality: partial", prompt)
        self.assertIn("- Engine key factors: form, rest", prompt)

    async def test_a_context_injury_list_leaves_the_not_given_list(self):
        prompt = await self._prompt_for(match_context={"injuries": "two defenders out"})
        self.assertIn("- Injuries: two defenders out", prompt)
        self.assertNotIn("伤停名单", prompt.split("You were NOT given")[1])

    async def test_blind_spots_may_be_fewer_than_two(self):
        prompt = await self._prompt_for()
        self.assertIn("at most 2 blind spots", prompt)
        self.assertIn("rather than naming a blind spot you cannot ground", prompt)

    async def test_elo_without_a_difference_key_does_not_raise(self):
        # The old renderer read elo_ratings['difference'] unguarded, outside the
        # try block, so a partial dict became a 500 from /optimize.
        prompt = await self._prompt_for(
            current_prediction={"elo_ratings": {"home": 1900, "away": 1850}}
        )
        self.assertIn("- Elo ratings: home 1900, away 1850", prompt)

    async def test_system_message_repeats_the_constraint(self):
        with patch(
            "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
            return_value=True,
        ), patch(
            "app.services.world_cup_ai_optimization_service.complete_json",
            new=AsyncMock(return_value=_json_result({"blind_spots": []})),
        ) as mock_complete:
            await optimize_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                current_prediction={
                    "predicted_score": {"home": 2.0, "away": 1.0},
                    "outcome_probabilities": {
                        "home_win": 0.5,
                        "draw": 0.25,
                        "away_win": 0.25,
                    },
                    "confidence": 0.7,
                },
                prediction_method="hybrid",
            )
        system = mock_complete.await_args.kwargs["messages"][0]
        self.assertIn("never assert a statistic that was not provided", system["content"])


class AnalyzeRouteWiringTests(unittest.TestCase):
    """The facts exist on the stored prediction; the route has to hand them over.

    A normal test of the analyzer passes whether or not the route supplies these,
    so scan the route body - the same reason `TestReviewQueueRouterWiring` exists.
    """

    def test_analyze_route_forwards_elo_and_data_quality(self):
        import inspect

        from app.api.routes.world_cup_predictions import analyze_match_prediction

        source = inspect.getsource(analyze_match_prediction)
        self.assertIn('elo_ratings=prediction.factors.get("elo_ratings")', source)
        self.assertIn('data_quality=prediction.factors.get("data_quality")', source)


if __name__ == "__main__":
    unittest.main()
