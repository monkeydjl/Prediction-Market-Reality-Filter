"""The single place a final score becomes a stored ``outcome`` token.

Two rules, one function. Basketball, baseball and hockey rows in
``kernel_match_results`` carry ``home_win``/``away_win`` only -- those leagues
play to a decision, so a level full-time score is not a reachable outcome.
Football does draw, and 287 of the 1181 finished football fixtures in the live
kernel (24.3%: epl 104, bundesliga 75, ligue1 75, ucl 33) are level.

Before P1-E9 this rule lived in ``_binary_outcome`` inside
``historical_data_ingestor``, whose docstring named the three binary sports and
whose body had no draw branch::

    return "home_win" if home_score > away_score else "away_win"

Reaching it with a football score does not raise or return ``None``; it returns
``"away_win"`` for a 1-1. That is why the football competitions could not simply
be added to the ingestor's sport list, and why the draw rule is a declared
parameter here rather than a property of the caller's own code path.

``allow_draw`` is required and keyword-only: the two call sites disagree about
it, so a positional third argument would let one of them silently inherit the
other's rule.
"""
from __future__ import annotations

#: Stored when the home side scored more. Same token in every competition.
OUTCOME_HOME_WIN = "home_win"
#: Stored when the away side scored more.
OUTCOME_AWAY_WIN = "away_win"
#: Stored for a level score, and only where the competition declares draws.
OUTCOME_DRAW = "draw"


def outcome_from_scores(
    home_score: int,
    away_score: int,
    *,
    allow_draw: bool,
) -> str:
    """Map a final score to the ``kernel_match_results.outcome`` token.

    Args:
        home_score: Full-time goals/points for the fixture's home side.
        away_score: Full-time goals/points for the fixture's away side.
        allow_draw: True only for competitions that can end level. When False a
            level score is treated the way the binary sports' replay treats it
            -- as an away win -- because ``seed_elo_from_games`` scores
            ``home_score > away_score`` and has no third bucket. Passing True
            for a binary sport would write a token its Elo replay cannot read;
            passing False for football stores 24.3% of results as the wrong
            side winning.

    Returns:
        One of ``home_win`` / ``draw`` / ``away_win``. ``draw`` is returned only
        when ``allow_draw`` is True.
    """
    if home_score > away_score:
        return OUTCOME_HOME_WIN
    if home_score < away_score:
        return OUTCOME_AWAY_WIN
    return OUTCOME_DRAW if allow_draw else OUTCOME_AWAY_WIN
