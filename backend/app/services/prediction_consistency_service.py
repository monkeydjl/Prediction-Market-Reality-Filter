# backend/app/services/prediction_consistency_service.py
"""Read-only census: does a prediction's scoreline name the same winner as its
probabilities?

Every stored prediction publishes two independent claims about who wins:
``predicted_scores`` (a scoreline) and ``outcome_probabilities``.  Nothing in
the repo compared them, and for three of the engines they are computed from
*different evidence*, so they can disagree:

- ``elo_odds``/``dixon_coles``/football derive the scoreline **from the fused
  probabilities** (``_probabilities_to_scores``), so the two agree by
  construction.
- ``basketball``/``baseball``/``hockey`` derive it from the raw Elo pair alone
  (``margin = (elo_home - elo_away + hfa) * coef``).  Elo carries 25.5% (mlb),
  33.5% (nba) and 35.0% (nhl) of each engine's own weighted vote, so 65-75% of
  the evidence that moves ``outcome_probabilities`` cannot move the scoreline.

Measured on the live corpus (2026-08-30, 16,090 rated fixtures in
``kernel_match_fixtures``): the published scoreline can name the opposite
winner from the fusion on 39.5% of nba, 99.4% of mlb and 51.5% of nhl
fixtures -- 11,500 of 16,090 (71.5%).  Confirmed behaviourally against the
real ``HockeyEngine``: holding Elo at 1500/1500 and flipping only the non-Elo
evidence leaves ``predicted_scores`` byte-identical at 3.4-2.1 (home ahead)
while ``outcome_probabilities`` moves to 0.4794/0.5206 (away ahead).

Both fields are consumed: the scoreline is rendered, fed to
``soft_totals_from_scores`` for the over/under recommendation, and graded as
``score_mae`` by ``learning_service.compute_error`` -- which builds the *same*
``PredictionError`` row's ``outcome_correct`` and ``brier_score`` from the
probabilities.  On a disagreeing fixture those metrics grade contradictory
claims, and ``mae`` is one of the optimizer's objectives.

This module only reports.  It changes no engine formula, weight or contract.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: A prediction's two claims name the same side.
VERDICT_CONSISTENT = "consistent"
#: One field names home, the other names away.  The defect this module exists for.
VERDICT_CONTRADICTS = "contradicts"
#: The scoreline is exactly level, so it names no winner while the
#: probabilities do.  Reachable whenever Elo is absent: the margin is then
#: exactly 0.0 regardless of what the other factors voted.
VERDICT_SCORE_EVEN = "score_even"
#: Could not evaluate.  A check has three outcomes, not two -- an unreadable
#: payload must never be counted as agreement.
VERDICT_UNREADABLE = "unreadable"

#: Worst first.  ``_worst_verdict`` scans this in order.
_VERDICT_SEVERITY = (
    VERDICT_CONTRADICTS,
    VERDICT_UNREADABLE,
    VERDICT_SCORE_EVEN,
    VERDICT_CONSISTENT,
)

#: Reported when an engine in ``ELO_ONLY_SCORE_ENGINES`` has no stored rows.
#: Seeding the per-engine report from the declared scope keeps an engine whose
#: predictions are simply absent from reading as healthy.
STATUS_NO_PREDICTIONS = "no_predictions"

WINNER_HOME = "home"
WINNER_AWAY = "away"
WINNER_DRAW = "draw"
WINNER_EVEN = "even"

#: Engines whose ``predicted_scores`` come from the Elo pair alone and are
#: therefore blind to every other factor they themselves fused.  Declared as
#: data so the behavioural tests can pin it: if one of these is changed to
#: derive its scoreline from the fused probabilities, its test goes red and
#: this set is what needs updating.
ELO_ONLY_SCORE_ENGINES: frozenset[str] = frozenset(
    {"basketball", "baseball", "hockey"}
)

#: Cap on the worked examples returned with the census, so the payload stays
#: bounded no matter how many rows are stored.
_SAMPLE_LIMIT = 25

__all__ = [
    "ELO_ONLY_SCORE_ENGINES",
    "STATUS_NO_PREDICTIONS",
    "VERDICT_CONSISTENT",
    "VERDICT_CONTRADICTS",
    "VERDICT_SCORE_EVEN",
    "VERDICT_UNREADABLE",
    "WINNER_AWAY",
    "WINNER_DRAW",
    "WINNER_EVEN",
    "WINNER_HOME",
    "collect_prediction_consistency",
    "consistency_verdict",
    "probability_winner",
    "score_winner",
]


def _finite(value: Any) -> float | None:
    """Return ``value`` as a finite float, or None when it is not one.

    ``bool`` is rejected: ``True`` is a valid float in Python and a score of
    ``True`` is a corrupt payload, not a 1-0 win.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def score_winner(scores: Any) -> str | None:
    """Which side the published scoreline names, or None if unreadable.

    Returns ``WINNER_HOME``, ``WINNER_AWAY`` or ``WINNER_EVEN``.  The scoreline
    is compared exactly as stored -- the census's job is to check what the
    system actually published, not a re-derived value.
    """
    if not isinstance(scores, Mapping):
        return None
    home = _finite(scores.get("home"))
    away = _finite(scores.get("away"))
    if home is None or away is None:
        return None
    if home > away:
        return WINNER_HOME
    if away > home:
        return WINNER_AWAY
    return WINNER_EVEN


def probability_winner(probs: Any) -> str | None:
    """Which side the probabilities name, or None if unreadable.

    Handles both the binary (``home_win``/``away_win``) and the football 3-way
    (plus ``draw``) payloads.  A tie between the top two returns
    ``WINNER_EVEN``; a ``draw`` argmax returns ``WINNER_DRAW``.
    """
    if not isinstance(probs, Mapping):
        return None
    home = _finite(probs.get("home_win"))
    away = _finite(probs.get("away_win"))
    if home is None or away is None:
        return None
    draw = _finite(probs.get("draw"))
    ranked = [(home, WINNER_HOME), (away, WINNER_AWAY)]
    if draw is not None:
        ranked.append((draw, WINNER_DRAW))
    best = max(value for value, _ in ranked)
    winners = [name for value, name in ranked if value == best]
    if len(winners) > 1:
        return WINNER_EVEN
    return winners[0]


#: The two verdicts that mean "the fields name opposite sides".
_OPPOSITE = frozenset({(WINNER_HOME, WINNER_AWAY), (WINNER_AWAY, WINNER_HOME)})


def consistency_verdict(scores: Any, probs: Any) -> tuple[str, str | None]:
    """Compare one prediction's two claims.  Returns ``(verdict, problem)``.

    ``problem`` is a human-readable sentence when the verdict is not
    ``VERDICT_CONSISTENT``, else None.  Never raises.
    """
    from_score = score_winner(scores)
    from_probs = probability_winner(probs)
    if from_score is None or from_probs is None:
        missing = []
        if from_score is None:
            missing.append("predicted_scores")
        if from_probs is None:
            missing.append("outcome_probabilities")
        return VERDICT_UNREADABLE, f"could not read {' and '.join(missing)}"
    if (from_score, from_probs) in _OPPOSITE:
        return (
            VERDICT_CONTRADICTS,
            f"scoreline names {from_score} but probabilities name {from_probs}",
        )
    if from_score == WINNER_EVEN and from_probs != WINNER_EVEN:
        return (
            VERDICT_SCORE_EVEN,
            f"scoreline is level so it names no winner while "
            f"probabilities name {from_probs}",
        )
    return VERDICT_CONSISTENT, None


def _worst_verdict(verdicts: list[str]) -> str:
    for candidate in _VERDICT_SEVERITY:
        if candidate in verdicts:
            return candidate
    return VERDICT_CONSISTENT


def _empty_tally() -> dict[str, int]:
    return {verdict: 0 for verdict in _VERDICT_SEVERITY}


def collect_prediction_consistency(*, now: datetime | None = None) -> dict[str, Any]:
    """Census every stored prediction's two claims about who wins.

    Read-only: opens a kernel session, reads ``kernel_predictions``, writes
    nothing.  The per-engine block is seeded from ``ELO_ONLY_SCORE_ENGINES`` so
    an engine with no stored rows reports ``no_predictions`` rather than being
    absent from the report and reading as healthy.
    """
    from app.kernel.kernel_db import KernelPrediction, get_kernel_session

    stamp = (now or datetime.now(timezone.utc)).isoformat()
    engines: dict[str, dict[str, Any]] = {
        name: {
            "predictions": 0,
            "verdicts": _empty_tally(),
            "elo_only_scores": True,
            "status": STATUS_NO_PREDICTIONS,
        }
        for name in sorted(ELO_ONLY_SCORE_ENGINES)
    }
    overall = _empty_tally()
    problems: list[str] = []
    samples: list[dict[str, Any]] = []

    session = get_kernel_session()
    try:
        rows = list(session.query(KernelPrediction).all())
    except Exception as exc:  # noqa: BLE001 - a census must not take the route down
        logger.warning("Prediction consistency census could not read rows: %s", exc)
        return {
            "generated_at": stamp,
            "total_predictions": 0,
            "verdicts": overall,
            "status": VERDICT_UNREADABLE,
            "problems": [f"could not read kernel_predictions: {exc}"],
            "engines": engines,
            "contradicting_samples": [],
        }
    finally:
        session.close()

    for row in rows:
        verdict, problem = consistency_verdict(
            row.predicted_scores, row.outcome_probabilities
        )
        overall[verdict] += 1
        block = engines.setdefault(
            row.engine,
            {
                "predictions": 0,
                "verdicts": _empty_tally(),
                "elo_only_scores": row.engine in ELO_ONLY_SCORE_ENGINES,
                "status": STATUS_NO_PREDICTIONS,
            },
        )
        block["predictions"] += 1
        block["verdicts"][verdict] += 1
        if problem is not None and verdict != VERDICT_CONSISTENT:
            problems.append(f"{row.match_id} ({row.engine}): {problem}")
        if verdict == VERDICT_CONTRADICTS and len(samples) < _SAMPLE_LIMIT:
            samples.append(
                {
                    "match_id": row.match_id,
                    "engine": row.engine,
                    "competition": row.competition,
                    "predicted_scores": row.predicted_scores,
                    "outcome_probabilities": row.outcome_probabilities,
                    "scoreline_names": score_winner(row.predicted_scores),
                    "probabilities_name": probability_winner(
                        row.outcome_probabilities
                    ),
                }
            )

    for block in engines.values():
        if block["predictions"] == 0:
            continue
        tally = block["verdicts"]
        block["status"] = _worst_verdict(
            [verdict for verdict, count in tally.items() if count]
        )

    total = len(rows)
    status = (
        STATUS_NO_PREDICTIONS
        if total == 0
        else _worst_verdict([v for v, count in overall.items() if count])
    )
    return {
        "generated_at": stamp,
        "total_predictions": total,
        "verdicts": overall,
        "status": status,
        "problems": problems,
        "engines": engines,
        "contradicting_samples": samples,
    }
