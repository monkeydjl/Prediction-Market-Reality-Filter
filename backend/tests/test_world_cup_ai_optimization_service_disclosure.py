"""Test world_cup_ai_optimization_service.py exception disclosure.

Covers service layer sink #37:
- Line 129: f"AI优化失败: {error_type}" - includes exception type name

Verifies that error_type is a safe fixed classification and does not leak
exception details, user input, or sensitive information.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class SentinelException(Exception):
    """Custom exception with sentinel in name for testing."""

    pass


def test_optimize_prediction_error_type_is_safe():
    """Sink #37 (line 129): f"AI优化失败: {error_type}" must use safe classification.

    Expected behavior: error_type should be type(exc).__name__ which is a fixed
    class name, not user input or exception text. However, we must verify:
    1. Exception class names are safe (no sentinel data in __name__)
    2. The format doesn't include exception args/text
    3. Custom exceptions don't leak data through their type name
    """
    from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai

    # Mock LLM gateway to raise exception
    with patch(
        "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
        return_value=True,
    ):
        with patch(
            "app.services.world_cup_ai_optimization_service.complete_json",
            side_effect=SentinelException(
                "https://api.openai.com/v1/chat?key=sk-SECRET_KEY_12345"
            ),
        ):
            result = pytest.importorskip("asyncio").run(
                optimize_prediction_with_ai(
                    home_team="Brazil",
                    away_team="Argentina",
                    current_prediction={
                        "predicted_score": {"home": 2.0, "away": 1.0},
                        "outcome_probabilities": {
                            "home_win": 0.55,
                            "draw": 0.25,
                            "away_win": 0.20,
                        },
                        "confidence": 0.68,
                    },
                    prediction_method="elo_odds",
                )
            )

    assert result["status"] == "error"
    message = result["message"]

    # The message should be "AI优化失败: SentinelException"
    # This is CONDITIONALLY SAFE because:
    # 1. Exception class names are fixed Python identifiers
    # 2. The exception args/text are NOT included
    # 3. Custom exception names should not contain sensitive data

    # Verify exception text is NOT included
    assert "https://api.openai.com" not in message
    assert "sk-SECRET_KEY" not in message

    # Verify the format is as expected (Chinese prefix + type name)
    assert "AI优化失败: " in message
    assert "SentinelException" in message

    # The exception type name itself should not be treated as a leak
    # because it's a Python class name, not data


def test_optimize_prediction_error_type_standard_exceptions():
    """Verify standard Python exceptions are safe in error messages."""
    from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai

    standard_exceptions = [
        (ValueError("sensitive data"), "ValueError"),
        (RuntimeError("database://user:pass@host"), "RuntimeError"),
        (KeyError("SECRET_KEY"), "KeyError"),
        (TypeError("internal details"), "TypeError"),
    ]

    for exc, expected_type in standard_exceptions:
        with patch(
            "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
            return_value=True,
        ):
            with patch(
                "app.services.world_cup_ai_optimization_service.complete_json",
                side_effect=exc,
            ):
                result = pytest.importorskip("asyncio").run(
                    optimize_prediction_with_ai(
                        home_team="Brazil",
                        away_team="Argentina",
                        current_prediction={
                            "predicted_score": {"home": 2.0, "away": 1.0},
                            "outcome_probabilities": {
                                "home_win": 0.55,
                                "draw": 0.25,
                                "away_win": 0.20,
                            },
                            "confidence": 0.68,
                        },
                        prediction_method="elo_odds",
                    )
                )

        assert result["status"] == "error"
        message = result["message"]

        # Should contain only the type name, not the exception args
        assert f"AI优化失败: {expected_type}" == message
        assert str(exc.args[0]) not in message


def test_optimize_prediction_unavailable_message_is_safe():
    """Verify unavailable message is a fixed safe string."""
    from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai

    with patch(
        "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
        return_value=False,
    ):
        result = pytest.importorskip("asyncio").run(
            optimize_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                current_prediction={
                    "predicted_score": {"home": 2.0, "away": 1.0},
                    "outcome_probabilities": {
                        "home_win": 0.55,
                        "draw": 0.25,
                        "away_win": 0.20,
                    },
                    "confidence": 0.68,
                },
                prediction_method="elo_odds",
            )
        )

    assert result["status"] == "unavailable"
    message = result["message"]

    # Fixed safe Chinese message (line 41)
    assert message == "AI优化功能需要配置至少一个可用的 LLM Gateway 路由"


def test_optimize_prediction_gateway_failure_message_is_safe():
    """Verify gateway failure message is a fixed safe string."""
    from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai

    mock_result = type("Result", (), {"ok": False, "json_data": None})()

    with patch(
        "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
        return_value=True,
    ):
        with patch(
            "app.services.world_cup_ai_optimization_service.complete_json",
            return_value=mock_result,
        ):
            result = pytest.importorskip("asyncio").run(
                optimize_prediction_with_ai(
                    home_team="Brazil",
                    away_team="Argentina",
                    current_prediction={
                        "predicted_score": {"home": 2.0, "away": 1.0},
                        "outcome_probabilities": {
                            "home_win": 0.55,
                            "draw": 0.25,
                            "away_win": 0.20,
                        },
                        "confidence": 0.68,
                    },
                    prediction_method="elo_odds",
                )
            )

    assert result["status"] == "error"
    message = result["message"]

    # Fixed safe Chinese message (line 122)
    assert message == "AI优化失败: all_routes_failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
