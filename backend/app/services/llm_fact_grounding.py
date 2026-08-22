"""Fact grounding for the World Cup LLM prompts (P2-W4).

The analysis and optimization prompts hand the model a handful of structured
numbers and then ask open questions ("key factors", "blind spots"). Nothing in
them forbade asserting a statistic that was never supplied, and the analysis
prompt actively asked for reasoning "based on probabilities and Elo/data" while
its only caller passed no Elo at all. A model answering that honestly has to
invent: card counts, injury lists, possession, xG and head-to-head records are
the usual inventions, and the analysis text is rendered verbatim to the operator
by `prediction-analysis-card.tsx`.

This module builds the two halves of the constraint in one place: the facts the
model actually holds, and an explicit statement of the fact kinds it does *not*
hold, with rules forbidding it from filling those gaps.
"""

from collections.abc import Mapping

# Fact kinds a football model will produce unprompted unless it is told it does
# not have them. The label carries the Chinese term as well, because the model
# answers in Chinese and an English-only prohibition is easy to slip past.
INVENTABLE_FACT_KINDS: tuple[tuple[str, str], ...] = (
    ("cards", "yellow/red card counts (红黄牌)"),
    ("injuries", "injury or suspension lists (伤停名单)"),
    ("lineups", "starting lineups (首发阵容)"),
    ("recent_form", "recent results or form (近期战绩)"),
    ("head_to_head", "head-to-head records (历史对战)"),
    ("possession", "possession or shot counts (控球/射门数)"),
    ("expected_goals", "expected goals / xG"),
    ("elo_ratings", "Elo ratings (Elo 评分)"),
    ("betting_odds", "betting odds or market lines (赔率/盘口)"),
    ("weather", "weather or pitch conditions (天气/场地)"),
    ("player_stats", "individual player statistics (球员个人数据)"),
)

_LABELS: dict[str, str] = {
    "cards": "Cards",
    "injuries": "Injuries",
    "lineups": "Lineups",
    "recent_form": "Recent form",
    "head_to_head": "Head-to-head",
    "possession": "Possession/shots",
    "expected_goals": "Expected goals (xG)",
    "elo_ratings": "Elo ratings",
    "betting_odds": "Betting odds",
    "weather": "Weather/pitch",
    "player_stats": "Player statistics",
    "stage": "Stage",
    "group": "Group",
    "venue": "Venue",
    "data_quality": "Input data quality",
    "key_factors": "Engine key factors",
}

_RULES = (
    "Hard rules (these override every instruction above):\n"
    "1. Every count, score, rating, record and name you state must appear in the "
    "prediction numbers or the fact list above. Treat that as the complete set "
    "of facts you hold about this match.\n"
    "2. If a point you want to make needs one of the facts you were not given, "
    "write 「该项数据未提供」 and move on. Do not estimate it, do not "
    "derive it from the probabilities, and do not describe a typical match as if "
    "it were this one.\n"
    "3. Do not say the prediction was built from any data source that is not "
    "named above."
)


def _label(key: str) -> str:
    return _LABELS.get(key, key.replace("_", " ").capitalize())


def _is_supplied(value: object) -> bool:
    """An empty value is not a supplied fact.

    Callers pass their optional fields unconditionally, so `None`, `""` and an
    empty list/dict all have to count as absent - otherwise an engine that
    stored `elo_ratings: {}` would take Elo off the "you were not given" list
    while putting nothing in its place.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def render_fact_value(value: object) -> str:
    """Flatten one fact value onto a single prompt line."""
    if isinstance(value, Mapping):
        return ", ".join(f"{key} {val}" for key, val in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


def build_fact_grounding_section(facts: Mapping[str, object]) -> str:
    """Render the supplied facts, the missing fact kinds, and the hard rules.

    Every key of `INVENTABLE_FACT_KINDS` carrying a non-empty value is dropped
    from the "you were NOT given" list, so a caller cannot render a fact and
    still have the model told it lacks that fact. Keys outside that vocabulary
    (stage, venue, data_quality, ...) are rendered as facts and change nothing
    about the missing list.
    """
    supplied = {key: value for key, value in facts.items() if _is_supplied(value)}

    if supplied:
        lines = "\n".join(
            f"- {_label(key)}: {render_fact_value(value)}" for key, value in supplied.items()
        )
        fact_block = (
            "Facts provided to you, beyond the prediction numbers above:\n" + lines
        )
    else:
        fact_block = (
            "No facts beyond the prediction numbers above were provided to you."
        )

    missing = [label for key, label in INVENTABLE_FACT_KINDS if key not in supplied]
    parts = [fact_block]
    if missing:
        parts.append(
            "You were NOT given, and therefore do not know: " + "; ".join(missing) + "."
        )
    parts.append(_RULES)
    return "\n\n".join(parts)
