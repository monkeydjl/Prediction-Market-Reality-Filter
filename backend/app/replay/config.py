"""Replay-time feature flag configuration.

ReplayConfig is a dataclass that overlays feature-flag values onto the
global ``settings`` singleton for the duration of a replay. ``None`` means
"use current settings value" (so ``preset_all_on()`` inherits whatever
the runtime .env configured). A non-None bool forces that value.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

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
    # Phase 3: execution_quality overlay + guardrail rule 4 (market_not_executable).
    # Without these, a replay's guardrail phase would read a stale
    # execution_quality block left on the record by a prior production run,
    # or depend on the live env's EXECUTION_QUALITY_ENABLED, instead of the
    # chosen preset.
    execution_quality_enabled: bool | None = None
    guardrail_market_not_executable_blocks_act: bool | None = None

    # Arbitrary KEY→value overrides applied AFTER the bool fields above.
    # Used by the diff CLI's --set / --set-a / --set-b flags to override
    # threshold settings (MARKET_MAX_SPREAD_PCT, DECISION_ACT_EDGE, etc.)
    # for the duration of a replay. KEY must be UPPERCASE; value is the
    # already-coerced Python type (bool/int/float/str). None = no overrides.
    settings_overrides: dict[str, Any] | None = None

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
            execution_quality_enabled=False,
            guardrail_market_not_executable_blocks_act=False,
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
        and disables Rule 2/3/4 for isolation. The CLI's ``run_replay`` calls
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
            # Disable Rule 4 (market_not_executable) + execution_quality so
            # rule 4 can't downgrade direction to WAIT before
            # simulate_llm_degraded runs. Without this, a live env with
            # EXECUTION_QUALITY_ENABLED=true could let rule 4 fire first,
            # short-circuiting the llm_degraded_blocks_act rule on a
            # non-strong direction.
            execution_quality_enabled=False,
            guardrail_market_not_executable_blocks_act=False,
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
        """DQ + LLM telemetry + execution_quality baseline, plus guardrails
        on with all 4 rules enabled. NOT all_off + guardrails, because
        guardrails need a ``final_displayed_direction`` to gate (only
        produced by decision_quality), and turning on DQ alone already
        downgrades empty-evidence YES/NO to WAIT (see
        decision_quality_service._apply_downgrade_rules rule 4). Comparing
        all_off vs this preset would conflate DQ's downgrades with
        guardrail's.

        All 4 guardrail rules need their prerequisites built:
          - Rule 1 (llm_degraded_blocks_act) reads llm_telemetry.degraded_mode
          - Rule 2 (uncalibrated_category) reads the calibration store
          - Rule 3 (high_conflict) reads evidence_breakdown
          - Rule 4 (market_not_executable) reads execution_quality.executable
        So this preset enables DQ + llm_telemetry + execution_quality.
        The per-phase CLI's baseline for guardrails is
        ``preset_guardrails_baseline`` (same prerequisites, guardrails off)
        so the marginal comparison isolates guardrails' impact.
        """
        cfg = cls.preset_all_off()
        cfg.decision_quality_enabled = True
        cfg.llm_telemetry_enabled = True
        cfg.execution_quality_enabled = True
        cfg.guardrails_enabled = True
        cfg.guardrail_llm_degraded_blocks_act = True
        cfg.guardrail_uncalibrated_category_blocks_act = True
        cfg.guardrail_high_conflict_blocks_act = True
        cfg.guardrail_market_not_executable_blocks_act = True
        return cfg

    @classmethod
    def preset_guardrails_baseline(cls) -> "ReplayConfig":
        """Baseline for the guardrails marginal comparison: same
        prerequisites as preset_guardrails_only (DQ + LLM telemetry +
        execution_quality) but with guardrails OFF. The per-phase CLI
        compares this preset vs preset_guardrails_only so the delta
        reflects only guardrails' impact, not DQ's or execution_quality's.
        """
        cfg = cls.preset_all_off()
        cfg.decision_quality_enabled = True
        cfg.llm_telemetry_enabled = True
        cfg.execution_quality_enabled = True
        # guardrails_enabled stays False (from preset_all_off)
        return cfg


@contextmanager
def apply_replay_config(cfg: ReplayConfig) -> Iterator[None]:
    """Temporarily overlay ReplayConfig onto global settings. Restores on
    exit even if an exception fires. Single-threaded replay use only —
    does not take a lock; concurrent replays would race on settings.

    Only fields with non-None values are applied; None fields leave the
    current settings value untouched (so preset_all_on is a true no-op).

    ``settings_overrides`` is applied AFTER the bool fields, so if a key
    appears in both (unlikely but possible), the override wins.
    """
    saved: dict[str, object] = {}
    try:
        for field_name in cfg.__dataclass_fields__:
            if field_name == "settings_overrides":
                continue
            val = getattr(cfg, field_name)
            if val is not None:
                key = field_name.upper()
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        if cfg.settings_overrides:
            for key, val in cfg.settings_overrides.items():
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        yield
    finally:
        for key, val in saved.items():
            setattr(settings, key, val)
