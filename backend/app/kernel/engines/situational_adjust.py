"""Soft situational probability adjustments (P1-E8).

Turns tournament context (knockout, must-win, group status) into small,
renormalized probability shifts — not fixed percentage-point hardcodes.

Inputs (FeatureSet.custom and MatchIdentity.stage):
- stage in knockout set → both sides "must avoid draw"
- custom.must_win_home / must_win_away (bool or 0/1)
- custom.home_pressure / away_pressure: "must_win" | "low_motivation" |
  "rotation_risk" | "protect_position" | "normal"
- custom.home_group_status / away_group_status: "qualified" | "eliminated" | ...

All multiplicative deltas are capped so a single context never dominates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_KNOCKOUT_STAGES = frozenset({
    "round_of_16", "quarterfinal", "quarter_final",
    "semifinal", "semi_final", "final", "third_place",
})

# Soft relative lifts (applied then renormalized). Keep modest.
_MUST_WIN_OWN = 1.06
_MUST_WIN_OPP_COUNTER = 1.02
_BOTH_MUST_WIN_DRAW = 0.88
_KNOCKOUT_DRAW = 0.90
_LOW_MOTIVATION_OWN = 0.94
_ROTATION_OWN = 0.96
_PROTECT_DRAW_UP = 1.04

_MAX_RELATIVE_SHIFT = 0.12  # after renorm, max |Δp| for any outcome


@dataclass(frozen=True)
class SituationalContext:
    is_knockout: bool
    must_win_home: bool
    must_win_away: bool
    home_pressure: str
    away_pressure: str
    home_status: str
    away_status: str
    notes: tuple[str, ...]


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _pressure(custom: dict[str, Any], side: str) -> str:
    key = f"{side}_pressure"
    raw = custom.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    # Infer from must_win flags / status
    status = str(custom.get(f"{side}_group_status") or "").lower()
    if status == "qualified":
        return "rotation_risk"
    if status == "eliminated":
        return "low_motivation"
    must = _as_bool(custom.get(f"must_win_{side}"))
    if must:
        return "must_win"
    return "normal"


def extract_situational_context(
    stage: str | None,
    custom: dict[str, Any] | None,
) -> SituationalContext:
    custom = custom or {}
    stage_n = (stage or "").lower().strip().replace(" ", "_")
    is_knockout = stage_n in _KNOCKOUT_STAGES

    must_h = _as_bool(custom.get("must_win_home"))
    must_a = _as_bool(custom.get("must_win_away"))
    # Knockout → both must win (no draw advances both)
    if is_knockout:
        must_h = True
        must_a = True

    home_p = _pressure(custom, "home")
    away_p = _pressure(custom, "away")
    if is_knockout:
        home_p = "must_win"
        away_p = "must_win"
    if must_h and home_p == "normal":
        home_p = "must_win"
    if must_a and away_p == "normal":
        away_p = "must_win"

    home_st = str(custom.get("home_group_status") or "").lower()
    away_st = str(custom.get("away_group_status") or "").lower()

    notes: list[str] = []
    if is_knockout:
        notes.append(f"knockout:{stage_n or 'unknown'}")
    if must_h:
        notes.append("must_win_home")
    if must_a:
        notes.append("must_win_away")
    if home_p not in {"", "normal"}:
        notes.append(f"home_pressure={home_p}")
    if away_p not in {"", "normal"}:
        notes.append(f"away_pressure={away_p}")
    if home_st:
        notes.append(f"home_status={home_st}")
    if away_st:
        notes.append(f"away_status={away_st}")

    return SituationalContext(
        is_knockout=is_knockout,
        must_win_home=must_h,
        must_win_away=must_a,
        home_pressure=home_p,
        away_pressure=away_p,
        home_status=home_st,
        away_status=away_st,
        notes=tuple(notes),
    )


def _multipliers(ctx: SituationalContext) -> dict[str, float]:
    m = {"home_win": 1.0, "draw": 1.0, "away_win": 1.0}

    if ctx.is_knockout:
        m["draw"] *= _KNOCKOUT_DRAW

    if ctx.must_win_home and ctx.must_win_away:
        m["home_win"] *= _MUST_WIN_OWN
        m["away_win"] *= _MUST_WIN_OWN
        m["draw"] *= _BOTH_MUST_WIN_DRAW
    elif ctx.must_win_home:
        m["home_win"] *= _MUST_WIN_OWN
        m["away_win"] *= _MUST_WIN_OPP_COUNTER
        m["draw"] *= 0.96
    elif ctx.must_win_away:
        m["away_win"] *= _MUST_WIN_OWN
        m["home_win"] *= _MUST_WIN_OPP_COUNTER
        m["draw"] *= 0.96

    if ctx.home_pressure == "low_motivation" or ctx.home_status == "eliminated":
        m["home_win"] *= _LOW_MOTIVATION_OWN
    if ctx.away_pressure == "low_motivation" or ctx.away_status == "eliminated":
        m["away_win"] *= _LOW_MOTIVATION_OWN
    if ctx.home_pressure == "rotation_risk" or ctx.home_status == "qualified":
        m["home_win"] *= _ROTATION_OWN
    if ctx.away_pressure == "rotation_risk" or ctx.away_status == "qualified":
        m["away_win"] *= _ROTATION_OWN
    if ctx.home_pressure == "protect_position" or ctx.away_pressure == "protect_position":
        m["draw"] *= _PROTECT_DRAW_UP

    return m


def apply_situational_adjustment(
    probs: dict[str, float],
    ctx: SituationalContext,
) -> tuple[dict[str, float], bool]:
    """Return (adjusted_probs, applied). applied=False if no context notes."""
    if not ctx.notes:
        return {
            "home_win": float(probs.get("home_win", 0.4)),
            "draw": float(probs.get("draw", 0.3)),
            "away_win": float(probs.get("away_win", 0.3)),
        }, False

    base = {
        "home_win": max(1e-6, float(probs.get("home_win", 0.4))),
        "draw": max(1e-6, float(probs.get("draw", 0.3))),
        "away_win": max(1e-6, float(probs.get("away_win", 0.3))),
    }
    mult = _multipliers(ctx)
    raw = {k: base[k] * mult[k] for k in base}
    total = sum(raw.values())
    adjusted = {k: raw[k] / total for k in raw}

    # Cap absolute shift so context never flips a strong favorite alone
    capped: dict[str, float] = {}
    for k in base:
        b = base[k] / sum(base.values())
        a = adjusted[k]
        delta = a - b
        if abs(delta) > _MAX_RELATIVE_SHIFT:
            a = b + (_MAX_RELATIVE_SHIFT if delta > 0 else -_MAX_RELATIVE_SHIFT)
        capped[k] = max(1e-6, a)
    t2 = sum(capped.values())
    out = {k: round(capped[k] / t2, 4) for k in capped}
    # Fix float drift
    s = sum(out.values())
    if abs(s - 1.0) > 1e-6:
        out["draw"] = round(out["draw"] + (1.0 - s), 4)
    return out, True


def situational_summary(ctx: SituationalContext, applied: bool) -> str:
    if not applied:
        return "no situational context"
    return "soft adj: " + ", ".join(ctx.notes)
