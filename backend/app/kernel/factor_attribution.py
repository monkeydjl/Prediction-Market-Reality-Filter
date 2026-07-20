"""Factor attribution helpers for model-vs-market disagreement (P1-V3)."""
from __future__ import annotations

from typing import Any


_OUTCOME_KEYS = ("home_win", "draw", "away_win", "home", "away")


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    # dataclass / object with attributes
    out: dict[str, Any] = {}
    for k in ("factor", "weight", "available", "detail", "direction", "value"):
        if hasattr(item, k):
            out[k] = getattr(item, k)
    # ContributionItem often has home_win/draw/away_win or contribution
    for k in _OUTCOME_KEYS:
        if hasattr(item, k):
            out[k] = getattr(item, k)
    if hasattr(item, "probs") and isinstance(item.probs, dict):
        out.update(item.probs)
    if hasattr(item, "outcome_probabilities") and isinstance(
        item.outcome_probabilities, dict
    ):
        out.update(item.outcome_probabilities)
    return out


def extract_factor_drivers(
    explanation: list | None,
    mapped_outcome: str,
    *,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Rank available factors by impact on mapped_outcome vs neutral.

    For 3-way items, impact ≈ weight * (p_outcome - 1/n_outcomes).
    For binary home-edge style (only home_win-like), use signed edge * weight.
    Returns list of {factor, weight, impact, available, detail}.
    """
    if not explanation:
        return []

    items: list[dict[str, Any]] = []
    for raw in explanation:
        d = _as_dict(raw)
        name = d.get("factor") or d.get("name") or d.get("id")
        if not name or name == "_meta":
            continue
        available = d.get("available", True)
        if available is False:
            continue
        try:
            w = float(d.get("weight") or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        if w <= 0:
            continue

        # Resolve outcome probability from item
        p = None
        if mapped_outcome in d and d[mapped_outcome] is not None:
            try:
                p = float(d[mapped_outcome])
            except (TypeError, ValueError):
                p = None
        # binary engines: sometimes only a single p_home
        if p is None and mapped_outcome == "home_win":
            for k in ("home", "p_home", "prob"):
                if d.get(k) is not None:
                    try:
                        p = float(d[k])
                        break
                    except (TypeError, ValueError):
                        pass
        if p is None and mapped_outcome == "away_win":
            for k in ("away", "p_away"):
                if d.get(k) is not None:
                    try:
                        p = float(d[k])
                        break
                    except (TypeError, ValueError):
                        pass

        # parse detail like "H=0.59 D=0.22 A=0.19" or "p=0.55"
        if p is None and isinstance(d.get("detail"), str):
            detail = d["detail"]
            try:
                import re

                hda = {
                    m.group(1).upper(): float(m.group(2))
                    for m in re.finditer(
                        r"([HDA])\s*=\s*([0-9]*\.?[0-9]+)",
                        detail,
                        flags=re.I,
                    )
                }
                if hda:
                    key_map = {
                        "home_win": "H",
                        "draw": "D",
                        "away_win": "A",
                        "home": "H",
                        "away": "A",
                    }
                    letter = key_map.get(mapped_outcome)
                    if letter and letter in hda:
                        p = hda[letter]
            except (TypeError, ValueError):
                p = None
            if p is None and "p=" in detail:
                try:
                    frag = detail.split("p=")[1].split()[0]
                    p = float(frag)
                    if mapped_outcome == "away_win":
                        p = 1.0 - p
                    elif mapped_outcome == "draw":
                        p = None
                except (IndexError, ValueError):
                    p = None

        if p is None:
            continue

        # neutral baseline
        n_ways = 3 if mapped_outcome in ("home_win", "draw", "away_win") else 2
        # if only binary signal and outcome is draw, skip
        if mapped_outcome == "draw" and n_ways == 2:
            continue
        neutral = 1.0 / n_ways
        impact = w * (p - neutral)
        items.append(
            {
                "factor": str(name),
                "weight": round(w, 4),
                "impact": round(impact, 4),
                "outcome_prob": round(p, 4),
                "available": True,
                "detail": d.get("detail"),
            }
        )

    items.sort(key=lambda x: abs(x["impact"]), reverse=True)
    return items[:top_n]


def format_factor_attribution(
    drivers: list[dict[str, Any]],
    *,
    model_higher: bool,
) -> str | None:
    """Chinese one-liner for top drivers."""
    if not drivers:
        return None
    parts = []
    for d in drivers:
        sign = "+" if d["impact"] >= 0 else ""
        parts.append(f"{d['factor']}({sign}{d['impact']:.3f})")
    lean = "支撑模型偏高" if model_higher else "支撑模型偏低/市场偏高"
    return f"主导因子（{lean}）：" + "、".join(parts)
