"""Replay-time feature flag configuration.

ReplayConfig is a dataclass that overlays feature-flag values onto the
global ``settings`` singleton for the duration of a replay. ``None`` means
"use current settings value" (so ``preset_all_on()`` inherits whatever
the runtime .env configured). A non-None bool forces that value.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from app.core.config import settings


@dataclass
class ReplayConfig:
    """Replay-time feature flag profile. Only includes flags that affect
    overlay output — arbitrary env vars (API keys, file paths) are out of
    scope because replay never triggers live LLM or writes to stores.
    """
    decision_quality_enabled: bool | None = None
    market_quality_enabled: bool | None = None
    source_reliability_enabled: bool | None = None
    prediction_calibration_enabled: bool | None = None
    llm_telemetry_enabled: bool | None = None
    guardrails_enabled: bool | None = None
    guardrail_llm_degraded_blocks_act: bool | None = None
    guardrail_uncalibrated_category_blocks_act: bool | None = None
    guardrail_high_conflict_blocks_act: bool | None = None

    @classmethod
    def preset_all_off(cls) -> "ReplayConfig":
        """Pre-Phase-1 baseline. Disables every overlay so the replayed
        record is byte-identical to a pre-overlay record."""
        return cls(
            decision_quality_enabled=False,
            market_quality_enabled=False,
            source_reliability_enabled=False,
            prediction_calibration_enabled=False,
            llm_telemetry_enabled=False,
            guardrails_enabled=False,
        )

    @classmethod
    def preset_all_on(cls) -> "ReplayConfig":
        """Use current settings values (inherit runtime .env). All fields
        None — apply_replay_config will skip them, leaving settings intact."""
        return cls()

    @classmethod
    def preset_llm_degraded(cls) -> "ReplayConfig":
        """Simulate full LLM failure. Enables llm_telemetry + guardrails +
        the llm_degraded_blocks_act rule + decision_quality (so a non-None
        ``final_displayed_direction`` exists for the guardrail to act on),
        and disables Rule 2/3 for isolation. The CLI's ``run_replay`` calls
        ``simulate_llm_degraded`` after ``replay_record`` to flip
        ``degraded_mode=True``; without that post-step this preset alone
        only builds the telemetry block — it does not force degraded mode.
        """
        return cls(
            decision_quality_enabled=True,  # produce final_displayed_direction for guardrail
            llm_telemetry_enabled=True,
            guardrails_enabled=True,
            guardrail_llm_degraded_blocks_act=True,
            # Disable Rule 2/3 so the only rule that can fire is
            # llm_degraded_blocks_act. Rule 2 otherwise fires fail-closed
            # when calibration_summary returns empty segments (test/empty
            # store default), which would downgrade direction to WAIT
            # before simulate_llm_degraded runs and short-circuit the
            # guardrail on a non-strong direction.
            guardrail_uncalibrated_category_blocks_act=False,
            guardrail_high_conflict_blocks_act=False,
        )

    @classmethod
    def preset_decision_quality_only(cls) -> "ReplayConfig":
        """All overlays off + only decision_quality on. Isolates the
        decision_quality overlay's impact on final direction."""
        cfg = cls.preset_all_off()
        cfg.decision_quality_enabled = True
        return cfg

    @classmethod
    def preset_market_quality_only(cls) -> "ReplayConfig":
        """All overlays off + only market_quality on. Isolates the
        market_quality overlay's impact on final direction."""
        cfg = cls.preset_all_off()
        cfg.market_quality_enabled = True
        return cfg

    @classmethod
    def preset_source_reliability_only(cls) -> "ReplayConfig":
        """All overlays off + only source_reliability on. Isolates the
        source_reliability overlay's impact on final direction."""
        cfg = cls.preset_all_off()
        cfg.source_reliability_enabled = True
        return cfg

    @classmethod
    def preset_guardrails_only(cls) -> "ReplayConfig":
        """All overlays off + only guardrails on (requires
        decision_quality to produce a direction for the guardrail to
        act on). Isolates the guardrail overlay's impact."""
        cfg = cls.preset_all_off()
        cfg.decision_quality_enabled = True  # guardrail needs a direction
        cfg.guardrails_enabled = True
        return cfg


@contextmanager
def apply_replay_config(cfg: ReplayConfig) -> Iterator[None]:
    """Temporarily overlay ReplayConfig onto global settings. Restores on
    exit even if an exception fires. Single-threaded replay use only —
    does not take a lock; concurrent replays would race on settings.

    Only fields with non-None values are applied; None fields leave the
    current settings value untouched (so preset_all_on is a true no-op).
    """
    saved: dict[str, object] = {}
    try:
        for field_name in cfg.__dataclass_fields__:
            val = getattr(cfg, field_name)
            if val is not None:
                key = field_name.upper()
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        yield
    finally:
        for key, val in saved.items():
            setattr(settings, key, val)
