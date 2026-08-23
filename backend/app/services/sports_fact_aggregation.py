"""Count each real-world occurrence once when aggregating sports facts.

Settlement used to add up ``red_cards`` across every fact kind that carries the
field. The same red card reaches the fact store at up to three grains:

- one ``discipline`` fact per card event (``world_cup_match_events_source``, or
  the ``discipline`` rows of a data-source bundle),
- the per-match total on the ``match_result`` / ``match_state`` fact, which the
  same bundle builds from ``home_red_cards`` + ``away_red_cards``,
- a tournament-wide total, if an operator attaches one to ``tournament_status``.

Those grains describe the same cards, and their ``fact_id`` values differ by
construction, so the store's upsert cannot merge them. A bundle carrying both
match totals and per-card rows therefore reported twice the real number, and
"at least 8 red cards" resolved YES at confidence 100 on four actual cards.

Goals have the same exposure at one grain: a match imported from two sources, or
re-imported at a later ``observed_at``, gets two ``match_result`` facts (the
generated ``fact_id`` seeds on ``source`` and ``observed_at``), and both were
summed.

The rule here is to pick a grain per match rather than add grains together.
Within a single match both counts are monotonic - cards and goals are never
taken back - so ``max`` is the right reducer for repeated observations of one
match, and it does not depend on ``observed_at`` being present or ordered.
"""

from __future__ import annotations

from typing import Any


# One fact per card event: the finest grain, already deduplicated by fact_id.
_PER_CARD_KINDS = frozenset({"discipline"})
# One fact per match, carrying that match's totals.
_PER_MATCH_KINDS = frozenset({"match_state", "match_result"})
# A single fact covering the whole tournament.
_TOURNAMENT_KINDS = frozenset({"tournament_status"})


def red_card_total(facts: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Total red cards, counting each card once, with the facts that carried it.

    ``suspension`` facts are deliberately not counted even though an operator
    could attach ``red_cards`` to one: a suspension is a consequence of a card
    that another fact already reports, so adding it is the same double count.
    """

    return _aggregate(facts, _red_cards_of)


def total_goals(facts: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Total goals across matches, counting each match once.

    Only per-match facts carry a score, so this ignores the per-card grain.
    """

    return _aggregate(facts, _goals_of, per_card_kinds=frozenset())


def _aggregate(
    facts: list[dict[str, Any]],
    value_of: Any,
    *,
    per_card_kinds: frozenset[str] = _PER_CARD_KINDS,
) -> tuple[float, list[dict[str, Any]]]:
    # match_id -> summed value of the per-card facts for that match
    per_card: dict[str, float] = {}
    per_card_facts: dict[str, list[dict[str, Any]]] = {}
    # match_id -> best per-match aggregate seen for that match
    per_match: dict[str, float] = {}
    per_match_facts: dict[str, dict[str, Any]] = {}
    # Facts we cannot attribute to a match, and so cannot reconcile: one each.
    unattributed_total = 0.0
    unattributed_facts: list[dict[str, Any]] = []
    # A tournament-wide total already includes every match.
    tournament_total = 0.0
    tournament_facts: list[dict[str, Any]] = []

    for fact in facts:
        kind = str(fact.get("kind") or "")
        value = value_of(fact)
        if value is None:
            continue
        if kind in _TOURNAMENT_KINDS:
            if value > tournament_total:
                tournament_total = value
                tournament_facts = [fact]
            continue
        if kind not in per_card_kinds and kind not in _PER_MATCH_KINDS:
            continue
        match_id = str(fact.get("match_id") or "")
        if not match_id:
            unattributed_total += value
            unattributed_facts.append(fact)
            continue
        if kind in per_card_kinds:
            per_card[match_id] = per_card.get(match_id, 0.0) + value
            per_card_facts.setdefault(match_id, []).append(fact)
        elif value > per_match.get(match_id, -1.0):
            per_match[match_id] = value
            per_match_facts[match_id] = fact

    matched_total = 0.0
    evidence: list[dict[str, Any]] = []
    for match_id in sorted(set(per_card) | set(per_match)):
        card_sum = per_card.get(match_id, 0.0)
        match_value = per_match.get(match_id, 0.0)
        # Only prefer the per-card grain when cards were actually reported at
        # it: a 0-0 match has a per-match fact worth citing and no card rows,
        # and both values are 0.
        if match_id in per_card and card_sum >= match_value:
            matched_total += card_sum
            evidence.extend(per_card_facts.get(match_id, []))
        else:
            matched_total += match_value
            evidence.append(per_match_facts[match_id])

    per_match_grain = matched_total + unattributed_total
    # `tournament_facts` rather than `tournament_total > 0`: with no
    # tournament-wide fact at all the total is 0, which would win the tie
    # against a genuine 0-0 match and drop its evidence.
    if tournament_facts and tournament_total >= per_match_grain:
        return tournament_total, tournament_facts
    return per_match_grain, evidence + unattributed_facts


def _red_cards_of(fact: dict[str, Any]) -> float | None:
    return _non_negative(fact.get("red_cards"))


def _goals_of(fact: dict[str, Any]) -> float | None:
    raw_score = fact.get("score")
    score: dict[str, Any] = raw_score if isinstance(raw_score, dict) else {}
    home = _non_negative(
        fact.get("home_goals") if fact.get("home_goals") is not None
        else fact.get("home_score") if fact.get("home_score") is not None
        else score.get("home")
    )
    away = _non_negative(
        fact.get("away_goals") if fact.get("away_goals") is not None
        else fact.get("away_score") if fact.get("away_score") is not None
        else score.get("away")
    )
    if home is None and away is None:
        return None
    return (home or 0.0) + (away or 0.0)


def _non_negative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number
