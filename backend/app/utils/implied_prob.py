"""Implied probability conversion utilities.

Polymarket prices are already 0-1 probability expressions. The Odds API
returns decimal odds which must be inverted (1/odds) and normalized to
remove the sportsbook vigorish (overround).
"""
from __future__ import annotations


def polymarket_to_implied(yes_price: float, no_price: float) -> tuple[float, float, float]:
    """Convert Polymarket YES/NO prices to (yes_implied, no_implied, spread).

    Price is already 0-1. YES+NO > 1.0 portion is the spread (recorded but
    not adjusted — Sub-project B decides whether to adjust).
    """
    yes_implied = yes_price
    no_implied = no_price
    spread = yes_price + no_price - 1.0
    return (yes_implied, no_implied, spread)


def odds_api_to_implied(decimal_odds_list: list[float]) -> list[float]:
    """Convert decimal odds to implied probabilities, normalized to remove vigorish.

    Each raw implied prob is 1/decimal_odds; the raw sum exceeds 1.0 due to
    overround, so we divide each by the sum to normalize.

    The returned list is always the same length as the input, and index i always
    corresponds to ``decimal_odds_list[i]``. Callers zip the result against
    outcome labels by position, so dropping an entry would silently attach a
    probability to the wrong outcome. Non-positive odds (no price / bad feed
    value) therefore yield 0.0 at that index rather than being filtered out;
    the remaining entries still normalize to 1.0.
    """
    raw = [(1.0 / d if d > 0 else 0.0) for d in decimal_odds_list]
    total = sum(raw)
    if total == 0:
        return [0.0] * len(decimal_odds_list)
    return [r / total for r in raw]
