"""World Cup prediction engines package.

Provides unified registry for engine dispatch.
"""

from typing import Callable
from app.services.world_cup_engines.world_cup_prediction_engine import predict_match_score
from app.services.world_cup_engines.world_cup_elo_odds_engine import predict_match_elo_odds


# Engine registry: maps engine name to prediction function
ENGINES: dict[str, Callable] = {
    "hybrid": predict_match_score,
    "elo_odds": predict_match_elo_odds,
}


def get_engine(name: str) -> Callable:
    """Get prediction engine function by name.

    Args:
        name: Engine name ("hybrid" or "elo_odds")

    Returns:
        Engine function

    Raises:
        KeyError: If engine name not registered
    """
    if name not in ENGINES:
        raise KeyError(f"Unknown engine: {name}. Available: {list(ENGINES.keys())}")
    return ENGINES[name]


__all__ = ["ENGINES", "get_engine"]


