"""world_cup_event_source.py
=========================
Curated sports-event source for the 2026 FIFA World Cup.

This source does not fetch market prices and deliberately does not use
``source.type = "prediction_market"``. It contributes high-interest, resolvable
World Cup questions to discovery so the regular evidence collection and
probability analysis pipeline can track them. Because there is no market
contract id, the prediction-freeze loop skips these records.
"""

from typing import Any

from app.core.config import settings


_TOURNAMENT = "2026 FIFA World Cup"
_TOURNAMENT_URL = (
    "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026"
)

_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "id": "usa-knockout-stage",
        "question": "Will the United States reach the knockout stage of the 2026 FIFA World Cup?",
        "baseline_probability": 70.0,
        "category": "team_progression",
        "entities": ["United States", "USMNT", _TOURNAMENT],
        "resolution_criteria": (
            "YES if the United States advances out of the group stage into any "
            "knockout round of the 2026 FIFA World Cup."
        ),
        "time_horizon": "2026 FIFA World Cup group stage",
    },
    {
        "id": "mexico-knockout-stage",
        "question": "Will Mexico reach the knockout stage of the 2026 FIFA World Cup?",
        "baseline_probability": 72.0,
        "category": "team_progression",
        "entities": ["Mexico", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Mexico advances out of the group stage into any knockout "
            "round of the 2026 FIFA World Cup."
        ),
        "time_horizon": "2026 FIFA World Cup group stage",
    },
    {
        "id": "canada-knockout-stage",
        "question": "Will Canada reach the knockout stage of the 2026 FIFA World Cup?",
        "baseline_probability": 48.0,
        "category": "team_progression",
        "entities": ["Canada", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Canada advances out of the group stage into any knockout "
            "round of the 2026 FIFA World Cup."
        ),
        "time_horizon": "2026 FIFA World Cup group stage",
    },
    {
        "id": "argentina-semifinal",
        "question": "Will Argentina reach the semifinals of the 2026 FIFA World Cup?",
        "baseline_probability": 35.0,
        "category": "team_progression",
        "entities": ["Argentina", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Argentina qualifies for a semifinal match at the 2026 FIFA "
            "World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup semifinals",
    },
    {
        "id": "brazil-semifinal",
        "question": "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
        "baseline_probability": 38.0,
        "category": "team_progression",
        "entities": ["Brazil", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Brazil qualifies for a semifinal match at the 2026 FIFA "
            "World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup semifinals",
    },
    {
        "id": "england-semifinal",
        "question": "Will England reach the semifinals of the 2026 FIFA World Cup?",
        "baseline_probability": 32.0,
        "category": "team_progression",
        "entities": ["England", _TOURNAMENT],
        "resolution_criteria": (
            "YES if England qualifies for a semifinal match at the 2026 FIFA "
            "World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup semifinals",
    },
    {
        "id": "penalty-shootout",
        "question": "Will any 2026 FIFA World Cup knockout match be decided by a penalty shootout?",
        "baseline_probability": 82.0,
        "category": "match_format",
        "entities": [_TOURNAMENT, "penalty shootout", "knockout stage"],
        "resolution_criteria": (
            "YES if at least one knockout-stage match at the 2026 FIFA World "
            "Cup is decided by kicks from the penalty mark."
        ),
        "time_horizon": "through the 2026 FIFA World Cup final",
    },
    {
        "id": "final-extra-time",
        "question": "Will the 2026 FIFA World Cup final go to extra time?",
        "baseline_probability": 30.0,
        "category": "match_format",
        "entities": [_TOURNAMENT, "final", "extra time"],
        "resolution_criteria": (
            "YES if the 2026 FIFA World Cup final is level after regulation "
            "time and extra time is played."
        ),
        "time_horizon": "2026 FIFA World Cup final",
    },
    {
        "id": "top-scorer-seven-goals",
        "question": "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
        "baseline_probability": 44.0,
        "category": "player_awards",
        "entities": [_TOURNAMENT, "top scorer", "Golden Boot"],
        "resolution_criteria": (
            "YES if the tournament's top goal scorer is credited with 7 or "
            "more goals by the final official standings."
        ),
        "time_horizon": "after the 2026 FIFA World Cup final",
    },
    {
        "id": "red-cards-eight",
        "question": "Will the 2026 FIFA World Cup have at least 8 red cards?",
        "baseline_probability": 55.0,
        "category": "discipline",
        "entities": [_TOURNAMENT, "red cards"],
        "resolution_criteria": (
            "YES if official tournament match reports record 8 or more red "
            "cards across all 2026 FIFA World Cup matches."
        ),
        "time_horizon": "after the 2026 FIFA World Cup final",
    },
    {
        "id": "france-quarterfinal",
        "question": "Will France reach the quarterfinals of the 2026 FIFA World Cup?",
        "baseline_probability": 60.0,
        "category": "team_progression",
        "entities": ["France", _TOURNAMENT],
        "resolution_criteria": (
            "YES if France qualifies for a quarterfinal match at the 2026 "
            "FIFA World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup quarterfinals",
    },
    {
        "id": "total-goals-140",
        "question": "Will the 2026 FIFA World Cup have at least 140 total goals?",
        "baseline_probability": 52.0,
        "category": "tournament_totals",
        "entities": [_TOURNAMENT, "total goals"],
        "resolution_criteria": (
            "YES if 140 or more goals are scored across all 2026 FIFA World "
            "Cup matches by the final official tournament records."
        ),
        "time_horizon": "after the 2026 FIFA World Cup final",
    },
    {
        "id": "argentina-winner",
        "question": "Will Argentina win the 2026 FIFA World Cup?",
        "baseline_probability": 18.0,
        "category": "tournament_winner",
        "entities": ["Argentina", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Argentina wins the 2026 FIFA World Cup final."
        ),
        "time_horizon": "2026 FIFA World Cup final",
    },
    {
        "id": "host-nation-semifinal",
        "question": "Will a host nation reach the semifinals of the 2026 FIFA World Cup?",
        "baseline_probability": 42.0,
        "category": "team_progression",
        "entities": ["United States", "Mexico", "Canada", _TOURNAMENT],
        "resolution_criteria": (
            "YES if the United States, Mexico, or Canada qualifies for a "
            "semifinal match at the 2026 FIFA World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup semifinals",
    },
    {
        "id": "germany-quarterfinal",
        "question": "Will Germany reach the quarterfinals of the 2026 FIFA World Cup?",
        "baseline_probability": 55.0,
        "category": "team_progression",
        "entities": ["Germany", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Germany qualifies for a quarterfinal match at the 2026 "
            "FIFA World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup quarterfinals",
    },
    {
        "id": "spain-quarterfinal",
        "question": "Will Spain reach the quarterfinals of the 2026 FIFA World Cup?",
        "baseline_probability": 58.0,
        "category": "team_progression",
        "entities": ["Spain", _TOURNAMENT],
        "resolution_criteria": (
            "YES if Spain qualifies for a quarterfinal match at the 2026 "
            "FIFA World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup quarterfinals",
    },
    {
        "id": "knockout-underdog-quarterfinal",
        "question": "Will a team ranked outside the FIFA top 16 reach the 2026 World Cup quarterfinals?",
        "baseline_probability": 40.0,
        "category": "team_progression",
        "entities": [_TOURNAMENT, "quarterfinals", "underdog"],
        "resolution_criteria": (
            "YES if any team whose FIFA ranking was outside the top 16 at "
            "the start of the tournament qualifies for a quarterfinal match."
        ),
        "time_horizon": "before the 2026 FIFA World Cup quarterfinals",
    },
    {
        "id": "european-finalist",
        "question": "Will at least one European team reach the 2026 FIFA World Cup final?",
        "baseline_probability": 78.0,
        "category": "team_progression",
        "entities": [_TOURNAMENT, "Europe", "final"],
        "resolution_criteria": (
            "YES if at least one UEFA-member nation plays in the 2026 FIFA "
            "World Cup final."
        ),
        "time_horizon": "2026 FIFA World Cup final",
    },
    {
        "id": "south-american-semifinal",
        "question": "Will at least two South American teams reach the 2026 World Cup semifinals?",
        "baseline_probability": 28.0,
        "category": "team_progression",
        "entities": [_TOURNAMENT, "CONMEBOL", "semifinals"],
        "resolution_criteria": (
            "YES if two or more CONMEBOL-member nations qualify for semifinal "
            "matches at the 2026 FIFA World Cup."
        ),
        "time_horizon": "before the 2026 FIFA World Cup semifinals",
    },
)


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Return curated World Cup candidate events.

    The source is local and deterministic: no network dependency, no API key, no
    failure mode beyond being disabled. ``limit`` mirrors the other event-source
    adapters and bounds how many curated sports questions enter the discovery
    candidate pool.
    """
    if not settings.WORLD_CUP_SOURCE_ENABLED:
        return []
    if limit <= 0:
        return []
    return [_to_candidate_event(item) for item in _CANDIDATES[:limit]]


def _to_candidate_event(item: dict[str, Any]) -> dict[str, Any]:
    baseline = float(item["baseline_probability"])
    question = str(item["question"])
    return {
        "question": question,
        "baseline_probability": baseline,
        "source": {
            "type": "sports_event",
            "platform": settings.WORLD_CUP_SOURCE_NAME,
            "source_id": f"world-cup-2026:{item['id']}",
            "question": question,
            "baseline_probability": round(baseline, 2),
            "url": _TOURNAMENT_URL,
            "sport": "football",
            "tournament": _TOURNAMENT,
            "category": item["category"],
            "resolution_criteria": item["resolution_criteria"],
            "time_horizon": item["time_horizon"],
            "entities": item["entities"],
            "tags": ["sports", "football", "world_cup_2026"],
        },
    }
