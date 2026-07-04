from app.core.config import settings


def test_conclusion_challenge_settings_exist_with_safe_defaults():
    assert settings.CONCLUSION_CHALLENGE_ENABLED is False
    assert settings.CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED is False
    assert settings.CONCLUSION_CHALLENGE_STRICTNESS == "normal"
    assert settings.CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS == 1
    assert settings.WORLD_CUP_CHALLENGE_ENABLED is False
    assert settings.EVENT_CHALLENGE_ENABLED is False
