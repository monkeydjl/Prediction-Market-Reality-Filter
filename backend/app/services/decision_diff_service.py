"""Decision diff service (Plan 5 §5.4).

Pure function that compares two timeline snapshots and ranks the primary
change driver behind a direction change. No I/O, no LLM, no settings
reads — same convention as ``build_decision_quality`` /
``build_source_reliability`` / ``build_execution_quality``.

The diff is consumed by the ``/api/events/{event_id}/decision-timeline``
route and rendered by the frontend ``DecisionTimelinePanel`` so a user
can see *why* an event flipped from YES to WAIT.

Driver ranking (first match wins):
    1. manual_resolution  — outcome appeared (event was resolved)
    2. llm_degraded       — llm_degraded_mode flipped False → True
    3. guardrail          — guardrail_fired list went from empty/null to non-empty
    4. market_quality     — market_quality.downgraded flipped False → True
    5. source_conflict    — source_reliability.downgraded flipped False → True
    6. calibration        — decision_quality.downgraded flipped False → True
    7. market_move        — probability.estimated moved by >= 5 percentage points
    8. none               — no material change detected

Overlay drivers (4-6) take precedence over market_move (7) because an
explicit overlay downgrade is stronger evidence of *why* the direction
changed than a probability drift alone.
"""
from __future__ import annotations

from typing import Any

_PROBABILITY_MOVE_THRESHOLD = 5.0  # percentage points


def build_decision_diff(
    prev: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compare two timeline snapshots and return a structured diff.

    ``prev`` may be None (first snapshot in a timeline). ``current`` must
    be a dict. Returns a diff dict with keys:
        direction_changed       — bool
        prev_direction          — str | None
        current_direction       — str | None
        probability_delta       — dict with baseline/estimated/change deltas
                                  (None values when either side lacks them)
        overlay_deltas          — list of per-overlay delta dicts
                                  {overlay, field, prev, current, changed}
        primary_change_driver   — one of the locked driver strings
        prev_downgrade_reason   — str | None
        current_downgrade_reason — str | None

    Pure, synchronous, deterministic. Does not crash on missing fields.
    """
    if not isinstance(current, dict):
        return {
            "direction_changed": False,
            "prev_direction": None,
            "current_direction": None,
            "probability_delta": {},
            "overlay_deltas": [],
            "primary_change_driver": "none",
            "prev_downgrade_reason": None,
            "current_downgrade_reason": None,
        }
    if not isinstance(prev, dict):
        return {
            "direction_changed": False,
            "prev_direction": None,
            "current_direction": current.get("final_displayed_direction"),
            "probability_delta": {},
            "overlay_deltas": [],
            "primary_change_driver": "initial",
            "prev_downgrade_reason": None,
            "current_downgrade_reason": current.get("final_downgrade_reason"),
        }

    prev_dir = prev.get("final_displayed_direction")
    cur_dir = current.get("final_displayed_direction")
    direction_changed = prev_dir != cur_dir

    prob_delta = _probability_delta(prev.get("probability"),
                                    current.get("probability"))
    overlay_deltas = _overlay_deltas(prev, current)
    driver = _rank_driver(prev, current, direction_changed, prob_delta)

    return {
        "direction_changed": direction_changed,
        "prev_direction": prev_dir,
        "current_direction": cur_dir,
        "probability_delta": prob_delta,
        "overlay_deltas": overlay_deltas,
        "primary_change_driver": driver,
        "prev_downgrade_reason": prev.get("final_downgrade_reason"),
        "current_downgrade_reason": current.get("final_downgrade_reason"),
    }


def _probability_delta(prev_prob: Any, cur_prob: Any) -> dict[str, Any]:
    if not isinstance(prev_prob, dict) or not isinstance(cur_prob, dict):
        return {}
    delta: dict[str, Any] = {}
    for key in ("baseline", "estimated", "change"):
        pv = prev_prob.get(key)
        cv = cur_prob.get(key)
        if isinstance(pv, (int, float)) and isinstance(cv, (int, float)):
            delta[key] = cv - pv
        else:
            delta[key] = None
    return delta


def _overlay_deltas(prev: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Record per-overlay downgraded-flag and downgrade_reason changes."""
    deltas: list[dict[str, Any]] = []
    for overlay_key in ("decision_quality", "market_quality",
                        "source_reliability", "execution_quality"):
        pv = prev.get(overlay_key)
        cv = current.get(overlay_key)
        pv_down = pv.get("downgraded") if isinstance(pv, dict) else None
        cv_down = cv.get("downgraded") if isinstance(cv, dict) else None
        pv_reason = pv.get("downgrade_reason") if isinstance(pv, dict) else None
        cv_reason = cv.get("downgrade_reason") if isinstance(cv, dict) else None
        if pv_down != cv_down or pv_reason != cv_reason:
            deltas.append({
                "overlay": overlay_key,
                "field": "downgraded",
                "prev": pv_down,
                "current": cv_down,
                "prev_reason": pv_reason,
                "current_reason": cv_reason,
                "changed": True,
            })
    return deltas


def _overlay_downgraded_flipped_true(prev: dict[str, Any], current: dict[str, Any],
                                     key: str) -> bool:
    pv = prev.get(key)
    cv = current.get(key)
    pv_down = pv.get("downgraded") if isinstance(pv, dict) else None
    cv_down = cv.get("downgraded") if isinstance(cv, dict) else None
    return pv_down is not True and cv_down is True


def _guardrail_fired_appeared(prev: dict[str, Any], current: dict[str, Any]) -> bool:
    pv = prev.get("guardrail_fired")
    cv = current.get("guardrail_fired")
    pv_empty = not pv or (isinstance(pv, (list, tuple)) and len(pv) == 0)
    cv_present = isinstance(cv, (list, tuple)) and len(cv) > 0
    return pv_empty and cv_present


def _llm_degraded_flipped_true(prev: dict[str, Any], current: dict[str, Any]) -> bool:
    return prev.get("llm_degraded_mode") is False and current.get("llm_degraded_mode") is True


def _outcome_appeared(prev: dict[str, Any], current: dict[str, Any]) -> bool:
    return prev.get("outcome") is None and current.get("outcome") is not None


def _market_move_significant(prob_delta: dict[str, Any]) -> bool:
    est = prob_delta.get("estimated")
    return isinstance(est, (int, float)) and abs(est) >= _PROBABILITY_MOVE_THRESHOLD


def _rank_driver(
    prev: dict[str, Any],
    current: dict[str, Any],
    direction_changed: bool,
    prob_delta: dict[str, Any],
) -> str:
    # 1. manual_resolution — outcome appeared (resolution event).
    if _outcome_appeared(prev, current):
        return "manual_resolution"
    # 2-3. LLM degraded / guardrail fire (these are explicit downgrades).
    if _llm_degraded_flipped_true(prev, current):
        return "llm_degraded"
    if _guardrail_fired_appeared(prev, current):
        return "guardrail"
    # 4-6. Overlay downgrades (explicit downgrades take precedence over
    # probability drift).
    if _overlay_downgraded_flipped_true(prev, current, "market_quality"):
        return "market_quality"
    if _overlay_downgraded_flipped_true(prev, current, "source_reliability"):
        return "source_conflict"
    if _overlay_downgraded_flipped_true(prev, current, "decision_quality"):
        return "calibration"
    # 7. market_move — probability drifted enough to explain the change.
    if direction_changed and _market_move_significant(prob_delta):
        return "market_move"
    # 8. none — no material change detected.
    return "none"
