"""Shared payload helpers for the prediction-market candidate sources.

``limitless_event_source``, ``opinion_event_source`` and
``predict_fun_event_source`` all turn a provider payload into the same
candidate-event shape. The five helpers below used to be byte-identical copies
inside each adapter -- 14 definitions across the three files -- so the
probability *scale* contract (a 0-1 fraction becomes 0-100 points) lived in
three places and had to be fixed three times. A drift check found the copies
had not yet diverged, but the field lists feeding them had already grown
accepted-but-untested entries. Converging the helpers means a fix lands once.

Scope is deliberately narrow -- only what these adapters genuinely share.

* ``extract_market_list`` handles a bare ``list`` or ``{"data": [...]}``. The
  Opinion adapter reads ``{"result": {"list": [...]}}`` instead, which is a
  *different* provider contract, so its own ``_extract_market_list`` stays local.
* ``normalize_probability`` and ``clean_number`` belong together: the former
  calls the latter, so splitting them across files would only recreate the
  "changed one, missed the other" surface.

``tests/test_event_source_utils.py`` pins the adapters to these objects by
identity, so a copy cannot silently reappear.
"""

from typing import Any

from app.utils.market_utils import safe_float

__all__ = [
    "clean_number",
    "extract_market_list",
    "extract_number",
    "extract_text",
    "normalize_probability",
]


def normalize_probability(raw: Any) -> float | None:
    """Coerce a provider probability into the 0-100 point scale.

    ``0-1`` is read as a fraction (multiplied by 100); ``0-100`` passes
    through; anything else is rejected with ``None``. A bare number cannot tell
    ``0.2`` meaning 20% from ``0.2`` meaning 0.2% -- the caller's field list is
    the contract that keeps the scale honest.

    The result is not rounded (``0.57 * 100`` is ``56.99999999999999``); the
    adapters round to two decimals when they build the event payload.
    """
    value = safe_float(clean_number(raw), -1.0)
    if 0.0 <= value <= 1.0:
        return value * 100
    if 0.0 <= value <= 100.0:
        return value
    return None


def clean_number(raw: Any) -> Any:
    """Strip currency/percent decoration so ``safe_float`` can parse the value."""
    if isinstance(raw, str):
        return raw.replace("$", "").replace(",", "").replace("%", "").strip()
    return raw


def extract_text(market: dict[str, Any], fields: tuple[str, ...]) -> str:
    """Return the first non-blank field value as a stripped string."""
    for field in fields:
        value = str(market.get(field, "") or "").strip()
        if value:
            return value
    return ""


def extract_number(market: dict[str, Any], fields: tuple[str, ...]) -> float:
    """Return the first *present* field coerced to float, else ``0.0``."""
    for field in fields:
        if market.get(field) is not None:
            return safe_float(clean_number(market.get(field)), 0.0)
    return 0.0


def extract_market_list(data: Any) -> list[dict[str, Any]]:
    """Coerce a provider payload into a list of markets.

    Accepts a bare list or ``{"data": [...]}``; anything else yields ``[]``.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    value = data.get("data")
    if isinstance(value, list):
        return value
    return []
